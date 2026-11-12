"""팀 공유용 정적 사이트 생성: 대시보드 데이터를 비밀번호로 암호화해 index.html 하나로 만든다.

암호화: PBKDF2-SHA256(20만 회)로 키를 만들고 AES-256-GCM으로 데이터 JSON을 암호화.
브라우저에서 같은 방식(WebCrypto)으로 복호화하므로 비밀번호 없이는 내용을 볼 수 없다.
"""
import base64
import json
import os
import pathlib
from pathlib import Path

ITERATIONS = 200_000


def encrypt(payload: str, password: str) -> dict:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

    salt, iv = os.urandom(16), os.urandom(12)
    key = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=ITERATIONS).derive(password.encode())
    ct = AESGCM(key).encrypt(iv, payload.encode(), None)
    b64 = lambda b: base64.b64encode(b).decode()
    return {"enc": {"v": 1, "iter": ITERATIONS, "salt": b64(salt), "iv": b64(iv), "ct": b64(ct)}}


def write_site(report: dict, out_dir: Path, password: str) -> Path:
    from . import report as report_mod

    out_dir.mkdir(parents=True, exist_ok=True)
    data = json.dumps(report, ensure_ascii=False)
    envelope = encrypt(data, password) if password else json.loads(data)
    html = report_mod.TEMPLATE.read_text(encoding="utf-8").replace(
        "__DATA__", json.dumps(envelope, ensure_ascii=False).replace("</", "<\\/"))
    (out_dir / "index.html").write_text(html, encoding="utf-8")
    # 콘텐츠 캘린더 저장 API (버셀 서버리스 함수)
    api_src = pathlib.Path(__file__).parent / "site_api" / "calendar.js"
    (out_dir / "api").mkdir(exist_ok=True)
    (out_dir / "api" / "calendar.js").write_text(api_src.read_text(encoding="utf-8"), encoding="utf-8")
    # 검색엔진 노출 방지
    (out_dir / "robots.txt").write_text("User-agent: *\nDisallow: /\n", encoding="utf-8")
    (out_dir / "vercel.json").write_text(json.dumps({
        "headers": [{"source": "/(.*)", "headers": [
            {"key": "X-Robots-Tag", "value": "noindex, nofollow"},
            {"key": "Cache-Control", "value": "no-cache"},
        ]}]
    }, indent=2), encoding="utf-8")
    return out_dir / "index.html"
