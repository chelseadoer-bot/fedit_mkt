"""Claude 비전으로 소재 이미지를 라벨링. 이미지 해시 단위로 캐시하므로 같은 이미지는 한 번만 호출."""
import base64
import io
import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed

import anthropic
from PIL import Image

from . import config, db

SYSTEM = f"""당신은 메타 광고 소재를 분류하는 크리에이티브 분석가입니다.
광고 이미지 한 장(필요하면 광고 이름)을 보고 각 축에서 가장 알맞은 값 하나를 고릅니다.

판단 기준:
{config.LABEL_GUIDE}

description에는 소재를 한국어 한 문장(40자 내외)으로 요약합니다. 이미지에 보이는 핵심 카피가 있으면 따옴표로 넣습니다."""


def _schema() -> dict:
    props = {dim: {"type": "string", "enum": vals} for dim, vals in config.DIMENSIONS.items()}
    props["description"] = {"type": "string"}
    return {
        "type": "object",
        "properties": props,
        "required": list(props),
        "additionalProperties": False,
    }


def _encode(path: str, max_side: int = 768) -> tuple[str, str]:
    im = Image.open(path).convert("RGB")
    im.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=85)
    return "image/jpeg", base64.standard_b64encode(buf.getvalue()).decode()


def label_image(client: anthropic.Anthropic, path: str, ad_name: str = "") -> dict:
    media_type, data = _encode(path)
    resp = client.beta.messages.create(
        model=config.CLAUDE_MODEL,
        max_tokens=4000,
        system=SYSTEM,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",  # 안전 분류기 오탐 시 서버가 다른 모델로 자동 재시도
        output_config={
            "effort": os.getenv("LABEL_EFFORT", "low"),
            "format": {"type": "json_schema", "schema": _schema()},
        },
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": data}},
                {"type": "text", "text": f"광고 이름: {ad_name}\n이 광고 소재를 분류해 주세요." if ad_name
                 else "이 광고 소재를 분류해 주세요."},
            ],
        }],
    )
    if resp.stop_reason == "refusal":
        raise RuntimeError("모델이 분류를 거절했습니다")
    text = next(b.text for b in resp.content if b.type == "text")
    return json.loads(text)


def label_pending(con, workers: int = 4) -> int:
    rows = con.execute(
        """SELECT a.image_hash, MIN(a.image_path) AS image_path, MIN(a.ad_name) AS ad_name FROM ads a
           LEFT JOIN labels l ON l.image_hash = ? || ':' || a.image_hash
           WHERE a.image_hash IS NOT NULL AND l.image_hash IS NULL
           GROUP BY a.image_hash""", (config.PROFILE,)
    ).fetchall()
    if not rows:
        return 0
    client = anthropic.Anthropic()
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(label_image, client, r["image_path"], r["ad_name"] or ""): r["image_hash"] for r in rows}
        for f in as_completed(futs):
            h = futs[f]
            try:
                out = f.result()
            except (anthropic.APIStatusError, anthropic.APIConnectionError, RuntimeError, json.JSONDecodeError) as e:
                print(f"  ⚠️ 라벨링 실패 {h[:8]}: {e}")
                continue
            desc = out.pop("description", "")
            db.save_label(con, h, out, desc, config.CLAUDE_MODEL)
            done += 1
            print(f"  🏷  {done}/{len(rows)} {h[:8]} {out}")
    con.commit()
    return done
