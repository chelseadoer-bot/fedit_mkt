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
