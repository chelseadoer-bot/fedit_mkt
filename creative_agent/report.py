"""분석 결과를 단일 HTML 파일(데이터·썸네일 내장)로 렌더링."""
import json
from datetime import datetime
from pathlib import Path

from . import config

TEMPLATE = Path(__file__).parent / "templates" / "dashboard.html"


def ranges(since: str, until: str) -> list[tuple[str, str, str, str]]:
    """대시보드에서 고를 수 있는 기간 목록 (수집 기간 안에 들어오는 것만)."""
    from datetime import date, timedelta
    s0, u = date.fromisoformat(since), date.fromisoformat(until)
    out = []
    for n in (7, 14, 30, 90):
        s = u - timedelta(days=n - 1)
        if s >= s0:
            out.append((f"{n}d", f"최근 {n}일", str(s), str(u)))
    month_start = u.replace(day=1)
    if month_start >= s0 and month_start < u:
        out.append(("this_month", f"이번 달 ({u.month}월)", str(month_start), str(u)))
    last_end = month_start - timedelta(days=1)
    last_start = last_end.replace(day=1)
    if last_start >= s0:
        out.append(("last_month", f"지난달 ({last_end.month}월)", str(last_start), str(last_end)))
    span = (u - s0).days + 1
    if span not in (7, 14, 30, 90):
        out.append(("all", f"전체 수집 기간 ({span}일)", since, until))
    return out


def bundle(views: list[dict], default_key: str) -> dict:
    """기간별 리포트 묶음. 썸네일은 한 번만 담는다."""
    thumbs = {}
    for v in views:
        thumbs.update(v.pop("thumbs", {}))
    d = next(v for v in views if v["key"] == default_key)
    return {"views": views, "default": default_key, "thumbs": thumbs, "period": d["period"]}


def stamp(report: dict) -> dict:
    from datetime import timezone, timedelta
    kst = datetime.now(timezone(timedelta(hours=9)))  # GitHub 서버(UTC)에서 돌아도 한국 시간으로 표시
    return {**report, "generated_at": kst.strftime("%Y-%m-%d %H:%M")}


def render(report: dict, out: Path | None = None) -> Path:
    report = stamp(report)
    payload = json.dumps(report, ensure_ascii=False).replace("</", "<\\/")
    html = TEMPLATE.read_text(encoding="utf-8").replace("__DATA__", payload)
    out = out or config.OUT_DIR / f"creative_{report['period']['since']}_{report['period']['until']}.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    (out.parent / "latest.html").write_text(html, encoding="utf-8")
    return out
