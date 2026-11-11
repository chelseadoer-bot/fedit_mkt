"""사용법:
  python -m creative_agent run [--days 7 | --since 2026-11-01 --until 2026-11-10] [--no-ai]
  python -m creative_agent demo
  python -m creative_agent check     # Meta 토큰/광고계정/Claude 키 연결 점검
"""
import argparse
import os
import sys
from datetime import date, timedelta

from . import analyze, config, db, report


def check() -> None:
    import requests
    ok = True
    tok, acct = config.META_ACCESS_TOKEN, config.META_AD_ACCOUNT_ID
    if not tok:
        print("❌ META_ACCESS_TOKEN 이 .env 에 없습니다"); ok = False
    if not acct.startswith("act_"):
        print("❌ META_AD_ACCOUNT_ID 는 act_숫자 형태여야 합니다 (현재: %r)" % acct); ok = False
    if ok:
        base = f"https://graph.facebook.com/{config.META_API_VERSION}"
        r = requests.get(f"{base}/{acct}", params={"fields": "name,currency,account_status,timezone_name",
                                                   "access_token": tok}, timeout=30)
        if r.ok:
            j = r.json()
            print(f"✅ 광고계정 연결됨: {j.get('name')} ({acct}, {j.get('currency')}, {j.get('timezone_name')})")
            r2 = requests.get(f"{base}/{acct}/insights", params={"date_preset": "last_7d",
                                                                "fields": "spend,impressions,inline_link_clicks,actions",
                                                                "access_token": tok}, timeout=60)
            if r2.ok:
                d = (r2.json().get("data") or [{}])[0]
                print(f"✅ 성과 읽기 OK: 최근 7일 광고비 {d.get('spend', 0)}, 노출 {d.get('impressions', 0)}, "
                      f"링크 클릭 {d.get('inline_link_clicks', 0)}")
                acts = {a["action_type"]: a["value"] for a in d.get("actions") or []}
                conv = next((t for t in config.CONVERSION_ACTIONS if t in acts), None)
                if conv:
                    print(f"✅ 핵심 전환: {config.ACTION_LABELS.get(conv, conv)} {acts[conv]}건 ({conv})")
                else:
                    print(f"⚠️  핵심 전환({', '.join(config.CONVERSION_ACTIONS)})이 최근 7일에 0건입니다")
                if acts:
                    print("   잡히는 이벤트: " + ", ".join(f"{config.ACTION_LABELS.get(k, k)} {v}" for k, v in
                                                   sorted(acts.items(), key=lambda kv: -float(kv[1]))[:8]))
            else:
                print("❌ 성과 읽기 실패:", r2.json().get("error", {}).get("message")); ok = False
        else:
            msg = r.json().get("error", {}).get("message", r.text[:200])
            print("❌ 광고계정 접근 실패:", msg)
            print("   → 시스템 사용자에 이 광고계정이 할당됐는지, 토큰에 ads_read 권한이 있는지 확인하세요"); ok = False
    if os.getenv("ANTHROPIC_API_KEY"):
        print("✅ ANTHROPIC_API_KEY 설정됨")
    else:
        print("⚠️  ANTHROPIC_API_KEY 가 없습니다 — 이미지 라벨링/AI 코멘트에 필요합니다")
    print("\n준비 완료! → python -m creative_agent run --days 7" if ok else "")


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # 윈도우 콘솔 한글/이모지 깨짐 방지
    p = argparse.ArgumentParser(prog="creative_agent")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="Meta 수집 → 라벨링 → 분석 → 대시보드")
    r.add_argument("--days", type=int, default=7)
    r.add_argument("--since")
    r.add_argument("--until")
    r.add_argument("--no-ai", action="store_true", help="AI 코멘트 생략(라벨링은 수행)")
    r.add_argument("--skip-collect", action="store_true", help="DB에 있는 데이터로만 분석")
    r.add_argument("--site", help="팀 공유용 정적 사이트를 만들 폴더 (DASHBOARD_PASSWORD 로 암호화)")
    d = sub.add_parser("demo", help="가짜 데이터로 대시보드 미리보기")
    d.add_argument("--ai", action="store_true", help="데모 데이터에도 AI 코멘트 생성")
    d.add_argument("--site", help="팀 공유용 정적 사이트를 만들 폴더")
    sub.add_parser("check", help="연결 점검")
    a = p.parse_args()
    if a.cmd == "check":
        return check()

    con = db.connect()
    if a.cmd == "demo":
        since, until = __import__("creative_agent.demo", fromlist=["seed"]).seed(con)
        use_ai = a.ai
    else:
        until = a.until or str(date.today() - timedelta(days=1))
        since = a.since or str(date.fromisoformat(until) - timedelta(days=a.days - 1))
        use_ai = not a.no_ai
        if not a.skip_collect:
            if not (config.META_ACCESS_TOKEN and config.META_AD_ACCOUNT_ID):
                sys.exit("META_ACCESS_TOKEN / META_AD_ACCOUNT_ID 를 .env 에 설정하세요 (.env.example 참고)")
            from . import meta_api
            print(f"① Meta 인사이트 수집 {since} ~ {until}")
            print(f"   {meta_api.fetch_insights(con, date.fromisoformat(since), date.fromisoformat(until))}행")
            print("② 소재 이미지 수집")
            try:
                print(f"   새 이미지 {meta_api.fetch_creatives(con)}개")
            except meta_api.MetaAPIError as e:  # 이미지 일부를 못 받아도 분석·배포는 계속
                print(f"   ⚠️ 소재 이미지 수집 중단: {e} — 이미 받은 이미지로 계속 진행합니다")
        from . import labeler
        print("③ Claude 이미지 라벨링 (새 이미지만)")
        print(f"   {labeler.label_pending(con)}개 라벨링")

    print("④ 집계/진단")
    rep = analyze.build(con, since, until)
    print(f"   소재 {rep['total_count']}개, 라벨 {rep['labeled_count']}개")
    if use_ai and rep["labeled_count"]:
        from . import insights
        print("⑤ AI 코멘트 생성")
        rep["ai_summary"] = insights.generate(rep)
    out = report.render(rep)
    print(f"✅ 대시보드: {out}")
    if a.site:
        from . import publish
        from pathlib import Path
        pw = os.getenv("DASHBOARD_PASSWORD", "")
        if not pw:
            sys.exit("❌ --site 를 쓰려면 DASHBOARD_PASSWORD 를 설정하세요 (비밀번호 없이 공개 배포하지 않습니다)")
        print(f"✅ 공유용 사이트(암호화): {publish.write_site(report.stamp(rep), Path(a.site), pw)}")


if __name__ == "__main__":
    main()
