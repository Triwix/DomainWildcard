from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Sequence

import httpx

from app.bootstrap import RDAPBootstrapResolver
from app.jobs import JobManager
from app.models import DomainResult
from app.patterns import PatternValidationError, estimate_total_candidates, validate_pattern
from app.rdap import HostRateLimiter, RDAPClient, build_default_known_policies
from app.result_cache import DomainResultCache
from app.wordlist import parse_wordlist_bytes

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "output"
DEFAULT_CACHE_DB_PATH = PROJECT_ROOT / "data" / "rdap_cache.sqlite3"
MAX_WORDLIST_WORDS = 50000
MAX_TOTAL_CANDIDATES = 1_000_000
VALID_EXPORT_FORMATS = ("txt", "csv", "json")
_FILENAME_NON_ALLOWED_RE = re.compile(r"[^a-z0-9.-]+")
_FILENAME_MULTI_DASH_RE = re.compile(r"-{2,}")
_WILDCARD_TOKEN_NON_ALLOWED_RE = re.compile(r"[^a-z0-9-]+")


ProgressCallback = Callable[[str, Dict[str, object], bool], None]


@dataclass
class PatternRunSummary:
    pattern: str
    normalized_pattern: Optional[str]
    status: str
    output_files: List[str]
    counts: Dict[str, int]
    error: Optional[str] = None
    job_id: Optional[str] = None

    def to_dict(self) -> Dict[str, object]:
        return {
            "pattern": self.pattern,
            "normalized_pattern": self.normalized_pattern,
            "status": self.status,
            "output_files": self.output_files,
            "counts": dict(self.counts),
            "error": self.error,
            "job_id": self.job_id,
        }


@dataclass
class BatchRunSummary:
    started_at: str
    finished_at: str
    output_dir: str
    formats: List[str]
    force_recheck: bool
    concurrency: int
    fail_fast: bool
    summary_path: str
    patterns: List[PatternRunSummary]

    def to_dict(self) -> Dict[str, object]:
        return {
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "output_dir": self.output_dir,
            "formats": self.formats,
            "force_recheck": self.force_recheck,
            "concurrency": self.concurrency,
            "fail_fast": self.fail_fast,
            "summary_path": self.summary_path,
            "patterns": [item.to_dict() for item in self.patterns],
        }


def parse_formats(raw_formats: str | Sequence[str] | None) -> List[str]:
    if raw_formats is None:
        return ["txt"]

    if isinstance(raw_formats, str):
        tokens = [piece.strip().lower() for piece in raw_formats.split(",")]
    else:
        tokens = [str(piece).strip().lower() for piece in raw_formats]

    selected: List[str] = []
    for token in tokens:
        if not token:
            continue
        if token not in VALID_EXPORT_FORMATS:
            raise ValueError(f"Unsupported format '{token}'. Valid values: {', '.join(VALID_EXPORT_FORMATS)}")
        if token not in selected:
            selected.append(token)

    if not selected:
        raise ValueError("At least one export format is required.")
    return selected


def read_wordlist_file(path: Path) -> List[str]:
    content = path.read_bytes()
    return parse_wordlist_bytes(content)


def normalize_wildcard_token(token: Optional[str]) -> str:
    value = str(token or "w").strip().lower()
    value = _WILDCARD_TOKEN_NON_ALLOWED_RE.sub("", value).strip("-")
    return value or "w"


def sanitize_pattern_for_filename(pattern: str, wildcard_token: Optional[str] = "w") -> str:
    token = normalize_wildcard_token(wildcard_token)
    sanitized = str(pattern or "").strip().lower().replace("*", token)
    sanitized = _FILENAME_NON_ALLOWED_RE.sub("-", sanitized)
    sanitized = _FILENAME_MULTI_DASH_RE.sub("-", sanitized)
    sanitized = sanitized.strip("-.")
    return sanitized or "search"


def build_export_filename(pattern: str, extension: str, wildcard_token: Optional[str] = "w") -> str:
    pattern_part = sanitize_pattern_for_filename(pattern, wildcard_token=wildcard_token)
    timestamp = datetime.now().astimezone().strftime("%Y-%m-%d-%H-%M-%S")
    ext = str(extension or "").strip().lower().lstrip(".")
    return f"{pattern_part}-{timestamp}.{ext}"


def build_summary_filename() -> str:
    timestamp = datetime.now().astimezone().strftime("%Y-%m-%d-%H-%M-%S")
    return f"batch-{timestamp}.json"


def ensure_unique_path(path: Path) -> Path:
    candidate = path
    suffix = candidate.suffix
    stem = candidate.stem
    index = 2
    while candidate.exists():
        candidate = candidate.with_name(f"{stem}-{index}{suffix}")
        index += 1
    return candidate


def _snapshot_counts(snapshot: Dict[str, object]) -> Dict[str, int]:
    keys = [
        "total_candidates",
        "valid_domains",
        "processed",
        "progress_processed",
        "available_count",
        "taken_count",
        "unknown_count",
        "invalid_count",
        "duplicate_count",
        "cache_hits",
        "cache_misses",
    ]
    counts: Dict[str, int] = {}
    for key in keys:
        value = snapshot.get(key, 0)
        counts[key] = int(value) if isinstance(value, int) else int(value or 0)
    return counts


def _render_export_content(manager: JobManager, job, fmt: str) -> str:
    if fmt == "txt":
        return manager.export_available_txt(job, sort_mode="earliest", query="")
    if fmt == "csv":
        return manager.export_available_csv(job, sort_mode="earliest", query="")
    payload = {"results": manager.export_available_json(job, sort_mode="earliest", query="")}
    return json.dumps(payload, indent=2)


def _validate_limit_counts(pattern: str, primary_words: List[str], secondary_words: Optional[List[str]]) -> int:
    total_candidates = estimate_total_candidates(
        pattern,
        len(primary_words),
        len(secondary_words) if secondary_words is not None else None,
    )
    if total_candidates > MAX_TOTAL_CANDIDATES:
        raise ValueError(
            f"Expanded candidate count ({total_candidates}) exceeds current max of {MAX_TOTAL_CANDIDATES}"
        )
    return total_candidates


class CacheOnlyRDAPClient:
    def __init__(self, result_cache: DomainResultCache):
        self.result_cache = result_cache

    async def check_domain(self, domain: str, force_recheck: bool = False) -> DomainResult:
        _ = force_recheck  # cache-only mode never performs network checks.
        cached = await self.result_cache.get(domain)
        if cached is None:
            return DomainResult(
                domain=domain,
                state="unknown",
                error="Cache miss (cache-only mode)",
                source="cache:miss",
                from_cache=False,
            )

        return DomainResult(
            domain=cached.domain,
            state=cached.state,
            rdap_host=cached.rdap_host,
            http_status=cached.http_status,
            error=cached.error,
            source=f"cache:{cached.source}",
            checked_at=cached.checked_at,
            ttl_seconds=cached.ttl_seconds,
            expires_at=cached.expires_at,
            from_cache=True,
        )


async def _wait_for_job(job, pattern: str, progress_callback: Optional[ProgressCallback]) -> Dict[str, object]:
    while True:
        snapshot = job.snapshot()
        if progress_callback:
            progress_callback(pattern, snapshot, False)
        if snapshot.get("status") in {"completed", "failed", "cancelled"}:
            if progress_callback:
                progress_callback(pattern, snapshot, True)
            return snapshot
        try:
            await asyncio.wait_for(job.done_event.wait(), timeout=1.0)
        except asyncio.TimeoutError:
            continue


async def _wait_for_job_with_stop(
    manager: JobManager,
    job,
    pattern: str,
    progress_callback: Optional[ProgressCallback],
    stop_requested: Optional[Callable[[], bool]],
) -> Dict[str, object]:
    cancel_requested = False
    while True:
        snapshot = job.snapshot()
        if progress_callback:
            progress_callback(pattern, snapshot, False)
        if snapshot.get("status") in {"completed", "failed", "cancelled"}:
            if progress_callback:
                progress_callback(pattern, snapshot, True)
            return snapshot
        if (
            not cancel_requested
            and stop_requested is not None
            and stop_requested()
            and snapshot.get("status") in {"queued", "running"}
        ):
            await manager.cancel_job(job.id)
            cancel_requested = True
            continue
        try:
            await asyncio.wait_for(job.done_event.wait(), timeout=1.0)
        except asyncio.TimeoutError:
            continue


async def run_batch(
    *,
    patterns: Iterable[str],
    wordlist_path: Path,
    wordlist_secondary_path: Optional[Path] = None,
    formats: str | Sequence[str] | None = None,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    force_recheck: bool = False,
    concurrency: int = 32,
    fail_fast: bool = False,
    progress_callback: Optional[ProgressCallback] = None,
    stop_requested: Optional[Callable[[], bool]] = None,
    export_partial_on_cancel: bool = True,
    dry_run: bool = False,
    cache_only: bool = False,
    cache_db_path: Optional[Path] = None,
    wildcard_token: str = "w",
    available_ttl_seconds: Optional[int] = None,
    taken_ttl_seconds: Optional[int] = None,
    unknown_ttl_seconds: Optional[int] = None,
    verisign_min_interval_seconds: Optional[float] = None,
    publicinterestregistry_min_interval_seconds: Optional[float] = None,
    identitydigital_min_interval_seconds: Optional[float] = None,
    registry_co_min_interval_seconds: Optional[float] = None,
    centralnic_min_interval_seconds: Optional[float] = None,
    gmoregistry_min_interval_seconds: Optional[float] = None,
    radix_min_interval_seconds: Optional[float] = None,
    denic_min_interval_seconds: Optional[float] = None,
    nominet_min_interval_seconds: Optional[float] = None,
    sidn_min_interval_seconds: Optional[float] = None,
    registro_br_min_interval_seconds: Optional[float] = None,
    au_min_interval_seconds: Optional[float] = None,
    rdap_client_override=None,
) -> BatchRunSummary:
    pattern_list = [str(item) for item in patterns if str(item).strip()]
    if not pattern_list:
        raise ValueError("At least one pattern is required.")
    if int(concurrency) < 1:
        raise ValueError("Concurrency must be at least 1.")
    if cache_only and force_recheck:
        raise ValueError("--cache-only cannot be combined with force_recheck.")

    selected_formats = parse_formats(formats)
    output_dir = Path(output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    primary_words = read_wordlist_file(Path(wordlist_path).expanduser().resolve())
    if not primary_words:
        raise ValueError("Primary wordlist is empty after parsing.")
    if len(primary_words) > MAX_WORDLIST_WORDS:
        raise ValueError(f"Wordlist exceeds the current max of {MAX_WORDLIST_WORDS} words")

    secondary_words: Optional[List[str]] = None
    if wordlist_secondary_path is not None:
        secondary_words = read_wordlist_file(Path(wordlist_secondary_path).expanduser().resolve())
        if not secondary_words:
            raise ValueError("Secondary wordlist is empty after parsing.")
        if len(secondary_words) > MAX_WORDLIST_WORDS:
            raise ValueError(f"Secondary wordlist exceeds the current max of {MAX_WORDLIST_WORDS} words")

    started_at = datetime.now().astimezone().isoformat()
    results: List[PatternRunSummary] = []

    http_client = None
    http_client_ipv4 = None
    cache = None

    try:
        if rdap_client_override is None:
            resolved_cache_path = Path(cache_db_path or DEFAULT_CACHE_DB_PATH).expanduser().resolve()
            cache = DomainResultCache(resolved_cache_path)
            if cache_only:
                rdap_client = CacheOnlyRDAPClient(cache)
            else:
                # Scale HTTP connection pools with requested worker concurrency so
                # high-concurrency CLI runs are not capped by httpx defaults.
                pool_limits = httpx.Limits(
                    max_connections=max(200, int(concurrency) * 2),
                    max_keepalive_connections=max(100, int(concurrency)),
                )
                http_client = httpx.AsyncClient(limits=pool_limits)
                http_client_ipv4 = httpx.AsyncClient(
                    limits=pool_limits,
                    transport=httpx.AsyncHTTPTransport(local_address="0.0.0.0")
                )
                resolver = RDAPBootstrapResolver()
                limiter = HostRateLimiter(
                    known_policies=build_default_known_policies(
                        verisign_min_interval_seconds=verisign_min_interval_seconds,
                        publicinterestregistry_min_interval_seconds=publicinterestregistry_min_interval_seconds,
                        identitydigital_min_interval_seconds=identitydigital_min_interval_seconds,
                        registry_co_min_interval_seconds=registry_co_min_interval_seconds,
                        centralnic_min_interval_seconds=centralnic_min_interval_seconds,
                        gmoregistry_min_interval_seconds=gmoregistry_min_interval_seconds,
                        radix_min_interval_seconds=radix_min_interval_seconds,
                        denic_min_interval_seconds=denic_min_interval_seconds,
                        nominet_min_interval_seconds=nominet_min_interval_seconds,
                        sidn_min_interval_seconds=sidn_min_interval_seconds,
                        registro_br_min_interval_seconds=registro_br_min_interval_seconds,
                        au_min_interval_seconds=au_min_interval_seconds,
                    )
                )
                rdap_client = RDAPClient(
                    http_client=http_client,
                    resolver=resolver,
                    ipv4_http_client=http_client_ipv4,
                    limiter=limiter,
                    result_cache=cache,
                    available_ttl_seconds=(
                        int(available_ttl_seconds) if available_ttl_seconds is not None else 15 * 60
                    ),
                    taken_ttl_seconds=(
                        int(taken_ttl_seconds) if taken_ttl_seconds is not None else 6 * 60 * 60
                    ),
                    unknown_ttl_seconds=(
                        int(unknown_ttl_seconds) if unknown_ttl_seconds is not None else 5 * 60
                    ),
                )
        else:
            rdap_client = rdap_client_override

        manager = JobManager(rdap_client=rdap_client, concurrency=concurrency)

        for raw_pattern in pattern_list:
            if stop_requested is not None and stop_requested():
                break
            normalized_pattern: Optional[str] = None
            output_files: List[str] = []
            status = "failed"
            counts: Dict[str, int] = {}
            error_text: Optional[str] = None
            job_id: Optional[str] = None

            try:
                normalized_pattern = validate_pattern(raw_pattern)
                wildcard_count = normalized_pattern.count("*")
                pattern_secondary_words = secondary_words if wildcard_count >= 2 and secondary_words is not None else None
                total_candidates = _validate_limit_counts(normalized_pattern, primary_words, pattern_secondary_words)

                if dry_run:
                    status = "completed"
                    counts = {
                        "total_candidates": total_candidates,
                        "valid_domains": 0,
                        "processed": 0,
                        "progress_processed": 0,
                        "available_count": 0,
                        "taken_count": 0,
                        "unknown_count": 0,
                        "invalid_count": 0,
                        "duplicate_count": 0,
                        "cache_hits": 0,
                        "cache_misses": 0,
                    }
                    error_text = "Dry run: no RDAP checks executed."
                    pattern_summary = PatternRunSummary(
                        pattern=raw_pattern,
                        normalized_pattern=normalized_pattern,
                        status=status,
                        output_files=[],
                        counts=counts,
                        error=error_text,
                        job_id=None,
                    )
                    results.append(pattern_summary)
                    continue

                job = manager.create_job(
                    normalized_pattern,
                    primary_words,
                    secondary_words=pattern_secondary_words,
                    force_recheck=force_recheck,
                )
                job_id = job.id
                if stop_requested is None:
                    snapshot = await _wait_for_job(job, normalized_pattern, progress_callback)
                else:
                    snapshot = await _wait_for_job_with_stop(
                        manager,
                        job,
                        normalized_pattern,
                        progress_callback,
                        stop_requested,
                    )
                status = str(snapshot.get("status", "failed"))
                counts = _snapshot_counts(snapshot)
                if status in {"completed", "cancelled"} and (status == "completed" or export_partial_on_cancel):
                    for fmt in selected_formats:
                        content = _render_export_content(manager, job, fmt)
                        filename = build_export_filename(normalized_pattern, fmt, wildcard_token=wildcard_token)
                        final_path = ensure_unique_path(output_dir / filename)
                        final_path.write_text(content, encoding="utf-8")
                        output_files.append(str(final_path))
                    if status == "cancelled":
                        error_text = "Cancelled by user request."
                else:
                    errors = snapshot.get("errors") or []
                    if isinstance(errors, list) and errors:
                        error_text = str(errors[0])
                    else:
                        error_text = f"Pattern run ended with status '{status}'"
            except (PatternValidationError, ValueError) as exc:
                status = "failed"
                error_text = str(exc)
            except Exception as exc:  # pragma: no cover - defensive path
                status = "failed"
                error_text = str(exc)

            if not counts:
                counts = {
                    "total_candidates": 0,
                    "valid_domains": 0,
                    "processed": 0,
                    "progress_processed": 0,
                    "available_count": 0,
                    "taken_count": 0,
                    "unknown_count": 0,
                    "invalid_count": 0,
                    "duplicate_count": 0,
                    "cache_hits": 0,
                    "cache_misses": 0,
                }

            pattern_summary = PatternRunSummary(
                pattern=raw_pattern,
                normalized_pattern=normalized_pattern,
                status=status if status in {"completed", "failed", "cancelled"} else "failed",
                output_files=output_files,
                counts=counts,
                error=error_text,
                job_id=job_id,
            )
            results.append(pattern_summary)

            if pattern_summary.status != "completed" and fail_fast:
                break
    finally:
        if http_client_ipv4 is not None:
            await http_client_ipv4.aclose()
        if http_client is not None:
            await http_client.aclose()
        if cache is not None:
            await cache.close()

    summary_data = BatchRunSummary(
        started_at=started_at,
        finished_at=datetime.now().astimezone().isoformat(),
        output_dir=str(output_dir),
        formats=selected_formats,
        force_recheck=bool(force_recheck),
        concurrency=int(concurrency),
        fail_fast=bool(fail_fast),
        summary_path="",
        patterns=results,
    )

    summary_path = ensure_unique_path(output_dir / build_summary_filename())
    summary_data.summary_path = str(summary_path)
    summary_path.write_text(json.dumps(summary_data.to_dict(), indent=2), encoding="utf-8")
    return summary_data
