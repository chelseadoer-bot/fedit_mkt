"""성과 집계 + 라벨 축별 비교 + 소재 진단.

목표(config.GOAL)
- cpa : 핵심 전환(기본: 랜딩페이지 조회) 수·전환당 비용·도달률(전환/링크클릭) 중심
- roas: 구매 매출·ROAS 중심 (쇼핑몰용)
"""
import base64
import io
import json
from collections import defaultdict

from PIL import Image

from . import config

KEYS = ("spend", "impressions", "clicks", "conversions", "purchases", "revenue")


def _ratio(a: float, b: float) -> float:
    return a / b if b else 0.0


def _metrics(m: dict) -> dict:
    return {
        **m,
        "ctr": _ratio(m["clicks"], m["impressions"]),
        "cpc": _ratio(m["spend"], m["clicks"]),
        "cpa": _ratio(m["spend"], m["conversions"]),
        "cvr": _ratio(m["conversions"], m["clicks"]),  # LPV 기준이면 '랜딩 도달률'
        "roas": _ratio(m["revenue"], m["spend"]),
        "aov": _ratio(m["revenue"], m["purchases"]),
    }


def _conversions(actions_json: str | None) -> tuple[float, str | None]:
    if not actions_json:
        return 0.0, None
    acts = json.loads(actions_json)
    for t in config.CONVERSION_ACTIONS:  # 우선순위대로 하나만(중복 집계 방지)
        if t in acts:
            return acts[t], t
    return 0.0, None


def _thumb(path: str | None, size: int = 160) -> str | None:
    if not path:
        return None
    try:
        im = Image.open(path).convert("RGB")
    except OSError:
        return None
    im.thumbnail((size, size))
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=78)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def _conv_fix_label(conv_action: str | None) -> str:
    return "랜딩도달개선" if conv_action and "landing_page_view" in conv_action else "전환율개선"


def diagnosis_order(conv_action: str | None) -> list[str]:
    if config.GOAL == "roas":
        return ["증액", "감액검토", "클릭률개선", "객단가검토", "모니터링"]
    return ["증액", "감액검토", "클릭률개선", _conv_fix_label(conv_action), "모니터링"]


def diagnose(ad: dict, acct: dict, conv_action: str | None) -> str:
    d = config.DIAGNOSIS
    if ad["spend"] < d["min_spend"]:
        return "모니터링"
    low_ctr = ad["ctr"] < acct["ctr"] * d["low_ctr_ratio"]

    if config.GOAL == "roas":
        if ad["roas"] >= acct["roas"] * d["scale_roas_ratio"]:
            return "증액"
        if low_ctr:
            return "클릭률개선"
        if ad["roas"] < acct["roas"] * d["cut_roas_ratio"]:
            return "객단가검토" if ad["cvr"] >= acct["cvr"] * d["good_cvr_ratio"] else "감액검토"
        return "모니터링"

    if acct["conversions"] <= 0:  # 전환 데이터가 아예 없으면 CTR로만 판단
        if ad["ctr"] >= acct["ctr"] * d["high_ctr_ratio"]:
            return "증액"
        return "클릭률개선" if low_ctr else "모니터링"

    if ad["conversions"] <= 0:
        if ad["spend"] >= acct["cpa"] * d["no_conv_spend_x"]:
            return "감액검토"
        return "클릭률개선" if low_ctr else "모니터링"
    if ad["cpa"] <= acct["cpa"] * d["scale_cpa_ratio"]:
        return "증액"
    if low_ctr:
        return "클릭률개선"
    if ad["cpa"] >= acct["cpa"] * d["bad_cpa_ratio"]:
        # 클릭은 잘 받는데 전환(랜딩 도달)이 안 되면 → 랜딩/전환 경로 문제
        return _conv_fix_label(conv_action) if ad["cvr"] < acct["cvr"] else "감액검토"
    return "모니터링"


def _won(v: float) -> str:
    return f"₩{v:,.0f}"


def _insight(dim: str, groups: list[dict], conv_label: str) -> str | None:
    groups = [g for g in groups if g["spend"] > 0]
    if len(groups) < 2:
        return None
    base = max(groups, key=lambda g: g["spend"])
    small = lambda g: " (표본 적음)" if g["count"] < 3 else ""

    if config.GOAL == "roas":
        best = max(groups, key=lambda g: g["roas"])
        if best is base:
            base = sorted(groups, key=lambda g: -g["spend"])[1]
        if not base["roas"]:
            return None
        return (f"{dim} 「{best['value']}」{small(best)}의 ROAS {best['roas']*100:.0f}%가 "
                f"「{base['value']}」({base['roas']*100:.0f}%)의 {best['roas']/base['roas']:.1f}배입니다.")

    with_conv = [g for g in groups if g["conversions"] > 0]
    if len(with_conv) >= 2:
        best = min(with_conv, key=lambda g: g["cpa"])
        if best is base or base not in with_conv:
            base = max((g for g in with_conv if g is not best), key=lambda g: g["spend"])
        diff = 1 - best["cpa"] / base["cpa"]
        return (f"{dim} 「{best['value']}」{small(best)}의 {conv_label} 1건당 비용 {_won(best['cpa'])}이 "
                f"「{base['value']}」({_won(base['cpa'])})보다 {diff*100:.0f}% 낮습니다 · "
                f"도달률 {best['cvr']*100:.1f}% vs {base['cvr']*100:.1f}%")
    best = max(groups, key=lambda g: g["ctr"])
    if best is base:
        base = sorted(groups, key=lambda g: -g["spend"])[1]
    return (f"{dim} 「{best['value']}」{small(best)}의 CTR {best['ctr']*100:.2f}%가 "
            f"「{base['value']}」({base['ctr']*100:.2f}%)보다 높습니다.")


def build(con, since: str, until: str) -> dict:
    rows = con.execute(
        """SELECT a.ad_id, a.ad_name, a.adset_name, a.campaign_name, a.platform, a.image_hash, a.image_path,
                  d.date, d.spend, d.impressions, d.clicks, d.purchases, d.revenue, d.actions,
                  l.labels, l.description
           FROM daily d JOIN ads a USING(ad_id)
           LEFT JOIN labels l ON l.image_hash = ? || ':' || a.image_hash
           WHERE d.date BETWEEN ? AND ? ORDER BY d.date""",
        (config.PROFILE, since, until),
    ).fetchall()

    ads: dict[str, dict] = {}
    seen_actions: dict[str, float] = defaultdict(float)
    conv_used: dict[str, float] = defaultdict(float)
    for r in rows:
        conv, conv_type = _conversions(r["actions"])
        if conv_type:
            conv_used[conv_type] += conv
        for k, v in json.loads(r["actions"] or "{}").items():
            seen_actions[k] += v
        a = ads.setdefault(r["ad_id"], {
            "ad_id": r["ad_id"], "name": r["ad_name"] or r["ad_id"],
            "sub": " › ".join(x for x in (r["campaign_name"], r["adset_name"]) if x),
            "platform": r["platform"], "image_hash": r["image_hash"], "image_path": r["image_path"],
            "labels": json.loads(r["labels"]) if r["labels"] else None, "description": r["description"],
            **{k: 0.0 for k in KEYS}, "_days": defaultdict(lambda: [0.0, 0.0, 0.0]),
        })
        vals = {"spend": r["spend"], "impressions": r["impressions"], "clicks": r["clicks"],
                "conversions": conv, "purchases": r["purchases"], "revenue": r["revenue"]}
        for k in KEYS:
            a[k] += vals[k] or 0
        day = a["_days"][r["date"]]
        day[0] += r["spend"] or 0
        day[1] += r["revenue"] or 0
        day[2] += conv

    conv_action = max(conv_used, key=conv_used.get) if conv_used else (config.CONVERSION_ACTIONS or [None])[0]
    conv_label = config.ACTION_LABELS.get(conv_action, conv_action or "전환")
    all_days = sorted({r["date"] for r in rows})
    acct = _metrics({k: sum(a[k] for a in ads.values()) for k in KEYS})

    out_ads, thumbs = [], {}
    for a in ads.values():
        if a["spend"] <= 0 and a["impressions"] <= 0:
            continue
        if not a["image_hash"]:  # 고정 이미지를 못 구한 광고는 소재 분석에서 제외
            continue
        m = _metrics({k: a[k] for k in KEYS})
        if config.GOAL == "roas":
            series = [round(_ratio(a["_days"][d][1], a["_days"][d][0]), 3) if d in a["_days"] else None
                      for d in all_days]
        else:  # 일별 전환 수
            series = [a["_days"][d][2] if d in a["_days"] else None for d in all_days]
        tags = []
        for dim, mapping in config.TAG_RULES.items():
            v = (a["labels"] or {}).get(dim)
            if v and (mapping is None or v in mapping):
                tags.append(v if mapping is None else mapping[v])
        if a["image_hash"] not in thumbs:
            t = _thumb(a["image_path"])
            if t:
                thumbs[a["image_hash"]] = t
        out_ads.append({
            "ad_id": a["ad_id"], "name": a["name"], "sub": a["sub"], "platform": a["platform"],
            "thumb": a["image_hash"] if a["image_hash"] in thumbs else None,
            "labels": a["labels"], "description": a["description"], "tags": tags,
            **m, "diagnosis": diagnose(m, acct, conv_action), "series": series,
        })
    out_ads.sort(key=lambda x: -x["spend"])

    labeled = [a for a in out_ads if a["labels"]]
    dimensions, insights = {}, {}
    for dim, values in config.DIMENSIONS.items():
        groups = []
        for v in values:
            members = [a for a in labeled if a["labels"].get(dim) == v]
            if not members:
                continue
            g = _metrics({k: sum(a[k] for a in members) for k in KEYS})
            g.update(value=v, count=len(members),
                     thumbs=[a["thumb"] for a in sorted(members, key=lambda x: -x["spend"]) if a["thumb"]][:3])
            groups.append(g)
        groups.sort(key=lambda g: -g["spend"])
        dimensions[dim] = groups
        insights[dim] = _insight(dim, groups, conv_label)

    analyzed = _metrics({k: sum(a[k] for a in out_ads) for k in KEYS})
    labeled_totals = _metrics({k: sum(a[k] for a in labeled) for k in KEYS})
    return {
        "period": {"since": since, "until": until}, "days": all_days,
        "goal": config.GOAL, "profile": config.PROFILE,
        "conversion": {"action": conv_action, "label": conv_label,
                       "is_landing": bool(conv_action and "landing_page_view" in conv_action)},
        "seen_actions": dict(sorted(seen_actions.items(), key=lambda kv: -kv[1])[:15]),
        "account": acct, "analyzed_totals": analyzed, "labeled_totals": labeled_totals,
        "labeled_count": len(labeled), "total_count": len(out_ads),
        "dimensions": dimensions, "insights": insights,
        "diagnosis_order": diagnosis_order(conv_action),
        "min_spend": config.DIAGNOSIS["min_spend"],
        "ads": out_ads, "thumbs": thumbs,
    }
