"""请求日志与 Dashboard 统计（SQLite，WAL 模式）。"""
from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

log = logging.getLogger("ragproxy.requestlog")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS requests (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  model TEXT,
  kb TEXT,
  query_preview TEXT,
  hit INTEGER DEFAULT 0,
  hit_docs TEXT,
  retrieve_ms REAL,
  total_ms REAL,
  status INTEGER,
  upstream TEXT,
  stream INTEGER DEFAULT 0,
  fmt TEXT,
  response TEXT
);
CREATE INDEX IF NOT EXISTS idx_requests_ts ON requests(ts);
CREATE INDEX IF NOT EXISTS idx_requests_model ON requests(model);
CREATE INDEX IF NOT EXISTS idx_requests_kb ON requests(kb);
"""


class RequestLogger:
    def __init__(self, db_path: Path, keep_days: int = 90):
        self.db_path = db_path
        self.keep_days = keep_days
        self._lock = threading.Lock()
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self) -> None:
        with self._lock:
            conn = self._conn()
            try:
                conn.executescript(_SCHEMA)
                # 旧库迁移：补充 response 列
                cols = {r["name"] for r in conn.execute("PRAGMA table_info(requests)")}
                if "response" not in cols:
                    conn.execute("ALTER TABLE requests ADD COLUMN response TEXT")
                conn.commit()
            finally:
                conn.close()

    async def log(
        self,
        *,
        ts: str,
        model: str,
        kb: str,
        query_preview: str,
        hit: bool,
        hit_docs: list[str],
        retrieve_ms: float,
        total_ms: float,
        status: int,
        upstream: str,
        stream: bool,
        fmt: str,
        response: str = "",
    ) -> None:
        def _write() -> None:
            try:
                with self._lock:
                    conn = self._conn()
                    try:
                        conn.execute(
                            """INSERT INTO requests
                            (ts, model, kb, query_preview, hit, hit_docs, retrieve_ms,
                             total_ms, status, upstream, stream, fmt, response)
                            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                            (
                                ts,
                                model,
                                kb or "",
                                query_preview,
                                1 if hit else 0,
                                json.dumps(hit_docs[:50], ensure_ascii=False),
                                float(retrieve_ms or 0),
                                float(total_ms or 0),
                                int(status),
                                upstream or "",
                                1 if stream else 0,
                                fmt,
                                response or "",
                            ),
                        )
                        if self.keep_days > 0:
                            conn.execute(
                                "DELETE FROM requests WHERE ts < datetime('now', ?)",
                                (f"-{self.keep_days} days",),
                            )
                        conn.commit()
                    finally:
                        conn.close()
            except Exception:  # noqa: BLE001
                log.exception("写入请求日志失败")

        await asyncio.to_thread(_write)

    def query(
        self,
        start: str | None = None,
        end: str | None = None,
        model: str | None = None,
        kb: str | None = None,
        status: int | None = None,
        page: int = 1,
        size: int = 20,
    ) -> dict[str, Any]:
        where: list[str] = []
        params: list[Any] = []
        if start:
            where.append("ts >= ?")
            params.append(start)
        if end:
            where.append("ts <= ?")
            params.append(end)
        if model:
            where.append("model = ?")
            params.append(model)
        if kb:
            where.append("kb LIKE ?")
            params.append(f"%{kb}%")
        if status is not None:
            where.append("status = ?")
            params.append(status)
        cond = (" WHERE " + " AND ".join(where)) if where else ""
        page = max(1, page)
        size = min(100, max(1, size))
        with self._lock:
            conn = self._conn()
            try:
                total = conn.execute(
                    f"SELECT COUNT(*) c FROM requests{cond}", params
                ).fetchone()["c"]
                rows = conn.execute(
                    f"SELECT * FROM requests{cond} ORDER BY id DESC LIMIT ? OFFSET ?",
                    params + [size, (page - 1) * size],
                ).fetchall()
                items = [dict(r) for r in rows]
            finally:
                conn.close()
        return {"total": total, "page": page, "size": size, "items": items}

    def stats(self) -> dict[str, Any]:
        with self._lock:
            conn = self._conn()
            try:
                total = conn.execute("SELECT COUNT(*) c FROM requests").fetchone()["c"]
                today = conn.execute(
                    "SELECT COUNT(*) c FROM requests WHERE ts >= date('now')"
                ).fetchone()["c"]
                hit = conn.execute(
                    "SELECT COUNT(*) c FROM requests WHERE hit=1"
                ).fetchone()["c"]
                avg_retrieve = conn.execute(
                    "SELECT AVG(retrieve_ms) a FROM requests"
                ).fetchone()["a"] or 0.0
                avg_total = conn.execute(
                    "SELECT AVG(total_ms) a FROM requests"
                ).fetchone()["a"] or 0.0
                err = conn.execute(
                    "SELECT COUNT(*) c FROM requests WHERE status >= 400"
                ).fetchone()["c"]
                per_model = [
                    dict(r)
                    for r in conn.execute(
                        """SELECT model, COUNT(*) c,
                           SUM(hit) hits, ROUND(AVG(retrieve_ms),1) avg_ms
                           FROM requests GROUP BY model ORDER BY c DESC LIMIT 20"""
                    ).fetchall()
                ]
                per_kb = [
                    dict(r)
                    for r in conn.execute(
                        """SELECT kb, COUNT(*) c FROM requests
                           WHERE kb != '' GROUP BY kb ORDER BY c DESC LIMIT 20"""
                    ).fetchall()
                ]
                last24h = [
                    dict(r)
                    for r in conn.execute(
                        """SELECT strftime('%Y-%m-%d %H:00', ts) h, COUNT(*) c
                           FROM requests WHERE ts >= datetime('now','-24 hours')
                           GROUP BY h ORDER BY h"""
                    ).fetchall()
                ]
            finally:
                conn.close()
        return {
            "total": total,
            "today": today,
            "hit_count": hit,
            "hit_rate": round(hit / total, 4) if total else 0.0,
            "avg_retrieve_ms": round(avg_retrieve, 2),
            "avg_total_ms": round(avg_total, 2),
            "error_count": err,
            "per_model": per_model,
            "per_kb": per_kb,
            "last24h": last24h,
        }


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")
