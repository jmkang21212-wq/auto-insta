"""SQLite 저장소. 구성안의 7테이블을 그대로 둡니다.

모든 레코드가 run_id로 묶여 "어느 날 어떤 공시가 어떤 판정을 거쳐 무엇이 나갔는지"를
역추적할 수 있는 것이 목적입니다. 제외·불일치 건도 지우지 않습니다.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS run (
  id INTEGER PRIMARY KEY,
  account TEXT NOT NULL,            -- kr / us
  kind TEXT NOT NULL,               -- daily / ipo / followup / weekly
  started_at TEXT NOT NULL,
  finished_at TEXT,
  status TEXT NOT NULL DEFAULT 'running',
  stage_reached TEXT
);
CREATE TABLE IF NOT EXISTS source (
  id INTEGER PRIMARY KEY,
  account TEXT NOT NULL,
  name TEXT NOT NULL UNIQUE,
  adapter TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1,
  last_ok_at TEXT,
  fail_count INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS disclosure (
  id INTEGER PRIMARY KEY,
  run_id INTEGER NOT NULL REFERENCES run(id),
  source_id INTEGER REFERENCES source(id),
  rcept_no TEXT NOT NULL UNIQUE,
  corp_code TEXT,
  corp_name TEXT NOT NULL,
  report_type TEXT NOT NULL,        -- ipo / capital_increase / cb / insider / ...
  report_name TEXT,
  filed_at TEXT,
  parent_rcept_no TEXT              -- 정정 공시면 원 공시 접수번호
);
CREATE TABLE IF NOT EXISTS issue (
  id INTEGER PRIMARY KEY,
  run_id INTEGER NOT NULL REFERENCES run(id),
  disclosure_id INTEGER NOT NULL REFERENCES disclosure(id),
  score REAL NOT NULL,
  selected INTEGER NOT NULL DEFAULT 0,
  standalone INTEGER NOT NULL DEFAULT 0,
  excluded_reason TEXT
);
CREATE TABLE IF NOT EXISTS fact (
  id INTEGER PRIMARY KEY,
  issue_id INTEGER NOT NULL REFERENCES issue(id),
  fields TEXT NOT NULL,             -- JSON: API 필드값 전체
  summary TEXT,                     -- JSON: LLM 문장 + 근거 문장
  verified INTEGER NOT NULL DEFAULT 0,
  mismatch_detail TEXT
);
CREATE TABLE IF NOT EXISTS post (
  id INTEGER PRIMARY KEY,
  run_id INTEGER NOT NULL REFERENCES run(id),
  issue_id INTEGER REFERENCES issue(id),
  cards TEXT NOT NULL,              -- JSON: 장별 문구·템플릿
  image_paths TEXT,                 -- JSON: 로컬 PNG 경로
  image_urls TEXT,                  -- JSON: 공개 URL (Storage 업로드 후)
  caption TEXT,
  approved_at TEXT,
  ig_container_id TEXT,
  ig_media_id TEXT,
  story_media_id TEXT,
  published_at TEXT,
  error TEXT
);
CREATE TABLE IF NOT EXISTS followup (
  id INTEGER PRIMARY KEY,
  disclosure_id INTEGER NOT NULL REFERENCES disclosure(id),
  kind TEXT NOT NULL,               -- listing_result / subscription_reminder
  due_date TEXT NOT NULL,
  done_run_id INTEGER REFERENCES run(id)
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class DB:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        try:
            yield self.conn
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    # --- run -------------------------------------------------------------
    def start_run(self, account: str, kind: str) -> int:
        with self.tx() as c:
            cur = c.execute(
                "INSERT INTO run(account, kind, started_at) VALUES (?,?,?)",
                (account, kind, now_iso()),
            )
            return int(cur.lastrowid)

    def stage(self, run_id: int, stage: str) -> None:
        with self.tx() as c:
            c.execute("UPDATE run SET stage_reached=? WHERE id=?", (stage, run_id))

    def finish_run(self, run_id: int, status: str) -> None:
        with self.tx() as c:
            c.execute(
                "UPDATE run SET status=?, finished_at=? WHERE id=?",
                (status, now_iso(), run_id),
            )

    # --- source ----------------------------------------------------------
    def ensure_source(self, account: str, name: str, adapter: str) -> int:
        with self.tx() as c:
            c.execute(
                "INSERT OR IGNORE INTO source(account, name, adapter) VALUES (?,?,?)",
                (account, name, adapter),
            )
            row = c.execute("SELECT id FROM source WHERE name=?", (name,)).fetchone()
            return int(row["id"])

    def source_ok(self, source_id: int) -> None:
        with self.tx() as c:
            c.execute(
                "UPDATE source SET last_ok_at=?, fail_count=0 WHERE id=?",
                (now_iso(), source_id),
            )

    def source_fail(self, source_id: int, disable_after: int = 3) -> None:
        with self.tx() as c:
            c.execute("UPDATE source SET fail_count=fail_count+1 WHERE id=?", (source_id,))
            c.execute(
                "UPDATE source SET enabled=0 WHERE id=? AND fail_count>=?",
                (source_id, disable_after),
            )

    # --- disclosure / issue / fact / post / followup ---------------------
    def upsert_disclosure(self, run_id: int, source_id: int, d: dict[str, Any]) -> int:
        with self.tx() as c:
            c.execute(
                """INSERT OR IGNORE INTO disclosure
                   (run_id, source_id, rcept_no, corp_code, corp_name, report_type,
                    report_name, filed_at, parent_rcept_no)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    run_id, source_id, d["rcept_no"], d.get("corp_code"), d["corp_name"],
                    d["report_type"], d.get("report_name"), d.get("filed_at"),
                    d.get("parent_rcept_no"),
                ),
            )
            row = c.execute(
                "SELECT id FROM disclosure WHERE rcept_no=?", (d["rcept_no"],)
            ).fetchone()
            return int(row["id"])

    def add_issue(self, run_id: int, disclosure_id: int, score: float,
                  selected: bool, standalone: bool, excluded_reason: str | None) -> int:
        with self.tx() as c:
            cur = c.execute(
                """INSERT INTO issue(run_id, disclosure_id, score, selected, standalone, excluded_reason)
                   VALUES (?,?,?,?,?,?)""",
                (run_id, disclosure_id, score, int(selected), int(standalone), excluded_reason),
            )
            return int(cur.lastrowid)

    def add_fact(self, issue_id: int, fields: dict, summary: dict | None,
                 verified: bool, mismatch_detail: str | None) -> int:
        with self.tx() as c:
            cur = c.execute(
                "INSERT INTO fact(issue_id, fields, summary, verified, mismatch_detail) VALUES (?,?,?,?,?)",
                (issue_id, json.dumps(fields, ensure_ascii=False),
                 json.dumps(summary, ensure_ascii=False) if summary else None,
                 int(verified), mismatch_detail),
            )
            return int(cur.lastrowid)

    def add_post(self, run_id: int, issue_id: int, cards: list[dict],
                 image_paths: list[str], caption: str) -> int:
        with self.tx() as c:
            cur = c.execute(
                "INSERT INTO post(run_id, issue_id, cards, image_paths, caption) VALUES (?,?,?,?,?)",
                (run_id, issue_id, json.dumps(cards, ensure_ascii=False),
                 json.dumps(image_paths, ensure_ascii=False), caption),
            )
            return int(cur.lastrowid)

    def add_followup(self, disclosure_id: int, kind: str, due_date: str) -> None:
        with self.tx() as c:
            c.execute(
                "INSERT INTO followup(disclosure_id, kind, due_date) VALUES (?,?,?)",
                (disclosure_id, kind, due_date),
            )
