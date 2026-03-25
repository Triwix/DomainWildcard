from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


VALID_JOB_STATES = {"queued", "running", "completed", "failed", "cancelled"}
VALID_RESULT_STATES = {"available", "taken", "unknown"}


@dataclass
class DomainResult:
    domain: str
    state: str
    rdap_host: Optional[str] = None
    http_status: Optional[int] = None
    error: Optional[str] = None
    source: Optional[str] = None
    checked_at: Optional[str] = None
    ttl_seconds: Optional[int] = None
    expires_at: Optional[str] = None
    from_cache: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "domain": self.domain,
            "state": self.state,
            "rdap_host": self.rdap_host,
            "http_status": self.http_status,
            "error": self.error,
            "source": self.source,
            "checked_at": self.checked_at,
            "ttl_seconds": self.ttl_seconds,
            "expires_at": self.expires_at,
            "from_cache": self.from_cache,
        }


@dataclass
class Job:
    id: str
    pattern: str
    force_recheck: bool = False
    status: str = "queued"
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    total_candidates: int = 0
    valid_domains: int = 0
    processed: int = 0
    invalid_count: int = 0
    duplicate_count: int = 0
    available_count: int = 0
    taken_count: int = 0
    unknown_count: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    available_domains: List[str] = field(default_factory=list)
    available_results: List[Dict[str, Any]] = field(default_factory=list)
    recent_unknowns: List[Dict[str, Any]] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    event_queue: Any = None
    done_event: Any = None

    def snapshot(self) -> Dict[str, Any]:
        progress_processed = self.processed + self.invalid_count + self.duplicate_count
        return {
            "job_id": self.id,
            "pattern": self.pattern,
            "force_recheck": self.force_recheck,
            "status": self.status,
            "created_at": self.created_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "ended_at": self.ended_at.isoformat() if self.ended_at else None,
            "total_candidates": self.total_candidates,
            "valid_domains": self.valid_domains,
            "processed": self.processed,
            "progress_processed": progress_processed,
            "invalid_count": self.invalid_count,
            "duplicate_count": self.duplicate_count,
            "available_count": self.available_count,
            "taken_count": self.taken_count,
            "unknown_count": self.unknown_count,
            "cache_hits": self.cache_hits,
            "cache_misses": self.cache_misses,
            "errors": self.errors,
            "recent_unknowns": self.recent_unknowns,
            "available_domains": self.available_domains,
            "available_results": self.available_results,
        }
