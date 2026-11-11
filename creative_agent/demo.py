"""Meta 토큰 없이 대시보드를 확인하기 위한 가짜 데이터(라벨 포함, Claude 호출 없음)."""
import hashlib
import json
import random
from datetime import date, timedelta

from PIL import Image, ImageDraw

from . import config, db

PALETTE = {"밝음/화사": (236, 226, 214), "뉴트럴/차분": (196, 190, 180), "어두움/무드": (52, 48, 46), "비비드": (40, 120, 90)}
CAMPAIGNS = ["(성인) 공홈_전환_카탈로그 캠페인", "(성인) 공홈_전환_프로모션 캠페인_ABO", "(성인) 공홈_전환_콜라보_CBO",
             "협력광고_B2B_CPA_conversion_전환_카탈로그"]
ITEMS = ["winterspecial", "winterpajama", "outer_all", "knit_set", "denim", "padding", "cardigan", "fleece", "coat", "muffler"]


def _image(labels: dict, seed: int) -> bytes:
    rnd = random.Random(seed)
    bg = PALETTE.get(labels.get("톤"), (220, 220, 220))
    im = Image.new("RGB", (400, 400), bg)
    d = ImageDraw.Draw(im)
    fg = tuple(255 - c for c in bg)
    if rnd.random() < 0.5:  # 노트북 화면
        d.rounded_rectangle((70, 120, 330, 290), 10, fill=fg)
        d.rectangle((90, 140, 310, 270), fill=tuple(rnd.randint(120, 240) for _ in range(3)))
    else:  # 인물
        d.ellipse((170, 60, 230, 120), fill=fg)
        d.rounded_rectangle((140, 125, 260, 330), 30, fill=fg)
    if labels.get("텍스트량", "적음") != "없음":
        for i in range(1 if labels.get("텍스트량") == "적음" else 3):
            d.rectangle((30, 24 + i * 22, 30 + rnd.randint(120, 300), 38 + i * 22), fill=fg)
    return im_bytes(im)


def im_bytes(im) -> bytes:
    import io
    b = io.BytesIO()
    im.save(b, format="JPEG", quality=85)
    return b.getvalue()


def seed(con, days: int = 40, n_ads: int = 120) -> tuple[str, str]:
    rnd = random.Random(42)
    config.IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    until = date.today() - timedelta(days=1)
    since = until - timedelta(days=days - 1)
    # 구도별 기본 성향(상품컷 ROAS↑, 모델+상품컷 CTR↓ 등)을 심어 둔다
    base = {"모델컷": (31, .031, .095), "모델+상품컷": (32, .009, .065), "상품컷": (53, .035, .16)}
    for i in range(n_ads):
        labels = {dim: rnd.choice(vals) for dim, vals in config.DIMENSIONS.items()}
        has_image = rnd.random() > 0.35  # 일부는 카탈로그 광고(이미지 없음)
        h = path = None
        if has_image:
            data = _image(labels, i)
            h = hashlib.sha1(data).hexdigest()
            path = config.IMAGE_DIR / f"{h}.jpg"
            path.write_bytes(data)
            db.save_label(con, h, labels, " · ".join(labels.values()) + " 데모 소재", "demo")
        camp = rnd.choice(CAMPAIGNS)
        item = rnd.choice(ITEMS)
        ad_id = f"demo_{i:04d}"
        db.upsert_ad(con, {"ad_id": ad_id, "ad_name": f"{2600 + rnd.randint(1, 12)}_{item}_{i:02d}",
                           "adset_name": f"26{rnd.randint(1, 12):02d}_cv_{item}", "campaign_name": camp,
                           "image_url": None, "image_hash": h, "image_path": str(path) if path else None})
        first_dim = next(iter(config.DIMENSIONS))
        roas0, ctr0, cvr0 = list(base.values())[config.DIMENSIONS[first_dim].index(labels[first_dim]) % 3]
        lpv_rate = rnd.uniform(0.45, 0.9)
        q = rnd.lognormvariate(0, 0.45)
        scale = rnd.lognormvariate(8.2, 1.0)  # 하루 수천 원 규모
        trend = rnd.uniform(-0.04, 0.04)
        for d in range(days):
            spend = max(0, scale * rnd.uniform(0.6, 1.4))
            imp = int(spend / rnd.uniform(5.5, 8.5))
            ctr = ctr0 * q * rnd.uniform(0.7, 1.3)
            clicks = int(imp * ctr)
            cvr = cvr0 * q ** 0.5 * (1 + trend * d) * rnd.uniform(0.6, 1.4)
            purchases = round(clicks * max(cvr, 0))
            aov = spend * roas0 * q / max(purchases, 1) if purchases else 0
            revenue = purchases * min(aov, 180_000) * rnd.uniform(0.75, 1.25)
            lpv = round(clicks * lpv_rate * rnd.uniform(0.85, 1.1))
            db.upsert_daily(con, {"ad_id": ad_id, "date": str(since + timedelta(days=d)), "spend": round(spend),
                                  "impressions": imp, "clicks": clicks, "purchases": purchases, "revenue": round(revenue),
                                  "actions": json.dumps({"landing_page_view": lpv, "omni_landing_page_view": lpv,
                                                         "link_click": clicks, "purchase": purchases})})
    con.commit()
    return str(since), str(until)


def fake_amplitude(rep: dict) -> dict:
    """데모용 가짜 Amplitude 결과(광고 ID별 가입·체험·활성화)."""
    rnd = random.Random(7)
    by_ad = {}
    for a in rep["ads"]:
        su = round(a["conversions"] * rnd.uniform(0.02, 0.12))
        tr = round(su * rnd.uniform(0.4, 0.8))
        by_ad[a["ad_id"]] = {"signups": su, "trials": tr, "activated": round(tr * rnd.uniform(0.2, 0.7)),
                             "paid": round(tr * rnd.uniform(0.05, 0.25))}
    meta = {k: sum(v[k] for v in by_ad.values()) for k in ("signups", "trials", "activated", "paid")}
    by_source = {"meta": dict(meta), "(none)": {"signups": 31, "trials": 20, "activated": 11, "paid": 3},
                 "naver": {"signups": 12, "trials": 8, "activated": 5, "paid": 1}, "lead_mail": {"signups": 2, "trials": 1, "activated": 0, "paid": 0}, "linkedin": {"signups": 4, "trials": 2, "activated": 1, "paid": 0}}
    totals = {k: sum(v[k] for v in by_source.values()) for k in meta}
    return {"events": {"signups": "sign_up", "trials": "trial_started", "activated": "brand_filter_changed",
                       "paid": "payment_completed"},
            "labels": {"signups": "가입", "trials": "무료체험 신청", "activated": "체험 중 브랜드 선택", "paid": "유료 전환"},
            "activation_steps": ["trial_started", "brand_filter_changed"], "trial_days": 5, "paid_window_days": 14,
            "totals": totals, "by_ad": by_ad, "by_source": by_source,
            "ad_property": "gp:initial_utm_content", "source_property": "gp:initial_utm_source"}


BRANDS = ["무신사 스탠다드", "에잇세컨즈", "탑텐", "스파오", "커버낫", "디스이즈네버댓", "마뗑킴", "앤더슨벨"]


def fake_accounts() -> dict:
    """데모용 가입 계정 활동."""
    rnd = random.Random(11)
    statuses = ["체험 중 D-3", "체험 중 D-1", "이탈 위험", "체험 종료", "가입만", "유료", "기존 가입자"]
    rows = []
    for i in range(14):
        st = rnd.choice(statuses)
        acts = [f"{k} {rnd.randint(1, 20)}회" for k in rnd.sample(["상품 상세", "폴더 저장", "브랜드 필터", "엑셀 완성", "상품 목록 조회"], 3)]
        src = rnd.choice(["meta", "meta", "lead_mail", "(직접)", "naver"])
        rows.append({"account": f"user{i:02d}***@example.com", "plan": rnd.choice(["free", "trial", "pro"]),
                     "account_type": rnd.choice(["personal", "company"]), "source": src,
                     "content": "261020_q_refc_shop_v1" if src == "meta" else None, "meta": src in ("meta", "lead_mail"),
                     "signup": f"2026-11-{rnd.randint(1, 10):02d}", "trial": None if st == "가입만" else f"2026-11-{rnd.randint(5, 10):02d}",
                     "status": st, "last_seen": f"2026-11-{rnd.randint(6, 11):02d} {rnd.randint(9, 22):02d}:{rnd.randint(0, 59):02d}",
                     "today": rnd.random() < 0.3, "yesterday": rnd.random() < 0.4, "visit_days_7": rnd.randint(1, 6),
                     "summary": f"11/0{rnd.randint(1, 9)} 가입 → " + " · ".join(acts) + f" → 브랜드 {rnd.randint(1, 5)}개 모니터링",
                     "monitored_brands": rnd.sample(BRANDS, rnd.randint(1, 5)),
                     "viewed_brands": [{"brand": b, "n": rnd.randint(1, 30)} for b in rnd.sample(BRANDS, rnd.randint(0, 3))]})
    rows.sort(key=lambda r: r["last_seen"], reverse=True)
    from collections import Counter
    pop = Counter(b for r in rows for b in set(r["monitored_brands"]) | {v["brand"] for v in r["viewed_brands"]})
    return {"popular_brands": [{"brand": b, "accounts": n} for b, n in pop.most_common(15)],
            "generated_kst": "2026-11-11 13:00", "days": 30, "masked": False,
            "today_count": sum(r["today"] for r in rows), "rows": rows}
