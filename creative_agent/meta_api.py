"""Meta Marketing API 수집기: 광고 단위 일별 인사이트 + 소재 이미지."""
import hashlib
import json
import time
from datetime import date

import requests

from . import config, db

GRAPH = "https://graph.facebook.com"
RETRY_CODES = {4, 17, 32, 613, 80000, 80004}  # 레이트 리밋 계열


class MetaAPIError(RuntimeError):
    pass


def _get(url: str, params: dict | None = None) -> dict:
    for attempt in range(6):
        r = requests.get(url, params=params, timeout=60)
        if r.ok:
            return r.json()
        err = r.json().get("error", {}) if r.headers.get("content-type", "").startswith("application/json") else {}
        if err.get("code") in RETRY_CODES or r.status_code >= 500:
            wait = 2 ** attempt * 5
            print(f"  ⏳ Meta API 제한/오류({err.get('code', r.status_code)}), {wait}s 후 재시도")
            time.sleep(wait)
            continue
        raise MetaAPIError(f"{r.status_code}: {err.get('message', r.text[:300])}")
    raise MetaAPIError("재시도 한도 초과")


def _paginate(path: str, params: dict):
    url = f"{GRAPH}/{config.META_API_VERSION}/{path}"
    params = {**params, "access_token": config.META_ACCESS_TOKEN}
    while url:
        data = _get(url, params)
        yield from data.get("data", [])
        url = data.get("paging", {}).get("next")
        params = None  # next URL에 파라미터가 이미 포함됨


def _actions_map(items: list | None) -> dict:
    return {a["action_type"]: float(a.get("value", 0)) for a in items or []}


def _action_value(items: list | None) -> float:
    if not items:
        return 0.0
    by_type = {a["action_type"]: float(a.get("value", 0)) for a in items}
    for t in config.META_PURCHASE_ACTIONS:  # 우선순위 순으로 하나만 사용(중복 집계 방지)
        if t in by_type:
            return by_type[t]
    return 0.0


def fetch_insights(con, since: date, until: date) -> int:
    n = 0
    for row in _paginate(
        f"{config.META_AD_ACCOUNT_ID}/insights",
        {
            "level": "ad",
            "time_increment": 1,
            "time_range": f'{{"since":"{since}","until":"{until}"}}',
            "fields": "ad_id,ad_name,adset_name,campaign_name,spend,impressions,inline_link_clicks,actions,action_values",
            "limit": 500,
        },
    ):
        db.upsert_ad(con, {
            "ad_id": row["ad_id"], "ad_name": row.get("ad_name"), "adset_name": row.get("adset_name"),
            "campaign_name": row.get("campaign_name"), "image_url": None, "image_hash": None, "image_path": None,
        })
        db.upsert_daily(con, {
            "ad_id": row["ad_id"], "date": row["date_start"],
            "spend": float(row.get("spend", 0)), "impressions": int(row.get("impressions", 0)),
            "clicks": int(row.get("inline_link_clicks", 0)),
            "purchases": _action_value(row.get("actions")),
            "revenue": _action_value(row.get("action_values")),
            "actions": json.dumps(_actions_map(row.get("actions"))),
        })
        n += 1
    con.commit()
    return n


def _creative_image_url(creative: dict) -> str | None:
    """대표 이미지 URL: 단일 이미지 > 링크 이미지 > 영상 썸네일 > 메타가 만든 썸네일(캐러셀은 첫 장)."""
    if creative.get("image_url"):
        return creative["image_url"]
    spec = creative.get("object_story_spec") or {}
    link = spec.get("link_data") or {}
    if link.get("picture"):
        return link["picture"]
    video = spec.get("video_data") or {}
    if video.get("image_url"):
        return video["image_url"]
    return creative.get("thumbnail_url")


def fetch_creatives(con) -> int:
    """이미지가 아직 없는 광고들의 소재 이미지를 광고 ID로 직접 조회해 내려받는다(종료·보관된 광고 포함)."""
    config.IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    pending = [r["ad_id"] for r in con.execute("SELECT ad_id FROM ads WHERE image_hash IS NULL")]
    n = 0
    url = f"{GRAPH}/{config.META_API_VERSION}/"
    for i in range(0, len(pending), 50):
        batch = pending[i:i + 50]
        data = _get(url, {
            "ids": ",".join(batch),
            "fields": "creative{image_url,thumbnail_url,object_story_spec}",
            "thumbnail_width": 600, "thumbnail_height": 600,
            "access_token": config.META_ACCESS_TOKEN,
        })
        for ad_id, ad in data.items():
            img_url = _creative_image_url(ad.get("creative") or {})
            if not img_url:
                continue
            try:
                img = requests.get(img_url, timeout=60)
                img.raise_for_status()
            except requests.RequestException as e:
                print(f"  ⚠️ 이미지 다운로드 실패 {ad_id}: {e}")
                continue
            h = hashlib.sha1(img.content).hexdigest()
            path = config.IMAGE_DIR / f"{h}.jpg"
            path.write_bytes(img.content)
            con.execute("UPDATE ads SET image_url=?, image_hash=?, image_path=? WHERE ad_id=?",
                        (img_url, h, str(path), ad_id))
            n += 1
    con.commit()
    return n
