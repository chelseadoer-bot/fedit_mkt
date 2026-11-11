"""집계 결과를 Claude에게 넘겨 마케터용 코멘트(발견 + 다음 액션)를 생성."""
import json
import os

import anthropic

from . import config

SYSTEM = """당신은 퍼포먼스 마케팅 팀의 시니어 크리에이티브 전략가입니다.
메타 광고 소재의 라벨 축별 성과와 소재별 성과/진단 데이터를 받아, 다음 주 소재 제작·예산 운영에 바로 쓸 수 있는 코멘트를 씁니다.

이 계정의 핵심 목표는 데이터의 '핵심전환'(예: 랜딩페이지 조회)입니다. 앰플리튜드 데이터(가입·체험·활성화)가 있으면 그것이 최우선입니다. 판단 순서:
0) (앰플리튜드가 있으면) 최종 기준은 '활성화 = 무료체험 후 브랜드 선택'. 소재별 무료체험 신청 수와 활성화 수, 가입당 비용을 보고, 랜딩은 많은데 가입·체험이 없는 소재를 반드시 짚을 것
1) 핵심전환 1건당 비용(CPA)과 전환 수
2) 도달률(핵심전환 ÷ 링크클릭) — 랜딩페이지 조회라면, 클릭 후 랜딩이 실제로 열린 비율. 낮으면 랜딩 로딩 속도·광고와 랜딩의 메시지 불일치·실수 클릭을 의심
3) CTR(후킹)과 CPC

원칙:
- 데이터에 있는 숫자만 인용합니다. 추정은 '추정'이라고 밝힙니다.
- 소재 수가 3개 미만이거나 전환이 10건 미만인 그룹/소재의 차이는 '표본 적음'으로 신중하게 다룹니다.
- 액션은 '무엇을, 어느 소재/축에, 얼마나'가 드러나게 구체적으로 씁니다. 예산 이동, 새로 만들 소재 방향(앵글×페르소나×포맷), 끌 소재, 랜딩 점검 항목.
- 경쟁사 광고 데이터가 있으면, 우리 소재와 앵글·포맷·오퍼가 어떻게 다른지 짚고 다음에 테스트할 만한 것을 1개 이상 액션에 넣습니다(경쟁사 성과는 모르므로 '참고'로만).
- 한국어, 간결한 존댓말."""

SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {"type": "string"},
        "findings": {"type": "array", "items": {
            "type": "object",
            "properties": {"title": {"type": "string"}, "detail": {"type": "string"}},
            "required": ["title", "detail"], "additionalProperties": False}},
        "actions": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["headline", "findings", "actions"],
    "additionalProperties": False,
}


def _compact(report: dict) -> dict:
    pct = lambda x: round(x * 100, 2)
    conv = report["conversion"]["label"]
    roas = report["goal"] == "roas"
    amp = report.get("amplitude")

    def m(x: dict) -> dict:
        out = {"광고비": round(x["spend"]), "노출": int(x["impressions"]), "링크클릭": int(x["clicks"]),
               "CTR%": pct(x["ctr"]), "CPC": round(x["cpc"])}
        if roas:
            out.update({"구매": int(x["purchases"]), "ROAS%": pct(x["roas"]), "CVR%": pct(x["cvr"])})
        else:
            out.update({conv: int(x["conversions"]), f"{conv}당비용": round(x["cpa"]), "도달률%": pct(x["cvr"])})
        if amp and "signups" in x:
            out.update({"가입": round(x["signups"], 1), "가입당비용": round(x["cps"]) if x.get("cps") else None,
                        "무료체험신청": round(x["trials"], 1), "체험중브랜드선택(활성화)": round(x["activated"], 1),
                        **({"유료전환": round(x["paid"], 1)} if "paid" in x else {})})
        return out

    dims = {d: [{"값": g["value"], "소재수": g["count"], **m(g)} for g in gs]
            for d, gs in report["dimensions"].items()}
    ads = [{"이름": a["name"], "세트": a["sub"], "라벨": a["labels"], "설명": a["description"],
            "진단": a["diagnosis"], **m(a)} for a in report["ads"][:30]]
    return {"기간": report["period"], "핵심전환": conv,
            "계정합계": m(report["account"]),
            "분석대상합계(이미지 있는 소재)": m(report["analyzed_totals"]),
            "라벨된소재": f'{report["labeled_count"]}/{report["total_count"]}',
            "축별성과": dims, "광고비상위소재": ads,
            **({"앰플리튜드": {
                "이벤트": amp["events"], "기간전체": amp["totals"], "메타유입": amp["meta"],
                "유입소스별": amp["sources"][:10], "광고와연결안된_utm_content": amp["unmatched"],
                "주의": f"활성화=무료체험 시작 후 {amp.get('trial_days', 5)}일(체험 기간) 안에 브랜드를 선택한 사람. 유료 전환(paid)이 있으면 그것이 최종 성과, 없으면 활성화가 최종 기준. 최근 체험자는 아직 진행 중일 수 있음. 가입은 첫 유입 UTM 기준."}} if amp else {})}


def _comp(c: dict | None) -> dict:
    if not c or not c.get("ads"):
        return {}
    names = {b["slug"]: b["name"] for b in c["brands"]}
    return {"경쟁사광고(사람이 저장한 스크린샷, 표본 적음)": [
        {"회사": names.get(a["brand"], a["brand"]), "처음본날": a["date"], "라벨": a["labels"],
         "헤드라인": a["headline"], "CTA": a["cta"], "타깃": a["target"], "전략": a["description"]}
        for a in c["ads"] if a["labels"]]}


def generate(report: dict, competitors: dict | None = None) -> dict | None:
    client = anthropic.Anthropic()
    resp = client.beta.messages.create(
        model=config.CLAUDE_MODEL,
        max_tokens=16000,
        system=SYSTEM,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        output_config={
            "effort": os.getenv("INSIGHT_EFFORT", "high"),
            "format": {"type": "json_schema", "schema": SCHEMA},
        },
        messages=[{"role": "user", "content":
                   "아래 데이터로 이번 기간 소재 분석 코멘트를 써 주세요. headline은 2문장 이내, findings는 3~5개, "
                   "actions는 반드시 3~5개(비워 두지 마세요).\n\n"
                   + json.dumps({**_compact(report), **_comp(competitors)}, ensure_ascii=False)}],
    )
    if resp.stop_reason == "refusal":
        print("  ⚠️ 코멘트 생성이 거절되었습니다")
        return None
    return json.loads(next(b.text for b in resp.content if b.type == "text"))
