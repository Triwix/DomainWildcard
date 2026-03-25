#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic
from typing import List, Optional, Sequence, Tuple
from urllib.parse import quote

import httpx

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.bootstrap import RDAPBootstrapResolver
from app.rate_calibration import StageMetrics, choose_winning_interval, evaluate_stage


DEFAULT_INTERVALS: Tuple[float, ...] = (0.50, 0.40, 0.33, 0.25, 0.20, 0.167)
DEFAULT_DURATIONS = {"warmup": 45.0, "stage": 120.0, "validation": 180.0}
SHORT_DURATIONS = {"warmup": 10.0, "stage": 30.0, "validation": 45.0}
FORCE_IPV4_HOST_CONTAINS = ("rdap.identitydigital.services",)


def parse_stage_intervals(raw: str) -> List[float]:
    values: List[float] = []
    for token in str(raw or "").split(","):
        piece = token.strip()
        if not piece:
            continue
        value = float(piece)
        if value <= 0:
            raise ValueError("Stage intervals must be greater than zero.")
        values.append(value)
    if not values:
        raise ValueError("At least one stage interval is required.")
    return values


def choose_durations(
    profile: str,
    warmup_override: Optional[float],
    stage_override: Optional[float],
    validation_override: Optional[float],
) -> Tuple[float, float, float]:
    base = DEFAULT_DURATIONS if profile == "default" else SHORT_DURATIONS
    warmup = float(base["warmup"] if warmup_override is None else warmup_override)
    stage = float(base["stage"] if stage_override is None else stage_override)
    validation = float(base["validation"] if validation_override is None else validation_override)
    return warmup, stage, validation


def percentile(values: Sequence[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = max(0, min(len(ordered) - 1, math.ceil((len(ordered) * fraction)) - 1))
    return float(ordered[idx])


def build_candidate(tld: str, run_token: str, index: int) -> str:
    compact = f"{run_token}{index:08x}"
    label = f"zzbench{compact}"[:63]
    return f"{label}.{tld}"


async def resolve_base_url(client: httpx.AsyncClient, resolver: RDAPBootstrapResolver, tld: str) -> str:
    await resolver.ensure_loaded(client)
    probe = f"probe-{uuid.uuid4().hex[:12]}.{tld}"
    base_url = resolver.resolve_base_url(probe)
    if not base_url:
        raise RuntimeError(f"No RDAP base URL found for .{tld}")
    return base_url.rstrip("/")


def should_force_ipv4_for_host(host: str) -> bool:
    lowered = str(host or "").lower()
    return any(piece in lowered for piece in FORCE_IPV4_HOST_CONTAINS)


def classify_status(metrics: StageMetrics, status: int) -> None:
    if status == 200:
        metrics.status_200 += 1
    elif status == 404:
        metrics.status_404 += 1
    elif status == 429:
        metrics.status_429 += 1
    elif 500 <= status <= 599:
        metrics.status_5xx += 1
    else:
        metrics.other_status += 1


async def run_stage(
    client: httpx.AsyncClient,
    base_url: str,
    tld: str,
    run_token: str,
    start_index: int,
    stage_name: str,
    interval_seconds: float,
    duration_seconds: float,
    timeout_seconds: float,
    user_agent: Optional[str],
    max_requests_remaining: Optional[int],
) -> Tuple[StageMetrics, int]:
    metrics = StageMetrics(
        name=stage_name,
        interval_seconds=interval_seconds,
        duration_seconds=duration_seconds,
    )
    latencies_ms: List[float] = []
    requests_sent = 0
    start = monotonic()
    next_allowed = start

    while True:
        now = monotonic()
        if now >= start + duration_seconds:
            break
        if max_requests_remaining is not None and requests_sent >= max_requests_remaining:
            break
        sleep_for = next_allowed - now
        if sleep_for > 0:
            await asyncio.sleep(sleep_for)

        request_started = monotonic()
        candidate = build_candidate(tld, run_token, start_index + requests_sent)
        endpoint = f"{base_url}/domain/{quote(candidate, safe='.-')}"
        metrics.total_requests += 1
        requests_sent += 1
        headers = {"Accept": "application/rdap+json, application/json"}
        if str(user_agent or "").strip():
            headers["User-Agent"] = str(user_agent).strip()
        try:
            response = await client.get(
                endpoint,
                headers=headers,
                follow_redirects=True,
                timeout=timeout_seconds,
            )
            classify_status(metrics, response.status_code)
        except (httpx.TimeoutException, httpx.TransportError):
            metrics.transport_errors += 1
        finally:
            elapsed_ms = (monotonic() - request_started) * 1000.0
            latencies_ms.append(elapsed_ms)
            next_allowed = request_started + interval_seconds

    metrics.elapsed_seconds = max(0.001, monotonic() - start)
    metrics.latency_p95_ms = percentile(latencies_ms, 0.95)
    return metrics, requests_sent


def render_row(metrics: StageMetrics, pass_state: Optional[Tuple[bool, str]] = None) -> str:
    effective = metrics.effective_rps()
    status = "-"
    reason = "-"
    if pass_state is not None:
        status = "PASS" if pass_state[0] else "FAIL"
        reason = pass_state[1]
    return (
        f"{metrics.name:>13} | "
        f"{metrics.interval_seconds:>7.3f}s | "
        f"{metrics.total_requests:>5} | "
        f"{metrics.status_200:>4} | "
        f"{metrics.status_404:>4} | "
        f"{metrics.status_429:>4} | "
        f"{metrics.status_5xx:>4} | "
        f"{metrics.other_status:>5} | "
        f"{metrics.transport_errors:>6} | "
        f"{effective:>6.2f} | "
        f"{metrics.latency_p95_ms:>8.1f} | "
        f"{status:>4} | {reason}"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Calibrate safe RDAP request interval for a TLD host.")
    parser.add_argument("--tld", default="com", help="Target TLD suffix (default: com).")
    parser.add_argument(
        "--durations",
        choices=["default", "short"],
        default="default",
        help="Duration profile. default=45/120/180, short=10/30/45 (warmup/stage/validation).",
    )
    parser.add_argument("--warmup-interval", type=float, default=1.0, help="Warmup interval in seconds.")
    parser.add_argument("--warmup-duration", type=float, default=None, help="Warmup duration override in seconds.")
    parser.add_argument("--stage-duration", type=float, default=None, help="Per-stage duration override in seconds.")
    parser.add_argument("--validation-duration", type=float, default=None, help="Validation duration override in seconds.")
    parser.add_argument(
        "--stage-intervals",
        default=",".join(str(v) for v in DEFAULT_INTERVALS),
        help="Comma-separated stage intervals in seconds.",
    )
    parser.add_argument(
        "--max-error-rate",
        type=float,
        default=0.005,
        help="Allowed (5xx + transport errors) rate before failing a stage.",
    )
    parser.add_argument("--timeout", type=float, default=20.0, help="HTTP timeout per request.")
    parser.add_argument("--max-requests", type=int, default=None, help="Optional global max request safety cap.")
    parser.add_argument("--json-out", default=None, help="Optional path to JSON report.")
    parser.add_argument(
        "--user-agent",
        default="",
        help="Optional User-Agent override. Blank uses the same default header behavior as the app/httpx client.",
    )
    return parser


async def run_calibration(args: argparse.Namespace) -> int:
    tld = str(args.tld).strip().lower().lstrip(".")
    if not tld:
        raise ValueError("TLD must not be empty.")
    stage_intervals = parse_stage_intervals(args.stage_intervals)
    warmup_duration, stage_duration, validation_duration = choose_durations(
        args.durations,
        args.warmup_duration,
        args.stage_duration,
        args.validation_duration,
    )
    warmup_interval = float(args.warmup_interval)
    if warmup_interval <= 0:
        raise ValueError("Warmup interval must be greater than zero.")
    if any(d <= 0 for d in (warmup_duration, stage_duration, validation_duration)):
        raise ValueError("Durations must be greater than zero.")

    print("RDAP Safe-Speed Calibration")
    print(f"Target TLD: .{tld}")
    print(f"Duration profile: warmup={warmup_duration}s, stage={stage_duration}s, validation={validation_duration}s")
    print(f"Stage intervals (s): {', '.join(f'{v:.3f}' for v in stage_intervals)}")
    print(f"Policy: zero 429, max instability rate {args.max_error_rate:.3%}")

    report = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "tld": tld,
        "stage_intervals": stage_intervals,
        "max_error_rate": args.max_error_rate,
        "durations": {
            "warmup_interval_seconds": warmup_interval,
            "warmup_duration_seconds": warmup_duration,
            "stage_duration_seconds": stage_duration,
            "validation_duration_seconds": validation_duration,
        },
        "base_url": None,
        "host": None,
        "warmup": None,
        "stages": [],
        "validation": None,
        "decision": None,
    }

    request_index = 0
    remaining = int(args.max_requests) if args.max_requests and args.max_requests > 0 else None
    run_token = uuid.uuid4().hex[:12]

    resolver = RDAPBootstrapResolver()
    async with httpx.AsyncClient() as bootstrap_client:
        base_url = await resolve_base_url(bootstrap_client, resolver, tld)
    host = httpx.URL(base_url).host or base_url
    report["base_url"] = base_url
    report["host"] = host
    force_ipv4 = should_force_ipv4_for_host(host)
    if force_ipv4:
        print(f"Resolved RDAP base: {base_url} (forcing IPv4)")
    else:
        print(f"Resolved RDAP base: {base_url}")

    run_client_kwargs = (
        {"transport": httpx.AsyncHTTPTransport(local_address="0.0.0.0")}
        if force_ipv4
        else {}
    )
    async with httpx.AsyncClient(**run_client_kwargs) as client:
        header = (
            "        Stage | Interval |   Req | 200  | 404  | 429  | 5xx  | Other | Errors |    RPS |   P95ms | Stat | Reason"
        )
        print(header)
        print("-" * len(header))

        warmup_name = f"warmup-{warmup_interval:.3f}s"
        warmup_metrics, used = await run_stage(
            client=client,
            base_url=base_url,
            tld=tld,
            run_token=run_token,
            start_index=request_index,
            stage_name=warmup_name,
            interval_seconds=warmup_interval,
            duration_seconds=warmup_duration,
            timeout_seconds=float(args.timeout),
            user_agent=args.user_agent,
            max_requests_remaining=remaining,
        )
        request_index += used
        if remaining is not None:
            remaining -= used
        report["warmup"] = asdict(warmup_metrics)
        print(render_row(warmup_metrics))

        stage_metrics_list: List[StageMetrics] = []
        for interval in stage_intervals:
            if remaining is not None and remaining <= 0:
                print("Max request cap reached before finishing all stages.")
                break

            stage_name = f"stage-{interval:.3f}s"
            metrics, used = await run_stage(
                client=client,
                base_url=base_url,
                tld=tld,
                run_token=run_token,
                start_index=request_index,
                stage_name=stage_name,
                interval_seconds=interval,
                duration_seconds=stage_duration,
                timeout_seconds=float(args.timeout),
                user_agent=args.user_agent,
                max_requests_remaining=remaining,
            )
            request_index += used
            if remaining is not None:
                remaining -= used

            verdict = evaluate_stage(metrics, max_error_rate=float(args.max_error_rate))
            stage_metrics_list.append(metrics)
            report["stages"].append({**asdict(metrics), "passed": verdict[0], "reason": verdict[1]})
            print(render_row(metrics, verdict))
            if not verdict[0]:
                print(f"Stopping escalation at first failed stage: {stage_name}")
                break

        decision = choose_winning_interval(
            stage_metrics_list,
            validation_result=None,
            max_error_rate=float(args.max_error_rate),
        )
        validation_metrics: Optional[StageMetrics] = None

        if decision.winning_interval_seconds is not None and not (remaining is not None and remaining <= 0):
            validation_name = f"validation-{decision.winning_interval_seconds:.3f}s"
            validation_metrics, used = await run_stage(
                client=client,
                base_url=base_url,
                tld=tld,
                run_token=run_token,
                start_index=request_index,
                stage_name=validation_name,
                interval_seconds=float(decision.winning_interval_seconds),
                duration_seconds=validation_duration,
                timeout_seconds=float(args.timeout),
                user_agent=args.user_agent,
                max_requests_remaining=remaining,
            )
            request_index += used
            if remaining is not None:
                remaining -= used

            validation_verdict = evaluate_stage(validation_metrics, max_error_rate=float(args.max_error_rate))
            print(render_row(validation_metrics, validation_verdict))
            report["validation"] = {
                **asdict(validation_metrics),
                "passed": validation_verdict[0],
                "reason": validation_verdict[1],
            }
            decision = choose_winning_interval(
                stage_metrics_list,
                validation_result=validation_metrics,
                max_error_rate=float(args.max_error_rate),
            )

        report["decision"] = {
            "winning_interval_seconds": decision.winning_interval_seconds,
            "winning_stage_name": decision.winning_stage_name,
            "reason": decision.reason,
            "total_requests": request_index,
        }

    print("")
    print("Decision")
    print(f"- Result: {decision.reason}")
    if decision.winning_interval_seconds is None:
        print("- Recommended min interval: none (no safe stage found)")
        exit_code = 2
    else:
        print(f"- Recommended min interval: {decision.winning_interval_seconds:.3f}s")
        exit_code = 0

    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    if args.json_out:
        path = Path(args.json_out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"- JSON report written to: {path}")

    return exit_code


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return asyncio.run(run_calibration(args))
    except KeyboardInterrupt:
        print("\nInterrupted.")
        return 130
    except Exception as exc:  # pragma: no cover - CLI guard
        print(f"Calibration failed: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
