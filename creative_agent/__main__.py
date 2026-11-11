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
    r.add_argument("--ai-refresh", action="store_true", help="오늘 만든 AI 코멘트가 있어도 새로 생성")
    r.add_argument("--skip-collect", action="store_true", help="DB에 있는 데이터로만 분석")
    r.add_argument("--site", help="팀 공유용 정적 사이트를 만들 폴더 (DASHBOARD_PASSWORD 로 암호화)")
    d = sub.add_parser("demo", help="가짜 데이터로 대시보드 미리보기")
    d.add_argument("--ai", action="store_true", help="데모 데이터에도 AI 코멘트 생성")
    d.add_argument("--site", help="팀 공유용 정적 사이트를 만들 폴더")
    sub.add_parser("check", help="연결 점검")
    sub.add_parser("amp-debug", help="앰플리튜드 활성화 수치 검증 (최근 30일)")
    a = p.parse_args()
    if a.cmd == "check":
        return check()
    if a.cmd == "amp-debug":
        from . import amplitude
        u = str(date.today() - timedelta(days=1))
        return amplitude.debug(str(date.fromisoformat(u) - timedelta(days=29)), u)

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
        from . import competitors
        try:
            n = competitors.label_pending(con)
            print(f"③-2 경쟁사 광고 라벨링 {n}개 (새 스크린샷만)")
        except Exception as e:  # 경쟁사 패널 실패가 전체를 막지 않게
            print(f"   ⚠️ 경쟁사 광고 라벨링 건너뜀: {e}")

    print("④ 집계/진단 (기간별)")
    from . import amplitude
    use_amp = amplitude.enabled() and a.cmd != "demo"
    views = []
    for key, label, s_, u_ in report.ranges(since, until):
        rep = analyze.build(con, s_, u_)
        rep.update(key=key, label=label)
        if use_amp:
            amplitude.attach(rep, amplitude.fetch(s_, u_))
        elif a.cmd == "demo":
            from . import demo
            amplitude.attach(rep, demo.fake_amplitude(rep))
        else:
            rep["amplitude"] = None
        amp_txt = ""
        if rep.get("amplitude"):
            t = rep["amplitude"]["totals"]
            amp_txt = (f" · 가입 {t['signups']:.0f}(메타 {rep['amplitude']['meta']['signups']:.0f})"
                       f" · 체험 {t['trials']:.0f} · 활성화 {t['activated']:.0f}"
                       f" (광고와 연결된 소재 {rep['amplitude']['matched_ads']}개)")
        print(f"   {label}: 소재 {rep['total_count']}개, 라벨 {rep['labeled_count']}개{amp_txt}")
        views.append(rep)
    for v in views:  # 소재와 못 이은 utm_content 값 (UTM 설정 점검용)
        if v["key"] == "30d" and v.get("amplitude") and v["amplitude"]["unmatched_values"]:
            vals = ", ".join(f"{u['value']}({u['signups']:.0f})" for u in v["amplitude"]["unmatched_values"][:10])
            print(f"   ⚠️ 소재와 연결 안 된 utm_content (가입 수): {vals}")
    default = next((v for v in views if v["key"] == "30d"), views[-1])
    from . import competitors as comp_mod
    comp = comp_mod.build(con, fake=(a.cmd == "demo"))
    if comp:
        print(f"   경쟁사 {len(comp['brands'])}곳, 저장한 광고 {len(comp['ads'])}개")
    if use_ai and default["labeled_count"]:
        # AI 코멘트는 하루(한국 날짜) 한 번만 만들고, 같은 날 다른 실행은 저장된 코멘트를 재사용
        import json as _json
        from datetime import datetime as _dt, timezone as _tz
        kst_today = str(_dt.now(_tz(timedelta(hours=9))).date())
        cache = config.DATA_DIR / "ai_summary.json"
        cached = _json.loads(cache.read_text(encoding="utf-8")) if cache.exists() else {}
        if not getattr(a, "ai_refresh", False) and cached.get("date") == kst_today and cached.get("summary"):
            print(f"⑤ AI 코멘트: 오늘({kst_today}) 만든 코멘트 재사용")
            default["ai_summary"] = cached["summary"]
        else:
            from . import insights
            print(f"⑤ AI 코멘트 생성 ({default['label']} 기준)")
            default["ai_summary"] = insights.generate(default, comp)
            if default["ai_summary"]:
                cache.parent.mkdir(parents=True, exist_ok=True)
                cache.write_text(_json.dumps({"date": kst_today, "summary": default["ai_summary"]}, ensure_ascii=False),
                                 encoding="utf-8")
    rep = report.bundle(views, default["key"])
    if comp:
        rep["thumbs"].update(comp.pop("thumbs"))
        rep["competitors"] = comp
    if use_amp:
        print("⑥ 가입 계정별 활동 (Amplitude Export)")
        rep["accounts"] = amplitude.accounts(30)
        if rep["accounts"]:
            print(f"   계정 {len(rep['accounts']['rows'])}개 · 오늘 방문 {rep['accounts']['today_count']}명"
                  f" · 제외 {rep['accounts']['excluded_count']}개")
    elif a.cmd == "demo":
        from . import demo
        rep["accounts"] = demo.fake_accounts()
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
