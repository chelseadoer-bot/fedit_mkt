"""SQLite 저장소: 광고 메타, 일별 성과, 이미지 라벨(이미지 해시 기준 캐시)."""
import json
import sqlite3
from .config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS ads (
  ad_id TEXT PRIMARY KEY,
  ad_name TEXT, adset_name TEXT, campaign_name TEXT,
  platform TEXT DEFAULT '메타',
  image_url TEXT, image_hash TEXT, image_path TEXT,
  updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS daily (
  ad_id TEXT, date TEXT,
  spend REAL, impressions INTEGER, clicks INTEGER,
  purchases REAL, revenue REAL, actions TEXT,
  PRIMARY KEY (ad_id, date)
);
CREATE TABLE IF NOT EXISTS labels (
  image_hash TEXT PRIMARY KEY,
  labels TEXT, description TEXT, model TEXT,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
"""


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    cols = {r["name"] for r in con.execute("PRAGMA table_info(daily)")}
    if "actions" not in cols:  # 이전 버전 DB 마이그레이션
        con.execute("ALTER TABLE daily ADD COLUMN actions TEXT")
    return con


def label_key(image_hash: str) -> str:
    """라벨은 프로필(b2b/commerce)별로 따로 캐시 — 축이 다르기 때문."""
    from .config import PROFILE
    return f"{PROFILE}:{image_hash}"


def upsert_ad(con, ad: dict) -> None:
    con.execute(
        """INSERT INTO ads(ad_id, ad_name, adset_name, campaign_name, platform, image_url, image_hash, image_path)
           VALUES(:ad_id, :ad_name, :adset_name, :campaign_name, :platform, :image_url, :image_hash, :image_path)
           ON CONFLICT(ad_id) DO UPDATE SET ad_name=excluded.ad_name, adset_name=excluded.adset_name,
             campaign_name=excluded.campaign_name, image_url=COALESCE(excluded.image_url, ads.image_url),
             image_hash=COALESCE(excluded.image_hash, ads.image_hash),
             image_path=COALESCE(excluded.image_path, ads.image_path), updated_at=CURRENT_TIMESTAMP""",
        {"platform": "메타", **ad},
    )


def upsert_daily(con, row: dict) -> None:
    con.execute(
        """INSERT OR REPLACE INTO daily(ad_id, date, spend, impressions, clicks, purchases, revenue, actions)
           VALUES(:ad_id, :date, :spend, :impressions, :clicks, :purchases, :revenue, :actions)""",
        {"actions": None, **row},
    )


def get_label(con, image_hash: str):
    r = con.execute("SELECT labels FROM labels WHERE image_hash=?", (label_key(image_hash),)).fetchone()
    return json.loads(r["labels"]) if r else None


def save_label(con, image_hash: str, labels: dict, description: str, model: str) -> None:
    con.execute(
        "INSERT OR REPLACE INTO labels(image_hash, labels, description, model) VALUES(?,?,?,?)",
        (label_key(image_hash), json.dumps(labels, ensure_ascii=False), description, model),
    )
