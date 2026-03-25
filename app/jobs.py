from __future__ import annotations

import asyncio
import csv
import inspect
import io
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from app.models import DomainResult, Job
from app.patterns import estimate_total_candidates, iter_expanded_pattern
from app.rdap import DomainValidationError, RDAPClient, normalize_domain

VALID_SORT_MODES = {"earliest", "recent", "az", "za", "len_asc", "len_desc"}


class JobManager:
    def __init__(self, rdap_client: RDAPClient, concurrency: int = 32):
        self.rdap_client = rdap_client
        self.concurrency = concurrency
        self._jobs: Dict[str, Job] = {}
        self._tasks: Dict[str, asyncio.Task] = {}
        try:
            signature = inspect.signature(rdap_client.check_domain)
            self._check_domain_supports_force = "force_recheck" in signature.parameters
        except (AttributeError, TypeError, ValueError):
            self._check_domain_supports_force = False

    def create_job(
        self,
        pattern: str,
        words: Iterable[str],
        secondary_words: Iterable[str] | None = None,
        force_recheck: bool = False,
    ) -> Job:
        job_id = str(uuid.uuid4())
        job = Job(
            id=job_id,
            pattern=pattern,
            force_recheck=bool(force_recheck),
            event_queue=asyncio.Queue(),
            done_event=asyncio.Event(),
        )
        self._jobs[job_id] = job
        task = asyncio.create_task(self._run_job(job, list(words), list(secondary_words) if secondary_words is not None else None))
        self._tasks[job_id] = task
        return job

    def get_job(self, job_id: str) -> Optional[Job]:
        return self._jobs.get(job_id)

    async def cancel_job(self, job_id: str) -> bool:
        job = self._jobs.get(job_id)
        if not job:
            return False

        if job.status in {"completed", "failed", "cancelled"}:
            return False

        task = self._tasks.get(job_id)
        if task and not task.done():
            task.cancel()
        job.status = "cancelled"
        if not job.ended_at:
            job.ended_at = datetime.now(timezone.utc)
        await self._emit(job, "cancelled", {"job_id": job.id})
        job.done_event.set()
        return True

    async def _emit(self, job: Job, event_type: str, payload: Dict) -> None:
        event = {"type": event_type, **payload}
        await job.event_queue.put(event)

    async def _run_job(self, job: Job, words: List[str], secondary_words: List[str] | None = None) -> None:
        producer_task: Optional[asyncio.Task] = None
        worker_tasks: List[asyncio.Task] = []
        work_queue: Optional[asyncio.Queue] = None
        result_queue: Optional[asyncio.Queue] = None
        progress_emit_every = max(10, self.concurrency)
        progress_emit_min_interval_seconds = 0.2
        last_progress_emit_time = 0.0
        last_progress_emit_processed = -1
        available_emit_batch_size = max(10, self.concurrency)
        available_emit_min_interval_seconds = 0.2
        available_emit_buffer: List[Dict[str, Any]] = []
        last_available_emit_time = 0.0

        async def emit_progress(force: bool = False) -> None:
            nonlocal last_progress_emit_time, last_progress_emit_processed
            progress_processed = job.processed + job.invalid_count + job.duplicate_count
            if not force:
                now = time.monotonic()
                if progress_processed == last_progress_emit_processed:
                    return
                if (
                    progress_processed % progress_emit_every != 0
                    and (now - last_progress_emit_time) < progress_emit_min_interval_seconds
                ):
                    return
                last_progress_emit_time = now
                last_progress_emit_processed = progress_processed
            else:
                last_progress_emit_time = time.monotonic()
                last_progress_emit_processed = progress_processed

            await self._emit(
                job,
                "progress",
                {
                    "job_id": job.id,
                    "processed": job.processed,
                    "progress_processed": progress_processed,
                    "total": job.total_candidates,
                    "total_candidates": job.total_candidates,
                    "valid_domains": job.valid_domains,
                    "available_count": job.available_count,
                    "taken_count": job.taken_count,
                    "unknown_count": job.unknown_count,
                    "invalid_count": job.invalid_count,
                    "duplicate_count": job.duplicate_count,
                    "cache_hits": job.cache_hits,
                    "cache_misses": job.cache_misses,
                },
            )

        async def emit_available_batch(force: bool = False) -> None:
            nonlocal last_available_emit_time
            if not available_emit_buffer:
                return
            now = time.monotonic()
            if not force:
                if (
                    len(available_emit_buffer) < available_emit_batch_size
                    and (now - last_available_emit_time) < available_emit_min_interval_seconds
                ):
                    return
            payload = {"job_id": job.id, "results": list(available_emit_buffer)}
            available_emit_buffer.clear()
            last_available_emit_time = now
            await self._emit(job, "available_batch", payload)
        try:
            if job.status == "cancelled":
                if not job.ended_at:
                    job.ended_at = datetime.now(timezone.utc)
                if not job.done_event.is_set():
                    await self._emit(job, "cancelled", {"job_id": job.id})
                return

            job.status = "running"
            job.started_at = datetime.now(timezone.utc)
            await self._emit(job, "started", {"job_id": job.id})

            job.total_candidates = estimate_total_candidates(
                job.pattern,
                len(words),
                len(secondary_words) if secondary_words is not None else None,
            )
            await emit_progress(force=True)

            queue_size = max(100, self.concurrency * 4)
            work_queue = asyncio.Queue(maxsize=queue_size)
            result_queue = asyncio.Queue(maxsize=queue_size)
            semaphore = asyncio.Semaphore(self.concurrency)
            seen: set[str] = set()

            async def producer() -> None:
                try:
                    for candidate in iter_expanded_pattern(job.pattern, words, secondary_words=secondary_words):
                        try:
                            normalized = normalize_domain(candidate)
                        except DomainValidationError:
                            job.invalid_count += 1
                            continue
                        if normalized in seen:
                            job.duplicate_count += 1
                            continue
                        seen.add(normalized)
                        job.valid_domains += 1
                        await work_queue.put(normalized)
                finally:
                    for _ in range(self.concurrency):
                        await work_queue.put(None)

            async def run_one(domain: str) -> DomainResult:
                async with semaphore:
                    if self._check_domain_supports_force:
                        return await self.rdap_client.check_domain(domain, force_recheck=job.force_recheck)
                    return await self.rdap_client.check_domain(domain)

            async def worker() -> None:
                while True:
                    domain = await work_queue.get()
                    try:
                        if domain is None:
                            return
                        try:
                            result = await run_one(domain)
                        except Exception as exc:  # pragma: no cover - defensive path
                            result = DomainResult(
                                domain=domain,
                                state="unknown",
                                error=f"Worker failure: {exc}",
                                source="worker:error",
                            )
                        await result_queue.put(result)
                    finally:
                        work_queue.task_done()

            producer_task = asyncio.create_task(producer())
            worker_tasks = [asyncio.create_task(worker()) for _ in range(self.concurrency)]

            while True:
                workers_done = all(task.done() for task in worker_tasks)
                if producer_task.done() and workers_done and result_queue.empty():
                    break

                try:
                    result = await asyncio.wait_for(result_queue.get(), timeout=0.25)
                except asyncio.TimeoutError:
                    continue

                job.processed += 1
                if result.from_cache:
                    job.cache_hits += 1
                else:
                    job.cache_misses += 1
                if result.state == "available":
                    job.available_count += 1
                    job.available_domains.append(result.domain)
                    result_data = result.to_dict()
                    job.available_results.append(result_data)
                    available_emit_buffer.append(result_data)
                    await emit_available_batch(force=False)
                elif result.state == "taken":
                    job.taken_count += 1
                else:
                    job.unknown_count += 1
                    if len(job.recent_unknowns) < 100:
                        job.recent_unknowns.append(result.to_dict())
                await emit_progress(force=False)
                result_queue.task_done()

            await work_queue.join()
            await result_queue.join()
            await asyncio.gather(*worker_tasks)
            await producer_task
            await emit_available_batch(force=True)
            await emit_progress(force=True)

            job.status = "completed"
            job.ended_at = datetime.now(timezone.utc)
            await self._emit(job, "completed", {"job_id": job.id})
        except asyncio.CancelledError:
            if producer_task and not producer_task.done():
                producer_task.cancel()
            for task in worker_tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*(worker_tasks + ([producer_task] if producer_task else [])), return_exceptions=True)
            await emit_available_batch(force=True)
            await emit_progress(force=True)
            job.status = "cancelled"
            if not job.ended_at:
                job.ended_at = datetime.now(timezone.utc)
            if not job.done_event.is_set():
                await self._emit(job, "cancelled", {"job_id": job.id})
        except Exception as exc:  # pragma: no cover - defensive path
            await emit_available_batch(force=True)
            await emit_progress(force=True)
            job.status = "failed"
            job.errors.append(str(exc))
            job.ended_at = datetime.now(timezone.utc)
            await self._emit(job, "failed", {"job_id": job.id, "error": str(exc)})
        finally:
            job.done_event.set()
            self._tasks.pop(job.id, None)

    def _get_available_records(self, job: Job) -> List[Dict[str, Any]]:
        if job.available_results:
            return [dict(item) for item in job.available_results]
        return [{"domain": domain, "state": "available"} for domain in job.available_domains]

    def get_available_records_view(self, job: Job, sort_mode: str = "earliest", query: str = "") -> List[Dict[str, Any]]:
        mode = (sort_mode or "earliest").strip().lower()
        if mode not in VALID_SORT_MODES:
            raise ValueError(f"Unsupported sort mode: {sort_mode}")

        query_text = (query or "").strip().lower()
        records = self._get_available_records(job)
        if query_text:
            records = [record for record in records if query_text in str(record.get("domain", "")).lower()]

        if mode == "az":
            return sorted(records, key=lambda record: str(record.get("domain", "")))
        if mode == "za":
            return sorted(records, key=lambda record: str(record.get("domain", "")), reverse=True)
        if mode == "len_asc":
            return sorted(
                records,
                key=lambda record: (len(str(record.get("domain", ""))), str(record.get("domain", ""))),
            )
        if mode == "len_desc":
            return sorted(
                records,
                key=lambda record: (-len(str(record.get("domain", ""))), str(record.get("domain", ""))),
            )
        if mode == "recent":
            return list(reversed(records))
        return list(records)

    def get_available_view(self, job: Job, sort_mode: str = "earliest", query: str = "") -> List[str]:
        records = self.get_available_records_view(job, sort_mode=sort_mode, query=query)
        return [str(record.get("domain", "")) for record in records]

    def export_available_txt(self, job: Job, sort_mode: str = "earliest", query: str = "") -> str:
        view_records = self.get_available_records_view(job, sort_mode=sort_mode, query=query)
        lines = [f"{record.get('domain', '')}\n" for record in view_records]
        return "".join(lines)

    def export_available_csv(self, job: Job, sort_mode: str = "earliest", query: str = "") -> str:
        view_records = self.get_available_records_view(job, sort_mode=sort_mode, query=query)
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["domain", "state", "source", "checked_at", "ttl_seconds", "expires_at", "from_cache"])
        for record in view_records:
            writer.writerow(
                [
                    record.get("domain", ""),
                    record.get("state", "available"),
                    record.get("source", ""),
                    record.get("checked_at", ""),
                    record.get("ttl_seconds", ""),
                    record.get("expires_at", ""),
                    record.get("from_cache", False),
                ]
            )
        return output.getvalue()

    def export_available_json(self, job: Job, sort_mode: str = "earliest", query: str = "") -> List[Dict[str, Any]]:
        return self.get_available_records_view(job, sort_mode=sort_mode, query=query)
