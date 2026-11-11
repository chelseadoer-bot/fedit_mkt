"""Amplitude 연동: 가입·체험·활성화 사용자 수를 첫 유입 UTM별로 가져온다.

Dashboard REST API의 Event Segmentation(고유 사용자 수, 첫 유입 UTM 사용자 속성으로 그룹)을 쓴다.
- 광고 소재와 잇기: initial_utm_content (메타 URL 매개변수의 utm_content = 광고 ID 또는 광고 이름)
- 유입 채널:       initial_utm_source
활성화 = 무료체험 시작(trial_started) 후 체험 기간(기본 5일) 안에 브랜드 선택(brand_filter_changed)까지 한 사용자.
유료 전환 = 무료체험 시작 후 N일(기본 14일) 안에 결제 이벤트(AMPLITUDE_PAID_EVENT)를 한 사용자. 이벤트를 지정해야 켜짐.
  둘 다 Funnel API(순서 있음): 기간 안에 무료체험을 시작한 사람을 기준으로 센다.
"""
import json
import os

import requests

API_KEY = os.getenv("AMPLITUDE_API_KEY", "")
SECRET_KEY = os.getenv("AMPLITUDE_SECRET_KEY", "")
REGION = (os.getenv("AMPLITUDE_REGION") or "us").lower()
EVENTS = {
    "signups": os.getenv("AMPLITUDE_SIGNUP_EVENT") or "sign_up",
    "trials": os.getenv("AMPLITUDE_TRIAL_EVENT") or "trial_started",
    "activated": os.getenv("AMPLITUDE_ACTIVATION_EVENT") or "brand_filter_changed",
}
PAID_EVENT = (os.getenv("AMPLITUDE_PAID_EVENT") or "").strip()
if PAID_EVENT:
    EVENTS["paid"] = PAID_EVENT
LABELS = {"signups": "가입", "trials": "무료체험 신청", "activated": "체험 중 브랜드 선택", "paid": "유료 전환"}
TRIAL_DAYS = int(os.getenv("AMPLITUDE_TRIAL_DAYS") or os.getenv("AMPLITUDE_ACTIVATION_WINDOW_DAYS") or "5")
PAID_WINDOW_DAYS = int(os.getenv("AMPLITUDE_PAID_WINDOW_DAYS") or "14")
AD_PROPERTY = os.getenv("AMPLITUDE_AD_PROPERTY") or "gp:initial_utm_content"
SOURCE_PROPERTY = os.getenv("AMPLITUDE_SOURCE_PROPERTY") or "gp:initial_utm_source"
NONE_LABELS = {"", "(none)", "none", "null", "(not set)", "EMPTY"}


class AmplitudeError(RuntimeError):
    pass


def enabled() -> bool:
    return bool(API_KEY and SECRET_KEY)


def _base() -> str:
    return "https://analytics.eu.amplitude.com" if REGION == "eu" else "https://amplitude.com"


def segmentation(event: str, since: str, until: str, group_by: str | None = None) -> dict[str, float]:
    """{그룹값: 기간 내 고유 사용자 수}. 그룹 없으면 {'(all)': n}."""
    params = {
        "e": json.dumps({"event_type": event}),
        "start": since.replace("-", ""), "end": until.replace("-", ""),
        "m": "uniques", "i": 1,
    }
    if group_by:
        params.update(g=group_by, limit=1000)
    r = requests.get(f"{_base()}/api/2/events/segmentation", params=params,
                     auth=(API_KEY, SECRET_KEY), timeout=120)
    if not r.ok:
        raise AmplitudeError(f"{r.status_code}: {r.text[:200]}")
    data = r.json().get("data", {})
    labels = data.get("seriesLabels") or []
    collapsed = data.get("seriesCollapsed") or []
    out: dict[str, float] = {}
    for i, series in enumerate(data.get("series") or []):
        label = labels[i] if i < len(labels) else "(all)"
        if isinstance(label, list):  # 그룹 응답은 [인덱스, "값"] 형태
            label = label[-1]
        label = "(none)" if not group_by and label in (0, "0") else str(label)
        if i < len(collapsed) and collapsed[i]:
            total = collapsed[i][0].get("value", 0)
        else:
            total = sum(v for v in series if isinstance(v, (int, float)))
        out[label if group_by else "(all)"] = out.get(label if group_by else "(all)", 0) + total
    return out


def funnel(events: list[str], since: str, until: str, group_by: str | None = None, window_days: int = 5) -> dict[str, float]:
    """순서 있는 퍼널에서 마지막 단계까지 간 고유 사용자 수. {그룹값: n} / 그룹 없으면 {'(all)': n}."""
    params = [("e", json.dumps({"event_type": ev})) for ev in events]
    params += [("start", since.replace("-", "")), ("end", until.replace("-", "")),
               ("mode", "ordered"), ("n", "active"), ("cs", window_days * 86400)]
    if group_by:
        params += [("g", group_by), ("limit", 1000)]
    r = requests.get(f"{_base()}/api/2/funnels", params=params, auth=(API_KEY, SECRET_KEY), timeout=120)
    if not r.ok:
        raise AmplitudeError(f"funnel {r.status_code}: {r.text[:200]}")
    data = r.json().get("data", [])
    if isinstance(data, dict):
        data = [data]
    out: dict[str, float] = {}
    grouped_seen = False
    for d in data:
        raw = d.get("cumulativeRaw") or []
        if not raw:
            continue
        label = d.get("groupValue", d.get("group"))
        if isinstance(label, list):
            label = label[-1] if label else None
        if group_by:
            if label is None:  # 그룹 응답에 섞인 전체 합계 행
                continue
            grouped_seen = True
            key = str(label)
        else:
            key = "(all)"
        out[key] = out.get(key, 0) + raw[-1]
    if group_by and data and not grouped_seen:
        print(f"  ⚠️ Amplitude 퍼널 응답에서 그룹({group_by})을 찾지 못했습니다 — 응답 키: {list(data[0])[:12]}")
    return out


def fetch(since: str, until: str) -> dict | None:
    """대시보드용 묶음. 실패하면 경고만 남기고 None."""
    if not enabled():
        return None
    try:
        totals, by_ad, by_source = {}, {}, {}
        act_steps = [EVENTS["trials"], EVENTS["activated"]]
        for key, event in EVENTS.items():
            if key == "activated":  # 무료체험 → 체험 기간 안에 브랜드 선택
                q = lambda g=None: funnel(act_steps, since, until, g, TRIAL_DAYS)
            elif key == "paid":  # 무료체험 → N일 안에 결제
                q = lambda g=None: funnel([EVENTS["trials"], PAID_EVENT], since, until, g, PAID_WINDOW_DAYS)
            else:
                q = lambda g=None, ev=event: segmentation(ev, since, until, g)
            totals[key] = sum(q().values())
            for prop, bucket in ((AD_PROPERTY, by_ad), (SOURCE_PROPERTY, by_source)):
                for value, n in q(prop).items():
                    v = "(none)" if value in NONE_LABELS else value
                    bucket.setdefault(v, {k: 0 for k in EVENTS})[key] += n
        return {"events": EVENTS, "labels": {k: LABELS[k] for k in EVENTS}, "activation_steps": act_steps,
                "trial_days": TRIAL_DAYS, "paid_window_days": PAID_WINDOW_DAYS if PAID_EVENT else None,
                "since": since, "until": until,
                "totals": totals, "by_ad": by_ad, "by_source": by_source,
                "ad_property": AD_PROPERTY, "source_property": SOURCE_PROPERTY}
    except (AmplitudeError, requests.RequestException, ValueError) as e:
        print(f"  ⚠️ Amplitude 조회 실패 — 가입/활성화 없이 진행합니다 ({e})")
        return None


# 메타 광고에서 시작했지만 utm_source가 다른 경로 (예: 모바일 광고 → 'PC로 링크 받기' 메일 → PC 가입)
META_SOURCE_ALIASES = {x.strip().lower() for x in (os.getenv("AMPLITUDE_META_SOURCES") or "lead_mail").split(",") if x.strip()}
META_SOURCE_NAMES = {"meta", "facebook", "fb", "instagram", "ig", "facebook_ads", "meta_ads", "instagram_ads"}


def is_meta(source: str) -> bool:
    s = source.strip().lower()
    return (s in META_SOURCE_NAMES or s in META_SOURCE_ALIASES
            or s.startswith(("meta", "facebook", "instagram", "fb_", "ig_")))


def attach(report: dict, amp: dict | None) -> dict:
    """리포트의 소재·라벨 그룹에 가입/체험/활성화 수를 붙인다. utm_content = 광고 ID 또는 광고 이름으로 매칭."""
    report["amplitude"] = None
    if not amp:
        return report
    by_ad = amp["by_ad"]
    keys = list(amp["events"])  # 이 결과에 실제로 들어 있는 단계(유료 전환은 설정했을 때만)
    by_name = {}
    for a in report["ads"]:
        by_name.setdefault(a["name"], []).append(a)
    matched_keys = set()
    zero = {k: 0 for k in keys}
    for a in report["ads"]:
        a.update(zero)
    for key, counts in by_ad.items():
        targets = [a for a in report["ads"] if a["ad_id"] == key] or by_name.get(key, [])
        if not targets:
            continue
        matched_keys.add(key)
        for k in keys:  # 같은 이름 광고가 여럿이면 광고비 비중으로 나눔
            spend = sum(t["spend"] for t in targets) or len(targets)
            for t in targets:
                t[k] += counts.get(k, 0) * ((t["spend"] or 1) / spend)
    for a in report["ads"]:
        a["cps"] = a["spend"] / a["signups"] if a["signups"] else 0  # 가입당 비용
        a["act_rate"] = a["activated"] / a["trials"] if a["trials"] else 0  # 체험 → 브랜드 선택 전환율
    for groups in report["dimensions"].values():
        for g in groups:
            members = [a for a in report["ads"] if a["labels"] and
                       any(a["labels"].get(d) == g["value"] for d in report["dimensions"] if g in report["dimensions"][d])]
            for k in keys:
                g[k] = sum(a[k] for a in members)
            g["cps"] = g["spend"] / g["signups"] if g["signups"] else 0
            g["act_rate"] = g["activated"] / g["trials"] if g["trials"] else 0
    for tot_key, pool in (("labeled_totals", [a for a in report["ads"] if a["labels"]]),
                          ("analyzed_totals", report["ads"])):
        t = report.get(tot_key)
        if t is not None:
            for k in keys:
                t[k] = sum(a[k] for a in pool)
            t["cps"] = t["spend"] / t["signups"] if t["signups"] else 0
    meta = {k: sum(v[k] for s, v in amp["by_source"].items() if is_meta(s)) for k in keys}
    unmatched = {k: sum(v[k] for key, v in by_ad.items() if key not in matched_keys and key != "(none)") for k in keys}
    unmatched_values = sorted(
        ({"value": key, **v} for key, v in by_ad.items() if key not in matched_keys and key != "(none)" and v["signups"]),
        key=lambda x: -x["signups"])[:15]
    report["amplitude"] = {
        **amp, "meta": meta, "matched_ads": len(matched_keys), "unmatched": unmatched,
        "unmatched_values": unmatched_values,
        "meta_aliases": sorted(META_SOURCE_ALIASES),
        "sources": sorted(({"source": s, "is_meta": is_meta(s), "alias": s.strip().lower() in META_SOURCE_ALIASES, **v}
                           for s, v in amp["by_source"].items()),
                          key=lambda x: -x["signups"]),
    }
    return report


def debug(since: str, until: str) -> None:
    """활성화 수치 검증용: 퍼널 원본 응답(단계별 인원·전환 시간)과 비교 퍼널을 출력. 사용자 정보는 출력하지 않음."""
    def raw_funnel(events, window_days):
        params = [("e", json.dumps({"event_type": ev})) for ev in events]
        params += [("start", since.replace("-", "")), ("end", until.replace("-", "")),
                   ("mode", "ordered"), ("n", "active"), ("cs", window_days * 86400)]
        r = requests.get(f"{_base()}/api/2/funnels", params=params, auth=(API_KEY, SECRET_KEY), timeout=120)
        if not r.ok:
            return {"error": f"{r.status_code}: {r.text[:200]}"}
        data = r.json().get("data", [])
        d = data[0] if isinstance(data, list) and data else data
        return {k: d.get(k) for k in ("events", "cumulativeRaw", "stepByStep", "medianTransTimes", "avgTransTimes")
                if isinstance(d, dict)} | {"keys": sorted(d)[:20] if isinstance(d, dict) else str(type(d))}

    def fmt_ms(v):
        if not isinstance(v, (int, float)):
            return str(v)
        s = v / 1000
        return f"{s:.1f}초" if s < 120 else f"{s/60:.1f}분" if s < 7200 else f"{s/3600:.1f}시간"

    print(f"== Amplitude 활성화 검증 {since} ~ {until} ==")
    for ev in ("sign_up", "trial_started", "brand_filter_changed", "folder_save", "excel_download"):
        try:
            print(f"  [고유 사용자] {ev}: {sum(segmentation(ev, since, until).values()):.0f}명")
        except AmplitudeError as e:
            print(f"  [고유 사용자] {ev}: 오류 {e}")
    checks = [(["trial_started", "brand_filter_changed"], TRIAL_DAYS),
              (["trial_started", "brand_filter_changed"], 1),
              (["brand_filter_changed", "trial_started"], 1),
              (["trial_started", "folder_save"], TRIAL_DAYS),
              (["trial_started", "excel_download"], TRIAL_DAYS),
              (["trial_started", "sign_up"], TRIAL_DAYS)]
    for evs, w in checks:
        d = raw_funnel(evs, w)
        if "error" in d:
            print(f"  [퍼널] {' → '.join(evs)} ({w}일): 오류 {d['error']}")
            continue
        med = d.get("medianTransTimes") or []
        avg = d.get("avgTransTimes") or []
        print(f"  [퍼널] {' → '.join(evs)} (전환 기간 {w}일): 단계별 인원 {d.get('cumulativeRaw')} · "
              f"전환 시간 중앙값 {[fmt_ms(x) for x in med]} · 평균 {[fmt_ms(x) for x in avg]}")
    print(f"  응답 키: {d.get('keys')}")


# ───────────────────────── 계정별 활동 (Export API) ─────────────────────────
import gzip
import io
import zipfile
from collections import Counter, defaultdict
from datetime import date as _date, datetime as _dt, timedelta as _td, timezone as _tz

KST = _tz(_td(hours=9))
SHOW_FULL_ACCOUNT = (os.getenv("SHOW_FULL_ACCOUNT") or "1").lower() not in ("0", "false", "no")  # 기본: 전체 이메일 표시


def _exclude_rules() -> list[str]:
    from . import config
    rules = []
    f = config.ROOT / "exclude_accounts.txt"
    if f.exists():
        rules += [l.strip() for l in f.read_text(encoding="utf-8").splitlines() if l.strip() and not l.startswith("#")]
    rules += [x.strip() for x in (os.getenv("EXCLUDE_ACCOUNTS") or "").split(",") if x.strip()]
    return [r.lower() for r in rules]


def _excluded(uid: str, rules: list[str]) -> bool:
    u = uid.lower()
    for r in rules:
        if "***" in r:  # 가려진 형태: 앞글자***@도메인
            pre, post = r.split("***", 1)
            if u.startswith(pre) and u.endswith(post):
                return True
        elif r.startswith("@"):
            if u.endswith(r):
                return True
        elif u == r:
            return True
    return False
# 요약에 쓰는 행동 (이벤트 → 한글 이름). 자동 수집 이벤트는 '페이지 조회'만 셈
ACTION_LABELS = {
    "product_detail_viewed": "상품 상세", "products_loaded": "상품 목록 조회", "folder_save": "폴더 저장",
    "excel_download": "엑셀 완성", "brand_filter_changed": "브랜드 필터", "feature_viewed": "기능 조회",
    "fedi_chat_sent": "페디 채팅", "Page Viewed": "페이지 조회", "[Amplitude] Page Viewed": "페이지 조회",
}


def _export_range(start: str, end: str) -> list[dict]:
    """Export API 한 번 호출(start/end = YYYYMMDDTHH, UTC). 데이터 없으면 []."""
    content, status = None, None
    for attempt in range(2):
        try:
            with requests.get(f"{_base()}/api/2/export", params={"start": start, "end": end},
                              auth=(API_KEY, SECRET_KEY), timeout=(30, 240), stream=True) as r:
                status = r.status_code
                if r.ok:
                    content = b"".join(r.iter_content(1 << 20))
                elif status != 404:
                    raise AmplitudeError(f"export {status}: {r.text[:200]}")
            break
        except requests.RequestException:
            if attempt == 1:
                raise
    if status == 404:
        return []
    events = []
    with zipfile.ZipFile(io.BytesIO(content)) as z:
        for name in z.namelist():
            if name.endswith(".gz"):
                for line in gzip.decompress(z.read(name)).decode("utf-8").splitlines():
                    if line.strip():
                        events.append(json.loads(line))
    keep = ("event_type", "event_time", "user_id", "amplitude_id", "user_properties", "event_properties")
    return [{k: e.get(k) for k in keep} for e in events]


def _export_day(day: _date, cache_dir) -> list[dict]:
    """하루치(UTC) 원본 이벤트. 이틀 이상 지난 날은 data/amp_export/에 캐시.
    최근 이틀은 6시간씩 나눠 받아 시간 초과를 피하고, 일부 조각이 실패해도 받은 만큼 돌려준다."""
    cache = cache_dir / f"{day:%Y%m%d}.jsonl.gz"
    fresh = (_dt.now(_tz.utc).date() - day).days < 2
    if cache.exists() and not fresh:
        with gzip.open(cache, "rt", encoding="utf-8") as f:
            return [json.loads(l) for l in f if l.strip()]
    if fresh:
        slim, failed = [], 0
        for h0, h1 in ((0, 5), (6, 11), (12, 17), (18, 23)):
            try:
                slim += _export_range(f"{day:%Y%m%d}T{h0:02d}", f"{day:%Y%m%d}T{h1:02d}")
            except (AmplitudeError, requests.RequestException, zipfile.BadZipFile) as e:
                failed += 1
                print(f"  ⚠️ {day} {h0:02d}~{h1:02d}시(UTC) 받기 실패 — 건너뜀 ({str(e)[:80]})")
        if failed == 4:
            raise AmplitudeError("4개 조각 모두 실패")
        return slim  # 최근 날짜는 계속 바뀌므로 캐시하지 않음
    slim = _export_range(f"{day:%Y%m%d}T00", f"{day:%Y%m%d}T23")
    cache_dir.mkdir(parents=True, exist_ok=True)
    with gzip.open(cache, "wt", encoding="utf-8") as f:
        for e in slim:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    return slim


BRAND_USER_PROP = os.getenv("AMPLITUDE_BRAND_USER_PROP") or "monitored_brands"
BRAND_EVENT_PROP = os.getenv("AMPLITUDE_BRAND_EVENT_PROP") or "brand_name"


def _as_list(v) -> list[str]:
    """브랜드 값이 리스트·JSON 문자열·쉼표 문자열 어느 형태든 리스트로."""
    if v is None or v == "":
        return []
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    s = str(v).strip()
    if s.startswith("["):
        try:
            return [str(x).strip() for x in json.loads(s) if str(x).strip()]
        except ValueError:
            pass
    return [x.strip() for x in s.split(",") if x.strip()]


def _mask(uid: str) -> str:
    if SHOW_FULL_ACCOUNT or not uid:
        return uid or "(익명)"
    if "@" in uid:
        name, dom = uid.split("@", 1)
        return f"{name[:2]}***@{dom}"
    return uid[:4] + "…" if len(uid) > 6 else uid[:2] + "…"


def _parse_time(s: str) -> _dt:
    return _dt.fromisoformat(s.replace(" ", "T")[:26]).replace(tzinfo=_tz.utc).astimezone(KST)


def accounts(days: int = 30) -> dict | None:
    """가입 계정(user_id가 있는 사용자)별 최근 활동 한 줄 요약."""
    if not enabled():
        return None
    from . import config
    cache_dir = config.DATA_DIR / "amp_export"
    today_utc = _dt.now(_tz.utc).date()
    import time as _time
    from concurrent.futures import ThreadPoolExecutor
    budget = float(os.getenv("AMPLITUDE_EXPORT_BUDGET_SEC") or "720")  # 내려받기에 쓸 최대 시간
    started = _time.monotonic()
    days_list = [today_utc - _td(days=i) for i in range(days + 1)]  # 최근 날짜부터

    def fetch_one(day):
        cached = (cache_dir / f"{day:%Y%m%d}.jsonl.gz").exists() and (today_utc - day).days >= 2
        if not cached and _time.monotonic() - started > budget:
            return day, None, "시간 한도"
        try:
            return day, _export_day(day, cache_dir), None
        except (AmplitudeError, requests.RequestException, zipfile.BadZipFile, ValueError) as e:
            return day, None, str(e)[:120]

    events, missing = [], []
    with ThreadPoolExecutor(max_workers=3) as ex:
        for day, evs, err in ex.map(fetch_one, days_list):
            if evs is None:
                missing.append(day)
                if err != "시간 한도":
                    print(f"  ⚠️ {day} 원본 이벤트 받기 실패 — 그날만 건너뜀 ({err})")
            else:
                events += evs
    if missing:
        print(f"   못 받은 날 {len(missing)}일 — 다음 실행 때 다시 시도합니다")
    if not events and missing:
        return None

    now = _dt.now(KST)
    today, yesterday = now.date(), now.date() - _td(days=1)
    by_user: dict[str, list] = defaultdict(list)
    props: dict[str, dict] = {}
    rules, excluded = _exclude_rules(), set()
    for e in events:
        uid = e.get("user_id")
        if not uid:
            continue
        if _excluded(str(uid), rules):
            excluded.add(str(uid))
            continue
        by_user[str(uid)].append(e)
        if e.get("user_properties"):
            props[str(uid)] = {**props.get(str(uid), {}), **e["user_properties"]}

    rows = []
    for uid, evs in by_user.items():
        evs.sort(key=lambda e: e["event_time"])
        times = [_parse_time(e["event_time"]) for e in evs]
        types = [e["event_type"] for e in evs]
        first_of = {}
        for t, ty in zip(times, types):
            first_of.setdefault(ty, t)
        signup = first_of.get(EVENTS["signups"])
        trial = first_of.get(EVENTS["trials"])
        paid = first_of.get(EVENTS["paid"]) if "paid" in EVENTS else None
        last = times[-1]
        visit_days = {t.date() for t in times}
        counts = Counter(ACTION_LABELS[ty] for ty in types if ty in ACTION_LABELS)
        p = props.get(uid, {})
        monitored = _as_list(p.get(BRAND_USER_PROP))
        n_brands = len(monitored) or None
        viewed = Counter()  # 행동 이벤트에 찍힌 브랜드
        for e in evs:
            for b in _as_list((e.get("event_properties") or {}).get(BRAND_EVENT_PROP)):
                viewed[b] += 1

        if paid:
            status = "유료"
        elif trial:
            left = TRIAL_DAYS - (now - trial).days
            if left > 0:
                status = "이탈 위험" if (now - last).days >= 2 else f"체험 중 D-{left}"
            else:
                status = "체험 종료"
        else:
            status = "가입만" if signup else "기존 가입자"

        src = p.get("initial_utm_source") or "(직접)"
        content = p.get("initial_utm_content")
        bits = []
        if signup:
            bits.append(f"{signup:%m/%d} 가입")
        if trial:
            bits.append(f"{trial:%m/%d} 체험 시작")
        if paid:
            bits.append(f"{paid:%m/%d} 결제")
        acts = [f"{k} {v}회" for k, v in counts.most_common() if k != "페이지 조회"][:4]
        bits.append(" · ".join(acts) if acts else "핵심 행동 없음")
        if n_brands:
            bits.append(f"브랜드 {n_brands}개 모니터링")
        rows.append({
            "account": _mask(uid), "plan": p.get("plan"), "account_type": p.get("account_type"),
            "source": src, "content": content, "meta": is_meta(src),
            "signup": f"{signup:%Y-%m-%d}" if signup else None, "trial": f"{trial:%Y-%m-%d}" if trial else None,
            "status": status, "last_seen": f"{last:%Y-%m-%d %H:%M}",
            "today": today in visit_days, "yesterday": yesterday in visit_days,
            "visit_days_7": sum(1 for d in visit_days if (today - d).days < 7),
            "summary": " → ".join(bits),
            "monitored_brands": monitored,
            "viewed_brands": [{"brand": b, "n": n} for b, n in viewed.most_common(8)],
        })
    rows.sort(key=lambda r: r["last_seen"], reverse=True)
    popular = Counter()
    for r in rows:  # 계정 수 기준 인기 브랜드 (설정 + 실제 조회)
        for b in set(r["monitored_brands"]) | {v["brand"] for v in r["viewed_brands"]}:
            popular[b] += 1
    with_brand = sum(1 for r in rows if r["monitored_brands"] or r["viewed_brands"])
    print(f"   브랜드 정보 있는 계정 {with_brand}/{len(rows)}개 "
          f"(설정 {sum(1 for r in rows if r['monitored_brands'])} · 조회 {sum(1 for r in rows if r['viewed_brands'])})")
    return {"popular_brands": [{"brand": b, "accounts": n} for b, n in popular.most_common(15)],"generated_kst": f"{now:%Y-%m-%d %H:%M}", "days": days, "masked": not SHOW_FULL_ACCOUNT,
            "missing_days": len(missing),
            "excluded_count": len(excluded),
            "today_count": sum(r["today"] for r in rows), "rows": rows}
