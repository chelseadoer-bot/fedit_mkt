"""콘텐츠 캘린더: content/calendar.json(대시보드에서 편집)의 기획 항목에 실제 성과를 붙인다.

- 광고     : 메타 광고 성과(이미 수집) — 항목의 link에 광고 이름 또는 광고 ID
- 인스타그램: Instagram Graph API — 게시물별 도달·좋아요·댓글·저장·공유 (META_ACCESS_TOKEN에 인스타 권한 필요)
- 스레드   : Threads API — 게시물별 조회·좋아요·답글·리포스트 (THREADS_ACCESS_TOKEN, 60일 토큰 자동 갱신)
link가 없으면 같은 날짜·같은 채널 게시물이 하나뿐일 때 자동으로 잇는다. 기획에 없는 게시물도 캘린더에 함께 보여준다.
"""
import json
import os
import re
from datetime import datetime, timedelta, timezone

import requests

from . import config

KST = timezone(timedelta(hours=9))
CAL_FILE = config.ROOT / "content" / "calendar.json"
GRAPH = f"https://graph.facebook.com/{config.META_API_VERSION}"
THREADS = "https://graph.threads.net/v1.0"


def load_items() -> list[dict]:
    if not CAL_FILE.exists():
        return []
    try:
        return json.loads(CAL_FILE.read_text(encoding="utf-8")).get("items", [])
    except ValueError:
        return []


def _kst_date(ts: str) -> str:
    # 인스타·스레드 timestamp: 2026-11-10T03:12:45+0000
    ts = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", ts)
    return datetime.fromisoformat(ts).astimezone(KST).strftime("%Y-%m-%d")


def _code(url: str | None) -> str | None:
    """게시물 URL에서 비교용 코드(/p/ABC/, /reel/ABC/, /post/ABC) 추출."""
    if not url:
        return None
    m = re.search(r"/(?:p|reel|tv|post)/([A-Za-z0-9_-]+)", url)
    return m.group(1) if m else url.strip().rstrip("/").lower()


# ───────────── 인스타그램 ─────────────
def instagram_posts(days: int = 120) -> tuple[list[dict], str]:
    token = os.getenv("INSTAGRAM_ACCESS_TOKEN") or config.META_ACCESS_TOKEN
    if not token:
        return [], "토큰 없음"
    try:
        ig_id = os.getenv("INSTAGRAM_USER_ID")
        if not ig_id:
            r = requests.get(f"{GRAPH}/me/accounts", params={
                "fields": "name,instagram_business_account{id,username}", "access_token": token}, timeout=60)
            if not r.ok:
                return [], f"인스타 계정 조회 실패: {r.json().get('error', {}).get('message', r.text[:120])}"
            for page in r.json().get("data", []):
                if page.get("instagram_business_account"):
                    ig_id = page["instagram_business_account"]["id"]
                    break
        if not ig_id:
            return [], "연결된 인스타그램 비즈니스 계정을 찾지 못함 (시스템 사용자에 페이지·인스타 계정 할당 및 권한 필요)"
        since = datetime.now(timezone.utc) - timedelta(days=days)
        url = f"{GRAPH}/{ig_id}/media"
        params = {"fields": "id,caption,media_type,media_product_type,permalink,timestamp,like_count,"
                            "comments_count,media_url,thumbnail_url", "limit": 50, "access_token": token}
        posts = []
        while url:
            r = requests.get(url, params=params, timeout=60)
            if not r.ok:
                return posts, f"인스타 게시물 조회 실패: {r.json().get('error', {}).get('message', r.text[:120])}"
            d = r.json()
            stop = False
            for m in d.get("data", []):
                ts = datetime.fromisoformat(re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", m["timestamp"]))
                if ts < since:
                    stop = True
                    break
                posts.append(m)
            url = None if stop else d.get("paging", {}).get("next")
            params = None
        out = []
        for m in posts:
            metrics = {"likes": m.get("like_count"), "comments": m.get("comments_count")}
            for metric_set in ("reach,saved,shares,views", "reach,saved"):
                r = requests.get(f"{GRAPH}/{m['id']}/insights", params={"metric": metric_set, "access_token": token},
                                 timeout=60)
                if r.ok:
                    for x in r.json().get("data", []):
                        v = (x.get("values") or [{}])[0].get("value", (x.get("total_value") or {}).get("value"))
                        metrics[x["name"]] = v
                    break
            out.append({"channel": "instagram", "id": m["id"], "date": _kst_date(m["timestamp"]),
                        "permalink": m.get("permalink"), "code": _code(m.get("permalink")),
                        "text": (m.get("caption") or "")[:120], "type": m.get("media_product_type") or m.get("media_type"),
                        "thumb": m.get("thumbnail_url") or m.get("media_url"), "metrics": metrics})
        return out, "ok"
    except (requests.RequestException, ValueError, KeyError) as e:
        return [], f"인스타 조회 오류: {str(e)[:120]}"


# ───────────── 스레드 ─────────────
def _threads_token() -> str | None:
    """THREADS_ACCESS_TOKEN(60일)을 쓰되, 7일마다 갱신해 data/threads_token.json에 보관."""
    base = os.getenv("THREADS_ACCESS_TOKEN")
    f = config.DATA_DIR / "threads_token.json"
    saved = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
    if base and saved.get("source") != base[-12:]:  # 새 토큰을 넣었으면 그걸 기준으로
        saved = {"token": base, "source": base[-12:], "refreshed_at": "2000-01-01T00:00:00+00:00"}
    token = saved.get("token") or base
    if not token:
        return None
    last = datetime.fromisoformat(saved.get("refreshed_at", "2000-01-01T00:00:00+00:00"))
    if datetime.now(timezone.utc) - last > timedelta(days=7):
        try:
            r = requests.get("https://graph.threads.net/refresh_access_token",
                             params={"grant_type": "th_refresh_token", "access_token": token}, timeout=60)
            if r.ok and r.json().get("access_token"):
                token = r.json()["access_token"]
                saved.update(token=token, refreshed_at=datetime.now(timezone.utc).isoformat())
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_text(json.dumps(saved), encoding="utf-8")
                print("   스레드 토큰 갱신 완료(60일 연장)")
            else:
                print(f"   ⚠️ 스레드 토큰 갱신 실패: {r.text[:120]}")
        except requests.RequestException as e:
            print(f"   ⚠️ 스레드 토큰 갱신 오류: {e}")
    return token


def threads_posts(days: int = 120) -> tuple[list[dict], str]:
    token = _threads_token()
    if not token:
        return [], "토큰 없음"
    try:
        since = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp())
        url = f"{THREADS}/me/threads"
        params = {"fields": "id,text,permalink,timestamp,media_type,media_url,thumbnail_url",
                  "since": since, "limit": 50, "access_token": token}
        posts = []
        while url:
            r = requests.get(url, params=params, timeout=60)
            if not r.ok:
                return [], f"스레드 게시물 조회 실패: {r.text[:150]}"
            d = r.json()
            posts += d.get("data", [])
            url = d.get("paging", {}).get("next")
            params = None
        out = []
        for m in posts:
            metrics = {}
            r = requests.get(f"{THREADS}/{m['id']}/insights",
                             params={"metric": "views,likes,replies,reposts,quotes,shares", "access_token": token},
                             timeout=60)
            if r.ok:
                for x in r.json().get("data", []):
                    metrics[x["name"]] = (x.get("values") or [{}])[0].get("value", (x.get("total_value") or {}).get("value"))
            out.append({"channel": "threads", "id": m["id"], "date": _kst_date(m["timestamp"]),
                        "permalink": m.get("permalink"), "code": _code(m.get("permalink")),
                        "text": (m.get("text") or "")[:120], "type": m.get("media_type"),
                        "thumb": m.get("thumbnail_url") or m.get("media_url"), "metrics": metrics})
        return out, "ok"
    except (requests.RequestException, ValueError, KeyError) as e:
        return [], f"스레드 조회 오류: {str(e)[:120]}"


# ───────────── 기획 ↔ 성과 연결 ─────────────
def _ad_perf(ads: list[dict], key: str) -> dict | None:
    key = (key or "").strip()
    if not key:
        return None
    hits = [a for a in ads if a["ad_id"] == key or a["name"] == key]
    if not hits:
        return None
    s = lambda k: sum(a.get(k) or 0 for a in hits)
    spend, conv = s("spend"), s("conversions")
    out = {"spend": spend, "impressions": s("impressions"), "clicks": s("clicks"), "conversions": conv,
           "cpa": spend / conv if conv else 0, "ads": len(hits)}
    if "signups" in hits[0]:
        out.update(signups=s("signups"), trials=s("trials"), activated=s("activated"))
    return out


def build(default_view: dict, live: bool = True) -> dict:
    items = load_items()
    status = {}
    posts = []
    if live:
        for fn, ch in ((instagram_posts, "instagram"), (threads_posts, "threads")):
            p, st = fn()
            posts += p
            status[ch] = st
            print(f"   {ch}: 게시물 {len(p)}개 ({st})")
    perf, matched = {}, set()
    for it in items:
        ch, link = it.get("channel"), it.get("link") or ""
        if ch == "ad":
            p = _ad_perf(default_view.get("ads", []), link or it.get("title", ""))
            if p:
                perf[it["id"]] = {"kind": "ad", **p}
            continue
        if ch not in ("instagram", "threads"):
            continue
        cands = [x for x in posts if x["channel"] == ch]
        hit = None
        if link:
            code = _code(link)
            hit = next((x for x in cands if x["code"] == code or x["permalink"] == link), None)
        else:  # 링크가 없으면 같은 날 그 채널 게시물이 딱 하나일 때 자동 연결
            same = [x for x in cands if x["date"] == it.get("date") and x["id"] not in matched]
            hit = same[0] if len(same) == 1 else None
        if hit:
            matched.add(hit["id"])
            perf[it["id"]] = {"kind": ch, "post_id": hit["id"], "permalink": hit["permalink"], "auto": not link,
                              **hit["metrics"]}
    unplanned = [x for x in posts if x["id"] not in matched]
    return {"items": items, "perf": perf, "posts": unplanned, "status": status,
            "period": default_view.get("period")}
