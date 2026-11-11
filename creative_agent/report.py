"""분석 결과를 단일 HTML 파일(데이터·썸네일 내장)로 렌더링."""
import json
from datetime import datetime
from pathlib import Path

from . import config

TEMPLATE = Path(__file__).parent / "templates" / "dashboard.html"


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
