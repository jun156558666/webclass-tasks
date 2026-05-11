import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

# Render は DATABASE_URL を自動セット（postgres://...）
_RAW_URL = os.getenv("DATABASE_URL", "")
IS_POSTGRES = _RAW_URL.startswith("postgres")

if IS_POSTGRES:
    import psycopg2
    import psycopg2.extras
    # psycopg2 は postgresql:// スキームを要求
    PG_URL = _RAW_URL.replace("postgres://", "postgresql://", 1)
    PH = "%s"  # プレースホルダー
else:
    DB_PATH = Path(__file__).parent.parent / "assignments.db"
    PH = "?"


@contextmanager
def _conn():
    if IS_POSTGRES:
        conn = psycopg2.connect(PG_URL)
        conn.autocommit = False
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    else:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()


def _rows(cursor) -> list[dict]:
    cols = [d[0] for d in cursor.description]
    return [dict(zip(cols, row)) for row in cursor.fetchall()]


def init_db():
    serial = "SERIAL PRIMARY KEY" if IS_POSTGRES else "INTEGER PRIMARY KEY AUTOINCREMENT"
    with _conn() as conn:
        conn.cursor().execute(f"""
            CREATE TABLE IF NOT EXISTS assignments (
                id          TEXT PRIMARY KEY,
                course_name TEXT NOT NULL,
                title       TEXT NOT NULL,
                deadline    TEXT,
                url         TEXT,
                submitted   INTEGER DEFAULT 0,
                scraped_at  TEXT NOT NULL
            )
        """)
        conn.cursor().execute(f"""
            CREATE TABLE IF NOT EXISTS scrape_log (
                id         {serial},
                scraped_at TEXT NOT NULL,
                count      INTEGER,
                error      TEXT
            )
        """)


def upsert_assignments(assignments: list[dict]):
    now = datetime.now().isoformat()
    with _conn() as conn:
        cur = conn.cursor()
        for a in assignments:
            cur.execute(f"""
                INSERT INTO assignments
                    (id, course_name, title, deadline, url, submitted, scraped_at)
                VALUES ({PH},{PH},{PH},{PH},{PH},0,{PH})
                ON CONFLICT(id) DO UPDATE SET
                    course_name = EXCLUDED.course_name,
                    title       = EXCLUDED.title,
                    deadline    = EXCLUDED.deadline,
                    url         = EXCLUDED.url,
                    scraped_at  = EXCLUDED.scraped_at
            """, (a["id"], a["course_name"], a["title"],
                  a.get("deadline"), a.get("url"), now))


def get_all_assignments() -> list[dict]:
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT * FROM assignments
            ORDER BY
                submitted ASC,
                CASE WHEN deadline IS NULL THEN 1 ELSE 0 END,
                deadline ASC
        """)
        return _rows(cur)


def set_submitted(assignment_id: str, submitted: bool) -> bool:
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute(
            f"UPDATE assignments SET submitted = {PH} WHERE id = {PH}",
            (1 if submitted else 0, assignment_id),
        )
        return cur.rowcount > 0


def log_scrape(count: int, error: str | None = None):
    with _conn() as conn:
        conn.cursor().execute(
            f"INSERT INTO scrape_log (scraped_at, count, error) VALUES ({PH},{PH},{PH})",
            (datetime.now().isoformat(), count, error),
        )


def get_last_scrape() -> dict | None:
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM scrape_log ORDER BY id DESC LIMIT 1")
        rows = _rows(cur)
        return rows[0] if rows else None
