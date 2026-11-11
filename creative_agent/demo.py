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
        by_ad[a["ad_id"]] = {"signups": su, "trials": tr, "activated": round(tr * rnd.uniform(0.2, 0.7))}
    meta = {k: sum(v[k] for v in by_ad.values()) for k in ("signups", "trials", "activated")}
    by_source = {"meta": meta, "(none)": {"signups": 31, "trials": 20, "activated": 11},
                 "naver": {"signups": 12, "trials": 8, "activated": 5}, "linkedin": {"signups": 4, "trials": 2, "activated": 1}}
    totals = {k: sum(v[k] for v in by_source.values()) for k in meta}
    return {"events": {"signups": "sign_up", "trials": "trial_started", "activated": "excel_download"},
            "totals": totals, "by_ad": by_ad, "by_source": by_source,
            "ad_property": "gp:initial_utm_content", "source_property": "gp:initial_utm_source"}
