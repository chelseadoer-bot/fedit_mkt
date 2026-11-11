"""Meta Marketing API 수집기: 광고 단위 일별 인사이트 + 소재 이미지."""
import hashlib
import json
import time
from datetime import date

import requests

from . import config, db

GRAPH = "https://graph.facebook.com"
RETRY_CODES = {1, 2, 4, 17, 32, 341, 613, 80000, 80004}  # 레이트 리밋·일시 오류 계열
PERMANENT_CODES = {10, 100, 190, 200, 803}  # 권한·잘못된 요청 — 재시도해도 안 됨


class MetaAPIError(RuntimeError):
    pass


def _get(url: str, params: dict | None = None) -> dict:
    """GET + 재시도. 레이트 리밋/일시 오류만 재시도하고, 잘못된 요청(100 등)은 바로 실패."""
    for attempt in range(5):
        r = requests.get(url, params=params, timeout=60)
        if r.ok:
            return r.json()
        try:
            err = r.json().get("error", {})
        except ValueError:
            err = {}
        code, msg = err.get("code"), err.get("message", r.text[:300])
        transient = code in RETRY_CODES or err.get("is_transient") or (r.status_code >= 500 and code not in PERMANENT_CODES)
        if transient and attempt < 4:
            wait = 2 ** attempt * 5
            print(f"  ⏳ Meta API 일시 오류({code or r.status_code}: {msg[:80]}), {wait}s 후 재시도")
            time.sleep(wait)
            continue
        raise MetaAPIError(f"{code or r.status_code}: {msg}")
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


CREATIVE_FIELDS = "creative{id,image_url,thumbnail_url,object_story_spec}"


def _creative_image_url(creative: dict) -> str | None:
    """대표 이미지 URL: 단일 이미지 > 링크 이미지 > 영상 썸네일. 없으면 None(→ 큰 썸네일을 따로 요청)."""
    if creative.get("image_url"):
        return creative["image_url"]
    spec = creative.get("object_story_spec") or {}
    link = spec.get("link_data") or {}
    if link.get("picture"):
        return link["picture"]
    video = spec.get("video_data") or {}
    if video.get("image_url"):
        return video["image_url"]
    return None


def _big_thumbnail(creative: dict) -> str | None:
    """캐러셀·다이내믹 소재 등은 메타가 만든 썸네일을 600px로 요청."""
    if not creative.get("id"):
        return creative.get("thumbnail_url")
    try:
        data = _get(f"{GRAPH}/{config.META_API_VERSION}/{creative['id']}", {
            "fields": "thumbnail_url", "thumbnail_width": 600, "thumbnail_height": 600,
            "access_token": config.META_ACCESS_TOKEN,
        })
        return data.get("thumbnail_url") or creative.get("thumbnail_url")
    except MetaAPIError:
        return creative.get("thumbnail_url")


def _lookup_ads(ad_ids: list[str]) -> dict:
    """광고 ID 여러 개를 한 번에 조회. 묶음이 실패하면(삭제된 광고 등) 하나씩 다시 조회하고 실패한 것만 건너뜀."""
    url = f"{GRAPH}/{config.META_API_VERSION}/"
    try:
        return _get(url, {"ids": ",".join(ad_ids), "fields": CREATIVE_FIELDS,
                          "access_token": config.META_ACCESS_TOKEN})
    except MetaAPIError as e:
        if len(ad_ids) == 1:
            print(f"  ⚠️ 광고 {ad_ids[0]} 소재 조회 실패 — 건너뜀 ({e})")
            return {}
        print(f"  ↻ 묶음 조회 실패({e}) — 하나씩 다시 조회합니다")
        out = {}
        for ad_id in ad_ids:
            out.update(_lookup_ads([ad_id]))
        return out


ALL_AD_STATUSES = ["ACTIVE", "PAUSED", "CAMPAIGN_PAUSED", "ADSET_PAUSED", "ARCHIVED", "DELETED",
                   "IN_PROCESS", "WITH_ISSUES", "PENDING_REVIEW", "DISAPPROVED", "PREAPPROVED"]


def _list_account_ads(wanted: set[str]) -> dict:
    """광고계정의 광고 목록에서 소재 정보를 가져온다(PC 첫 실행에서 동작이 확인된 방식).
    종료·보관된 광고까지 포함해 보고, 그 필터가 거절되면 기본 목록(진행·일시중지)으로 다시 시도."""
    base = {"fields": "id," + CREATIVE_FIELDS, "thumbnail_width": 600, "thumbnail_height": 600, "limit": 200}
    attempts = [
        {**base, "filtering": json.dumps([{"field": "ad.effective_status", "operator": "IN", "value": ALL_AD_STATUSES}])},
        base,
    ]
    for params in attempts:
        found = {}
        try:
            for ad in _paginate(f"{config.META_AD_ACCOUNT_ID}/ads", params):
                if ad["id"] in wanted:
                    found[ad["id"]] = ad
            return found
        except MetaAPIError as e:
            print(f"  ⚠️ 광고 목록 조회 실패({e})" + (" — 필터 없이 다시 시도" if params is not base else ""))
    return {}


def fetch_creatives(con) -> int:
    """이미지가 아직 없는 광고들의 소재 이미지를 내려받는다.
    1) 광고계정 광고 목록에서 찾고  2) 거기 없는 광고만 광고 ID로 개별 조회."""
    config.IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    pending = [r["ad_id"] for r in con.execute("SELECT ad_id FROM ads WHERE image_hash IS NULL")]
    if not pending:
        return 0
    ads = _list_account_ads(set(pending))
    print(f"   광고 목록에서 {len(ads)}/{len(pending)}개 찾음")
    rest = [a for a in pending if a not in ads]
    for i in range(0, len(rest), 50):
        ads.update(_lookup_ads(rest[i:i + 50]))

    n = 0
    for ad_id, ad in ads.items():
        creative = ad.get("creative") or {}
        img_url = _creative_image_url(creative) or creative.get("thumbnail_url") or _big_thumbnail(creative)
        if not img_url:
            print(f"  ⚠️ 광고 {ad_id}: 소재 이미지 주소 없음")
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
    missing = len(pending) - n
    if missing:
        print(f"   이미지를 못 구한 광고 {missing}개 — 다음 실행 때 다시 시도합니다")
    return n
