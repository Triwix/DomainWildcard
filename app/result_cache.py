from __future__ import annotations

import asyncio
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


@dataclass
class CachedDomainRecord:
    domain: str
    state: str
    rdap_host: Optional[str]
    http_status: Optional[int]
    error: Optional[str]
    source: str
    checked_at: str
    ttl_seconds: int
    expires_at: str


class DomainResultCache:
    def __init__(self, db_path: Path, prune_interval_seconds: int = 300):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.prune_interval_seconds = max(60, int(prune_interval_seconds))
        self._lock = asyncio.Lock()
        self._last_prune_epoch = 0.0

        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA synchronous=NORMAL;")
        self._conn.execute("PRAGMA temp_store=MEMORY;")
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS domain_cache (
                domain TEXT PRIMARY KEY,
                state TEXT NOT NULL,
                rdap_host TEXT,
                http_status INTEGER,
                error TEXT,
                source TEXT NOT NULL,
                checked_at TEXT NOT NULL,
                ttl_seconds INTEGER NOT NULL,
                expires_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        self._conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_domain_cache_expires_at
            ON domain_cache (expires_at)
            """
        )
        self._conn.commit()

    async def close(self) -> None:
        async with self._lock:
            self._conn.close()

    async def get(self, domain: str) -> Optional[CachedDomainRecord]:
        now_text = datetime.now(timezone.utc).isoformat()
        async with self._lock:
            self._maybe_prune_locked()
            row = self._conn.execute(
                """
                SELECT domain, state, rdap_host, http_status, error, source, checked_at, ttl_seconds, expires_at
                FROM domain_cache
                WHERE domain = ?
                """,
                (domain,),
            ).fetchone()
            if row is None:
                return None

            if row["expires_at"] <= now_text:
                self._conn.execute("DELETE FROM domain_cache WHERE domain = ?", (domain,))
                self._conn.commit()
                return None

            return CachedDomainRecord(
                domain=row["domain"],
                state=row["state"],
                rdap_host=row["rdap_host"],
                http_status=row["http_status"],
                error=row["error"],
                source=row["source"],
                checked_at=row["checked_at"],
                ttl_seconds=int(row["ttl_seconds"]),
                expires_at=row["expires_at"],
            )

    async def put(
        self,
        domain: str,
        state: str,
        rdap_host: Optional[str],
        http_status: Optional[int],
        error: Optional[str],
        source: str,
        checked_at: str,
        ttl_seconds: int,
        expires_at: str,
    ) -> None:
        now_text = datetime.now(timezone.utc).isoformat()
        async with self._lock:
            self._conn.execute(
                """
                INSERT INTO domain_cache (
                    domain, state, rdap_host, http_status, error, source, checked_at, ttl_seconds, expires_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(domain) DO UPDATE SET
                    state = excluded.state,
                    rdap_host = excluded.rdap_host,
                    http_status = excluded.http_status,
                    error = excluded.error,
                    source = excluded.source,
                    checked_at = excluded.checked_at,
                    ttl_seconds = excluded.ttl_seconds,
                    expires_at = excluded.expires_at,
                    updated_at = excluded.updated_at
                """,
                (
                    domain,
                    state,
                    rdap_host,
                    http_status,
                    error,
                    source,
                    checked_at,
                    int(ttl_seconds),
                    expires_at,
                    now_text,
                ),
            )
            self._conn.commit()

    async def size(self) -> int:
        async with self._lock:
            row = self._conn.execute("SELECT COUNT(*) AS count FROM domain_cache").fetchone()
            if row is None:
                return 0
            return int(row["count"])

    def _maybe_prune_locked(self) -> None:
        now_epoch = time.time()
        if now_epoch - self._last_prune_epoch < self.prune_interval_seconds:
            return
        self._last_prune_epoch = now_epoch
        now_text = datetime.now(timezone.utc).isoformat()
        self._conn.execute("DELETE FROM domain_cache WHERE expires_at <= ?", (now_text,))
        self._conn.commit()
