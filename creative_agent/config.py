"""설정: 환경변수 + 라벨 축/진단 규칙."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("CREATIVE_DATA_DIR", ROOT / "data"))
IMAGE_DIR = DATA_DIR / "images"
DB_PATH = DATA_DIR / "creative.db"
OUT_DIR = Path(os.getenv("CREATIVE_OUT_DIR", ROOT / "out"))


def load_dotenv(path: Path = ROOT / ".env") -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        if v.strip():
            os.environ.setdefault(k.strip(), v.strip())


load_dotenv()

META_ACCESS_TOKEN = os.getenv("META_ACCESS_TOKEN", "")
META_AD_ACCOUNT_ID = os.getenv("META_AD_ACCOUNT_ID", "")
META_API_VERSION = os.getenv("META_API_VERSION", "v24.0")
META_PURCHASE_ACTIONS = [
    a.strip()
    for a in os.getenv("META_PURCHASE_ACTION", "omni_purchase,purchase,offsite_conversion.fb_pixel_purchase").split(",")
    if a.strip()
]
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-opus-5-5")

# ── 분석 프로필 ──
# b2b: 가입/리드 전환·CPA 중심, 메시지 앵글·타깃 페르소나 축 (FEDIT 기본값)
# commerce: 구매·ROAS 중심, 구도·할인표기 축 (쇼핑몰용)
PROFILE = (os.getenv("CREATIVE_PROFILE") or "b2b").strip().lower()

# 핵심 전환으로 셀 action_type (우선순위 순, 처음 잡히는 것 하나만 사용).
#  b2b 기본값: 랜딩페이지 조회(landing_page_view) — 링크 클릭 후 랜딩이 실제로 로드된 횟수
#  다른 이벤트(가입·리드 등)로 바꾸려면 .env 에 META_CONVERSION_ACTION=complete_registration 처럼 지정
_DEFAULT_CONV = {
    "b2b": "landing_page_view,omni_landing_page_view",
    "commerce": "omni_purchase,purchase,offsite_conversion.fb_pixel_purchase",
}
CONVERSION_ACTIONS = [a.strip() for a in (os.getenv("META_CONVERSION_ACTION") or _DEFAULT_CONV.get(PROFILE, "")).split(",")
                      if a.strip()]
# 목표: b2b → 전환당 비용(CPA), commerce → 매출(ROAS)
GOAL = os.getenv("CREATIVE_GOAL", "roas" if PROFILE == "commerce" else "cpa")
# 사람이 읽기 쉬운 이벤트 이름
ACTION_LABELS = {
    "landing_page_view": "랜딩페이지 조회", "omni_landing_page_view": "랜딩페이지 조회",
    "complete_registration": "회원가입", "offsite_conversion.fb_pixel_complete_registration": "회원가입(픽셀)",
    "lead": "리드", "offsite_conversion.fb_pixel_lead": "리드(픽셀)", "onsite_conversion.lead_grouped": "리드(메타 양식)",
    "start_trial": "무료체험 시작", "offsite_conversion.fb_pixel_start_trial": "무료체험 시작(픽셀)",
    "submit_application": "신청서 제출", "offsite_conversion.fb_pixel_submit_application": "신청서 제출(픽셀)",
    "contact": "문의", "offsite_conversion.fb_pixel_contact": "문의(픽셀)",
    "subscribe": "구독", "offsite_conversion.fb_pixel_subscribe": "구독(픽셀)",
    "omni_purchase": "구매", "purchase": "구매", "offsite_conversion.fb_pixel_purchase": "구매(픽셀)",
}

# ── 라벨 축: Claude가 소재 이미지(+광고 이름)를 보고 각 축에서 하나씩 고릅니다. 자유롭게 수정하세요. ──
PROFILES = {
    "b2b": {
        "dimensions": {
            "메시지 앵글": ["질문형", "문제제기형", "호기심형", "혜택/기능소개형", "공감/후기형", "숫자/데이터형"],
            "타깃 페르소나": ["MD", "바이어", "쇼핑몰 사장님", "디자이너", "영업/벤더", "불특정"],
            "포맷": ["화면 시연/UI", "인물/UGC", "상품·공간 사진", "그래픽/텍스트"],
            "텍스트량": ["없음", "적음", "많음"],
            "톤": ["밝음/화사", "뉴트럴/차분", "어두움/무드", "비비드"],
        },
        "tags": {"메시지 앵글": None, "타깃 페르소나": None},  # None = 값을 그대로 태그로
        "guide": """- 메시지 앵글: 카피의 말 걸기 방식. "혹시 MD세요?"처럼 묻으면 '질문형', 고충·불편을 짚으면 '문제제기형', "저만 몰랐어요"처럼 궁금증을 유발하면 '호기심형', 기능·이점을 직접 설명하면 '혜택/기능소개형', 사용자 경험담이면 '공감/후기형', 숫자를 앞세우면 '숫자/데이터형'.
- 타깃 페르소나: 카피나 장면이 부르는 대상. 패션 MD → 'MD', 바이어 → '바이어', 1인/온라인 쇼핑몰 운영자 → '쇼핑몰 사장님', 브랜드 디자이너 → '디자이너', OEM·영업 담당 → '영업/벤더', 특정 대상이 없으면 '불특정'.
- 포맷: 노트북·앱 화면 시연이면 '화면 시연/UI', 사람이 주인공이거나 UGC 느낌이면 '인물/UGC', 의류·작업실·매장 사진이면 '상품·공간 사진', 텍스트·도형 위주 디자인이면 '그래픽/텍스트'.
- 텍스트량: 이미지 위 카피·자막 양.
- 톤: 전체 색감과 밝기.
광고 이름에 페르소나/앵글 단서(md, buyer, shop, solo, vendor, q=질문, prob=문제제기, cur=호기심, bnf=혜택)가 있으면 참고하되, 이미지와 다르면 이미지를 우선합니다.""",
    },
    "commerce": {
        "dimensions": {
            "구도": ["모델컷", "모델+상품컷", "상품컷"],
            "배경": ["스튜디오/단색", "실내", "야외", "합성/그래픽"],
            "텍스트량": ["없음", "적음", "많음"],
            "할인표기": ["있음", "없음"],
            "톤": ["밝음/화사", "뉴트럴/차분", "어두움/무드", "비비드"],
            "모델": ["여성", "남성", "혼성/다수", "없음"],
        },
        "tags": {"구도": {"모델컷": "모델컷", "상품컷": "상품컷"}, "할인표기": {"있음": "가격 표기"}},
        "guide": """- 구도: 사람이 주인공이면 '모델컷', 사람과 상품 단독컷이 함께면 '모델+상품컷', 사람 없이 상품만 있으면 '상품컷'.
- 배경: 무지/스튜디오는 '스튜디오/단색', 그래픽·콜라주·합성이면 '합성/그래픽'.
- 텍스트량: 로고만 있으면 '적음', 헤드라인+부가 문구가 화면의 약 20% 이상이면 '많음'.
- 할인표기: %, 할인가, 쿠폰, SALE, 가격 숫자가 보이면 '있음'.
- 톤: 전체 색감과 밝기. 모델: 보이는 사람의 성별 구성, 없으면 '없음'.""",
    },
}
if PROFILE not in PROFILES:
    raise SystemExit(f"CREATIVE_PROFILE 은 {list(PROFILES)} 중 하나여야 합니다 (현재: {PROFILE})")
DIMENSIONS: dict[str, list[str]] = PROFILES[PROFILE]["dimensions"]
TAG_RULES = PROFILES[PROFILE]["tags"]
LABEL_GUIDE = PROFILES[PROFILE]["guide"]

# ── 진단 규칙 (계정 평균 대비) ──
DIAGNOSIS = {
    "min_spend": float(os.getenv("MIN_SPEND") or "10000"),  # 이 광고비 미만은 '모니터링'
    # 전환(CPA) 기준
    "scale_cpa_ratio": 0.8,      # CPA ≤ 계정 CPA × 0.8 → 증액
    "bad_cpa_ratio": 1.5,        # CPA ≥ 계정 CPA × 1.5 → 감액검토 / 전환율개선
    "no_conv_spend_x": 2.0,      # 전환 0인데 광고비 ≥ 계정 CPA × 2 → 감액검토
    # 매출(ROAS) 기준 (commerce)
    "scale_roas_ratio": 1.2,
    "cut_roas_ratio": 0.6,
    # 공통
    "low_ctr_ratio": 0.7,        # CTR < 계정 CTR × 0.7 → 클릭률개선
    "high_ctr_ratio": 1.3,       # (전환 데이터가 없을 때) CTR ≥ 계정 × 1.3 → 증액
    "good_cvr_ratio": 1.0,
}
