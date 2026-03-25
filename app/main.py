from __future__ import annotations

import asyncio
import json
import os
import re
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional

import httpx
from fastapi import Body, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from app.bootstrap import RDAPBootstrapResolver
from app.jobs import JobManager
from app.patterns import PatternValidationError, estimate_total_candidates, validate_pattern
from app.rdap import DEFAULT_KNOWN_POLICIES, RDAPClient
from app.result_cache import DomainResultCache
from app.wordlist import parse_wordlist_bytes

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
CACHE_DB_PATH = BASE_DIR.parent / "data" / "rdap_cache.sqlite3"
MAX_WORDLIST_WORDS = 50000
MAX_UPLOAD_BYTES = 8 * 1024 * 1024
MAX_TOTAL_CANDIDATES = 1_000_000
_FILENAME_NON_ALLOWED_RE = re.compile(r"[^a-z0-9.-]+")
_FILENAME_MULTI_DASH_RE = re.compile(r"-{2,}")
_WILDCARD_TOKEN_NON_ALLOWED_RE = re.compile(r"[^a-z0-9-]+")
MANUAL_RATE_HOST_KEYS = tuple(sorted({policy.host_contains for policy in DEFAULT_KNOWN_POLICIES}))
STATIC_CACHE_SECONDS = max(0, int(os.getenv("DOMAIN_SEARCH_STATIC_MAX_AGE_SECONDS", "0")))


class CachedStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        if response.status_code == 200:
            if STATIC_CACHE_SECONDS > 0:
                response.headers.setdefault("Cache-Control", f"public, max-age={STATIC_CACHE_SECONDS}")
            else:
                response.headers.setdefault("Cache-Control", "no-cache, no-store, must-revalidate")
                response.headers.setdefault("Pragma", "no-cache")
                response.headers.setdefault("Expires", "0")
        return response


async def _read_upload_limited(upload: UploadFile, max_bytes: int) -> bytes:
    chunks: list[bytes] = []
    total = 0

    while True:
        chunk = await upload.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(status_code=413, detail=f"Upload exceeds {max_bytes} bytes")
        chunks.append(chunk)

    return b"".join(chunks)


def _normalize_wildcard_token(token: Optional[str]) -> str:
    value = str(token or "w").strip().lower()
    value = _WILDCARD_TOKEN_NON_ALLOWED_RE.sub("", value).strip("-")
    return value or "w"


def _sanitize_pattern_for_filename(pattern: str, wildcard_token: Optional[str] = "w") -> str:
    token = _normalize_wildcard_token(wildcard_token)
    sanitized = str(pattern or "").strip().lower().replace("*", token)
    sanitized = _FILENAME_NON_ALLOWED_RE.sub("-", sanitized)
    sanitized = _FILENAME_MULTI_DASH_RE.sub("-", sanitized)
    sanitized = sanitized.strip("-.")
    return sanitized or "search"


def _build_export_filename(pattern: str, extension: str, wildcard_token: Optional[str] = "w") -> str:
    pattern_part = _sanitize_pattern_for_filename(pattern, wildcard_token=wildcard_token)
    timestamp = datetime.now().astimezone().strftime("%Y-%m-%d-%H-%M-%S")
    ext = str(extension or "").strip().lower().lstrip(".")
    return f"{pattern_part}-{timestamp}.{ext}"

@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.http_client = httpx.AsyncClient()
    app.state.http_client_ipv4 = httpx.AsyncClient(
        transport=httpx.AsyncHTTPTransport(local_address="0.0.0.0")
    )
    resolver = RDAPBootstrapResolver()
    result_cache = DomainResultCache(CACHE_DB_PATH)
    rdap_client = RDAPClient(
        http_client=app.state.http_client,
        resolver=resolver,
        ipv4_http_client=app.state.http_client_ipv4,
        result_cache=result_cache,
    )
    app.state.jobs = JobManager(rdap_client=rdap_client)
    app.state.result_cache = result_cache
    try:
        yield
    finally:
        await app.state.http_client_ipv4.aclose()
        await app.state.http_client.aclose()
        await app.state.result_cache.close()


app = FastAPI(title="Domain Wildcard Availability Checker", lifespan=lifespan)
app.add_middleware(GZipMiddleware, minimum_size=1024)
app.mount("/static", CachedStaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(
        STATIC_DIR / "index.html",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


@app.post("/api/jobs")
async def create_job(
    pattern: str = Form(...),
    wordlist: UploadFile = File(...),
    wordlist_secondary: Optional[UploadFile] = File(default=None),
    force_recheck: bool = Form(default=False),
):
    try:
        validated_pattern = validate_pattern(pattern)
    except PatternValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    if not wordlist.filename:
        raise HTTPException(status_code=400, detail="Wordlist file is required")

    content = await _read_upload_limited(wordlist, MAX_UPLOAD_BYTES)
    words = parse_wordlist_bytes(content)
    if not words:
        raise HTTPException(status_code=400, detail="Wordlist is empty after parsing")
    if len(words) > MAX_WORDLIST_WORDS:
        raise HTTPException(status_code=400, detail=f"Wordlist exceeds the current max of {MAX_WORDLIST_WORDS} words")

    secondary_words = None
    if validated_pattern.count("*") >= 2 and wordlist_secondary and wordlist_secondary.filename:
        secondary_content = await _read_upload_limited(wordlist_secondary, MAX_UPLOAD_BYTES)
        secondary_words = parse_wordlist_bytes(secondary_content)
        if not secondary_words:
            raise HTTPException(status_code=400, detail="Secondary wordlist is empty after parsing")
        if len(secondary_words) > MAX_WORDLIST_WORDS:
            raise HTTPException(status_code=400, detail=f"Secondary wordlist exceeds the current max of {MAX_WORDLIST_WORDS} words")

    total_candidates = estimate_total_candidates(
        validated_pattern,
        len(words),
        len(secondary_words) if secondary_words is not None else None,
    )
    if total_candidates > MAX_TOTAL_CANDIDATES:
        raise HTTPException(
            status_code=400,
            detail=f"Expanded candidate count ({total_candidates}) exceeds current max of {MAX_TOTAL_CANDIDATES}",
        )

    job = app.state.jobs.create_job(
        validated_pattern,
        words,
        secondary_words=secondary_words,
        force_recheck=force_recheck,
    )
    return {"job_id": job.id}


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str):
    job = app.state.jobs.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job.snapshot()


@app.get("/api/rate-status")
async def get_rate_status():
    rdap_client = app.state.jobs.rdap_client
    if hasattr(rdap_client, "get_rate_status"):
        return {"hosts": rdap_client.get_rate_status()}
    return {"hosts": []}


@app.get("/api/rate-config")
async def get_rate_config():
    rdap_client = app.state.jobs.rdap_client
    if hasattr(rdap_client, "get_rate_config"):
        config = rdap_client.get_rate_config()
        return {"supported_hosts": list(MANUAL_RATE_HOST_KEYS), **config}
    return {"supported_hosts": list(MANUAL_RATE_HOST_KEYS), "defaults": {}, "overrides": {}}


@app.post("/api/rate-config")
async def update_rate_config(payload: dict = Body(...)):
    rdap_client = app.state.jobs.rdap_client
    if not hasattr(rdap_client, "set_rate_overrides") or not hasattr(rdap_client, "get_rate_config"):
        raise HTTPException(status_code=501, detail="Rate configuration is not supported by current RDAP client.")

    overrides_raw = payload.get("overrides")
    if not isinstance(overrides_raw, dict):
        raise HTTPException(status_code=400, detail="Payload must include an 'overrides' object.")

    replace = bool(payload.get("replace", True))
    reset_backoff = bool(payload.get("reset_backoff", True))
    normalized_overrides: dict[str, float] = {}
    for raw_host, raw_interval in overrides_raw.items():
        host_key = str(raw_host or "").strip().lower()
        if not host_key:
            continue
        if host_key not in MANUAL_RATE_HOST_KEYS:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported host key '{host_key}'. Supported keys: {', '.join(MANUAL_RATE_HOST_KEYS)}",
            )
        try:
            interval_seconds = float(raw_interval)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail=f"Invalid interval for host '{host_key}'.")
        if interval_seconds <= 0:
            raise HTTPException(status_code=400, detail=f"Interval for host '{host_key}' must be > 0.")
        normalized_overrides[host_key] = interval_seconds

    await rdap_client.set_rate_overrides(
        normalized_overrides,
        replace=replace,
        reset_backoff=reset_backoff,
    )
    config = rdap_client.get_rate_config()
    return {"supported_hosts": list(MANUAL_RATE_HOST_KEYS), **config}


@app.delete("/api/rate-config")
async def clear_rate_config(reset_backoff: bool = Query(default=True)):
    rdap_client = app.state.jobs.rdap_client
    if not hasattr(rdap_client, "clear_rate_overrides") or not hasattr(rdap_client, "get_rate_config"):
        raise HTTPException(status_code=501, detail="Rate configuration is not supported by current RDAP client.")
    await rdap_client.clear_rate_overrides(reset_backoff=bool(reset_backoff))
    config = rdap_client.get_rate_config()
    return {"supported_hosts": list(MANUAL_RATE_HOST_KEYS), **config}


@app.post("/api/jobs/{job_id}/cancel")
async def cancel_job(job_id: str):
    job = app.state.jobs.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    cancelled = await app.state.jobs.cancel_job(job_id)
    if not cancelled:
        raise HTTPException(status_code=409, detail=f"Job is already {job.status}")

    return {"job_id": job_id, "status": "cancelled"}


def _sse_message(event_name: str, payload: dict) -> str:
    encoded = json.dumps(payload, separators=(",", ":"))
    return f"event: {event_name}\ndata: {encoded}\n\n"


@app.get("/api/jobs/{job_id}/events")
async def stream_job_events(job_id: str, request: Request):
    job = app.state.jobs.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    async def event_generator():
        yield _sse_message("snapshot", job.snapshot())

        while True:
            if await request.is_disconnected():
                break

            try:
                event = await asyncio.wait_for(job.event_queue.get(), timeout=15)
                event_type = event.get("type", "message")
                yield _sse_message(event_type, event)
            except asyncio.TimeoutError:
                if job.done_event.is_set() and job.event_queue.empty():
                    terminal = job.status if job.status in {"completed", "failed", "cancelled"} else "completed"
                    yield _sse_message(terminal, {"job_id": job.id, "type": terminal})
                    break
                yield ": keep-alive\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.get("/api/jobs/{job_id}/export.txt")
async def export_txt(
    job_id: str,
    sort: str = Query(default="earliest"),
    q: str = Query(default=""),
    wildcard_token: str = Query(default="w"),
):
    job = app.state.jobs.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    try:
        txt = app.state.jobs.export_available_txt(job, sort_mode=sort, query=q)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    filename = _build_export_filename(job.pattern, "txt", wildcard_token=wildcard_token)
    return PlainTextResponse(
        txt,
        media_type="text/plain",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@app.get("/api/jobs/{job_id}/export.csv")
async def export_csv(
    job_id: str,
    sort: str = Query(default="earliest"),
    q: str = Query(default=""),
    wildcard_token: str = Query(default="w"),
):
    job = app.state.jobs.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    try:
        csv_text = app.state.jobs.export_available_csv(job, sort_mode=sort, query=q)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    filename = _build_export_filename(job.pattern, "csv", wildcard_token=wildcard_token)
    return PlainTextResponse(
        csv_text,
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@app.get("/api/jobs/{job_id}/export.json")
async def export_json(
    job_id: str,
    sort: str = Query(default="earliest"),
    q: str = Query(default=""),
    wildcard_token: str = Query(default="w"),
):
    job = app.state.jobs.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    try:
        payload = {"results": app.state.jobs.export_available_json(job, sort_mode=sort, query=q)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    filename = _build_export_filename(job.pattern, "json", wildcard_token=wildcard_token)
    return JSONResponse(
        payload,
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
