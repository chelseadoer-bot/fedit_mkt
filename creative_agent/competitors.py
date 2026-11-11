"""경쟁사·참고 광고: competitors/<slug>/ 폴더의 스크린샷을 우리 소재와 같은 기준으로 라벨링해 비교한다.

메타 광고 라이브러리 API는 한국 일반 광고를 제공하지 않고, 웹 수집은 약관 위반이라
사람이 저장한 스크린샷 + 광고 라이브러리 바로가기 링크 방식으로 운영한다.
"""
import base64
import hashlib
import io
import json
import random
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import quote

from PIL import Image

from . import config, db

DIR = config.ROOT / "competitors"
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp"}
OFFERS = ["없음", "할인/쿠폰", "보상/경품", "무료 체험", "기능 데모/샘플"]

SYSTEM = f"""당신은 SaaS 광고를 분석하는 크리에이티브 전략가입니다.
다른 회사의 광고 스크린샷 한 장을 보고, 우리 회사 소재와 같은 기준으로 분류합니다.
스크린샷에는 인스타그램/페이스북 화면 요소(프로필, 좋아요, 팔로워 등)가 섞여 있을 수 있으니 광고 크리에이티브 자체만 봅니다.

판단 기준:
{config.LABEL_GUIDE}
- 오퍼: 결제·가입 시 혜택. 할인·쿠폰 → '할인/쿠폰', 상품권·포인트·경품 → '보상/경품', 무료 체험 → '무료 체험', 실제 화면·결과물 미리보기 위주 → '기능 데모/샘플', 없으면 '없음'.
- headline: 광고의 가장 큰 카피를 그대로 옮겨 적습니다.
- cta: 행동 유도 문구(버튼 등). 없으면 빈 문자열.
- target: 이 광고가 노리는 사람을 짧게(예: '대학생', '연구자', '기업 실무자').
- description: 이 광고의 전략을 한국어 한 문장으로."""


def _key(h: str) -> str:
    return f"comp:{config.PROFILE}:{h}"


def _schema() -> dict:
    props = {dim: {"type": "string", "enum": vals} for dim, vals in config.DIMENSIONS.items()}
    props["오퍼"] = {"type": "string", "enum": OFFERS}
    for k in ("headline", "cta", "target", "description"):
        props[k] = {"type": "string"}
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


def load_brands() -> list[dict]:
    path = DIR / "brands.json"
    brands = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    known = {b["slug"] for b in brands}
    if DIR.exists():  # brands.json에 없는 폴더도 회사로 취급
        for d in sorted(p for p in DIR.iterdir() if p.is_dir() and p.name not in known):
            brands.append({"slug": d.name, "name": d.name, "keyword": d.name})
    for b in brands:
        kw = b.get("keyword") or b["name"]
        b["adlib_url"] = ("https://www.facebook.com/ads/library/?active_status=active&ad_type=all&country=KR"
                          f"&media_type=all&search_type=keyword_unordered&q={quote(kw)}")
        if b.get("instagram"):
            b["ig_url"] = f"https://www.instagram.com/{b['instagram'].lstrip('@')}/"
    return brands


def _files(brands: list[dict]) -> list[dict]:
    out = []
    for b in brands:
        folder = DIR / b["slug"]
        if not folder.is_dir():
            continue
        for f in sorted(folder.iterdir()):
            if f.suffix.lower() not in IMAGE_EXT:
                continue
            data = f.read_bytes()
            m = re.match(r"(\d{4}-\d{2}-\d{2})", f.name)
            out.append({"brand": b["slug"], "file": f.name, "path": str(f),
                        "hash": hashlib.sha1(data).hexdigest(), "date": m.group(1) if m else None})
    return out


def _label_one(client, path: str) -> dict:
    from .labeler import _encode
    media_type, data = _encode(path, max_side=1024)
    resp = client.beta.messages.create(
        model=config.CLAUDE_MODEL, max_tokens=4000, system=SYSTEM,
        betas=["server-side-fallback-2026-07-01"], fallbacks="default",
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": _schema()}},
        messages=[{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": data}},
            {"type": "text", "text": "이 광고를 분류해 주세요."}]}],
    )
    if resp.stop_reason == "refusal":
        raise RuntimeError("모델이 분류를 거절했습니다")
    return json.loads(next(b.text for b in resp.content if b.type == "text"))


def label_pending(con, workers: int = 3) -> int:
    import anthropic
    brands = load_brands()
    todo = [f for f in _files(brands)
            if not con.execute("SELECT 1 FROM labels WHERE image_hash=?", (_key(f["hash"]),)).fetchone()]
    if not todo:
        return 0
    client = anthropic.Anthropic()
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_label_one, client, f["path"]): f for f in todo}
        for fut in as_completed(futs):
            f = futs[fut]
            try:
                out = fut.result()
            except (anthropic.APIStatusError, anthropic.APIConnectionError, RuntimeError, json.JSONDecodeError) as e:
                print(f"  ⚠️ 경쟁사 광고 라벨링 실패 {f['brand']}/{f['file']}: {e}")
                continue
            con.execute("INSERT OR REPLACE INTO labels(image_hash, labels, description, model) VALUES(?,?,?,?)",
                        (_key(f["hash"]), json.dumps(out, ensure_ascii=False), out.get("description", ""),
                         config.CLAUDE_MODEL))
            done += 1
    con.commit()
    return done


def _thumb(path: str, size: int = 360) -> str | None:
    try:
        im = Image.open(path).convert("RGB")
    except OSError:
        return None
    im.thumbnail((size, size))
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=80)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def build(con, fake: bool = False) -> dict | None:
    brands = load_brands()
    if not brands:
        return None
    ads, thumbs = [], {}
    rnd = random.Random(3)
    for f in _files(brands):
        row = con.execute("SELECT labels FROM labels WHERE image_hash=?", (_key(f["hash"]),)).fetchone()
        lab = json.loads(row["labels"]) if row else None
        if lab is None and fake:  # 데모: API 없이 가짜 라벨
            lab = {d: rnd.choice(v) for d, v in config.DIMENSIONS.items()}
            lab.update({"오퍼": rnd.choice(OFFERS), "headline": f"{f['brand']} 데모 헤드라인",
                        "cta": "지금 시작하기", "target": "데모", "description": "데모 라벨"})
        t = _thumb(f["path"])
        if t:
            thumbs["c_" + f["hash"]] = t
        info = {k: (lab or {}).pop(k, "") for k in ("headline", "cta", "target", "description")}
        ads.append({**{k: f[k] for k in ("brand", "file", "date")}, "thumb": "c_" + f["hash"] if t else None,
                    "labels": lab, **info})
    for b in brands:
        b["count"] = sum(1 for a in ads if a["brand"] == b["slug"])
    return {"brands": brands, "ads": ads, "thumbs": thumbs,
            "dimensions": {**config.DIMENSIONS, "오퍼": OFFERS}}
