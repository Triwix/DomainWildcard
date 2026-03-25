#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic
from typing import Dict, List, Optional, Sequence, Tuple
from urllib.parse import quote

import httpx

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.bootstrap import RDAPBootstrapResolver
from app.rate_calibration import StageMetrics, choose_winning_interval, evaluate_stage


@dataclass(frozen=True)
class CalibrationTargetProfile:
    tld: str
    warmup_interval_seconds: float
    stage_intervals: Tuple[float, ...]
    policy_env: str


SUPPORTED_TARGETS: Dict[str, CalibrationTargetProfile] = {
    "com": CalibrationTargetProfile(
        tld="com",
        warmup_interval_seconds=0.020,
        stage_intervals=(0.010, 0.005, 0.0025, 0.0010, 0.0005, 0.00025, 0.0001),
        policy_env="RDAP_VERISIGN_MIN_INTERVAL_SECONDS",
    ),
    "net": CalibrationTargetProfile(
        tld="net",
        warmup_interval_seconds=0.020,
        stage_intervals=(0.010, 0.005, 0.0025, 0.0010, 0.0005, 0.00025, 0.0001),
        policy_env="RDAP_VERISIGN_MIN_INTERVAL_SECONDS",
    ),
    "org": CalibrationTargetProfile(
        tld="org",
        warmup_interval_seconds=0.050,
        stage_intervals=(0.020, 0.010, 0.005, 0.0025, 0.0015, 0.0010, 0.00075, 0.0005),
        policy_env="RDAP_PUBLICINTERESTREGISTRY_MIN_INTERVAL_SECONDS",
    ),
    "ai": CalibrationTargetProfile(
        tld="ai",
        warmup_interval_seconds=1.500,
        stage_intervals=(1.200, 1.000, 0.850, 0.750, 0.670, 0.600, 0.550, 0.500),
        policy_env="RDAP_IDENTITYDIGITAL_MIN_INTERVAL_SECONDS",
    ),
    "io": CalibrationTargetProfile(
        tld="io",
        warmup_interval_seconds=1.500,
        stage_intervals=(1.200, 1.000, 0.850, 0.750, 0.670, 0.600, 0.550, 0.500),
        policy_env="RDAP_IDENTITYDIGITAL_MIN_INTERVAL_SECONDS",
    ),
    "info": CalibrationTargetProfile(
        tld="info",
        warmup_interval_seconds=1.500,
        stage_intervals=(1.200, 1.000, 0.850, 0.750, 0.670, 0.600, 0.550, 0.500),
        policy_env="RDAP_IDENTITYDIGITAL_MIN_INTERVAL_SECONDS",
    ),
    "co": CalibrationTargetProfile(
        tld="co",
        warmup_interval_seconds=0.050,
        stage_intervals=(0.020, 0.015, 0.0125, 0.010, 0.0075, 0.0050),
        policy_env="RDAP_REGISTRY_CO_MIN_INTERVAL_SECONDS",
    ),
    "xyz": CalibrationTargetProfile(
        tld="xyz",
        warmup_interval_seconds=0.100,
        stage_intervals=(0.050, 0.033, 0.025, 0.020, 0.015, 0.0125, 0.010, 0.0075),
        policy_env="RDAP_CENTRALNIC_MIN_INTERVAL_SECONDS",
    ),
    "shop": CalibrationTargetProfile(
        tld="shop",
        warmup_interval_seconds=1.500,
        stage_intervals=(1.200, 1.000, 0.800, 0.670, 0.500, 0.400, 0.333, 0.250),
        policy_env="RDAP_GMOREGISTRY_MIN_INTERVAL_SECONDS",
    ),
    "store": CalibrationTargetProfile(
        tld="store",
        warmup_interval_seconds=0.750,
        stage_intervals=(0.500, 0.400, 0.330, 0.250, 0.200, 0.167, 0.143, 0.125),
        policy_env="RDAP_RADIX_MIN_INTERVAL_SECONDS",
    ),
    "online": CalibrationTargetProfile(
        tld="online",
        warmup_interval_seconds=0.750,
        stage_intervals=(0.500, 0.400, 0.330, 0.250, 0.200, 0.167, 0.143, 0.125),
        policy_env="RDAP_RADIX_MIN_INTERVAL_SECONDS",
    ),
    "de": CalibrationTargetProfile(
        tld="de",
        warmup_interval_seconds=0.100,
        stage_intervals=(0.050, 0.033, 0.025, 0.020, 0.015, 0.0125, 0.010, 0.0075),
        policy_env="RDAP_DENIC_MIN_INTERVAL_SECONDS",
    ),
    "uk": CalibrationTargetProfile(
        tld="uk",
        warmup_interval_seconds=0.100,
        stage_intervals=(0.050, 0.033, 0.025, 0.020, 0.015, 0.0125, 0.010, 0.0075),
        policy_env="RDAP_NOMINET_MIN_INTERVAL_SECONDS",
    ),
    "nl": CalibrationTargetProfile(
        tld="nl",
        warmup_interval_seconds=0.200,
        stage_intervals=(0.100, 0.070, 0.050, 0.040, 0.033, 0.025, 0.020, 0.015),
        policy_env="RDAP_SIDN_MIN_INTERVAL_SECONDS",
    ),
    "br": CalibrationTargetProfile(
        tld="br",
        warmup_interval_seconds=0.100,
        stage_intervals=(0.050, 0.033, 0.025, 0.020, 0.015, 0.0125, 0.010, 0.0075),
        policy_env="RDAP_REGISTRO_BR_MIN_INTERVAL_SECONDS",
    ),
    "au": CalibrationTargetProfile(
        tld="au",
        warmup_interval_seconds=1.000,
        stage_intervals=(0.750, 0.670, 0.500, 0.400, 0.333, 0.250, 0.200, 0.167),
        policy_env="RDAP_AU_MIN_INTERVAL_SECONDS",
    ),
}
FORCE_IPV4_TLDS = {"ai", "io", "info"}


def parse_stage_intervals(raw: str) -> Tuple[float, ...]:
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
    return tuple(values)


def percentile(values: Sequence[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = max(0, min(len(ordered) - 1, math.ceil((len(ordered) * fraction)) - 1))
    return float(ordered[idx])


def build_candidate(tld: str, run_token: str, index: int) -> str:
    compact = f"{run_token}{index:08x}"
    label = f"zzhostcal{compact}"[:63]
    return f"{label}.{tld}"


async def resolve_base_url(client: httpx.AsyncClient, resolver: RDAPBootstrapResolver, tld: str) -> str:
    await resolver.ensure_loaded(client)
    probe = f"probe-{uuid.uuid4().hex[:12]}.{tld}"
    base_url = resolver.resolve_base_url(probe)
    if not base_url:
        raise RuntimeError(f"No RDAP base URL found for .{tld}")
    return base_url.rstrip("/")


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


async def issue_request(
    client: httpx.AsyncClient,
    endpoint: str,
    timeout_seconds: float,
    user_agent: Optional[str],
) -> Tuple[Optional[int], float]:
    started = monotonic()
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
        return response.status_code, (monotonic() - started) * 1000.0
    except (httpx.TimeoutException, httpx.TransportError):
        return None, (monotonic() - started) * 1000.0


async def run_stage_concurrent(
    client: httpx.AsyncClient,
    base_url: str,
    tld: str,
    run_token: str,
    start_index: int,
    stage_name: str,
    interval_seconds: float,
    duration_seconds: float,
    timeout_seconds: float,
    user_agent: str,
    max_requests_remaining: Optional[int],
    max_in_flight: int,
) -> Tuple[StageMetrics, int]:
    metrics = StageMetrics(
        name=stage_name,
        interval_seconds=interval_seconds,
        duration_seconds=duration_seconds,
    )
    latencies_ms: List[float] = []
    requests_sent = 0
    started = monotonic()
    finished_dispatch = started + duration_seconds
    next_allowed = started
    in_flight: set[asyncio.Task[Tuple[Optional[int], float]]] = set()

    def collect_done(done: set[asyncio.Task[Tuple[Optional[int], float]]]) -> None:
        for task in done:
            status, latency_ms = task.result()
            latencies_ms.append(latency_ms)
            if status is None:
                metrics.transport_errors += 1
            else:
                classify_status(metrics, status)

    while True:
        now = monotonic()
        can_dispatch = (
            now < finished_dispatch
            and len(in_flight) < max_in_flight
            and (max_requests_remaining is None or requests_sent < max_requests_remaining)
            and now >= next_allowed
        )
        if can_dispatch:
            candidate = build_candidate(tld, run_token, start_index + requests_sent)
            endpoint = f"{base_url}/domain/{quote(candidate, safe='.-')}"
            dispatch_started = monotonic()
            metrics.total_requests += 1
            requests_sent += 1
            in_flight.add(
                asyncio.create_task(
                    issue_request(
                        client=client,
                        endpoint=endpoint,
                        timeout_seconds=timeout_seconds,
                        user_agent=user_agent,
                    )
                )
            )
            next_allowed = dispatch_started + interval_seconds
            continue

        if now >= finished_dispatch and not in_flight:
            break
        if max_requests_remaining is not None and requests_sent >= max_requests_remaining and not in_flight:
            break

        timeout: Optional[float] = None
        if now < finished_dispatch and len(in_flight) < max_in_flight:
            timeout = max(0.0, next_allowed - now)

        if in_flight:
            done, pending = await asyncio.wait(
                in_flight,
                timeout=timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )
            in_flight = pending
            if done:
                collect_done(done)
                continue

        if timeout is not None and timeout > 0:
            await asyncio.sleep(timeout)

    if in_flight:
        done, _ = await asyncio.wait(in_flight)
        collect_done(done)

    metrics.elapsed_seconds = max(0.001, monotonic() - started)
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
        f"{metrics.name:>15} | "
        f"{metrics.interval_seconds:>8.5f}s | "
        f"{metrics.total_requests:>6} | "
        f"{metrics.status_200:>4} | "
        f"{metrics.status_404:>4} | "
        f"{metrics.status_429:>4} | "
        f"{metrics.status_5xx:>4} | "
        f"{metrics.other_status:>5} | "
        f"{metrics.transport_errors:>6} | "
        f"{effective:>7.2f} | "
        f"{metrics.latency_p95_ms:>8.1f} | "
        f"{status:>4} | {reason}"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Calibrate safe concurrent RDAP floors for supported hosts.")
    parser.add_argument(
        "--target",
        action="append",
        choices=sorted(SUPPORTED_TARGETS.keys()),
        help="TLD target to calibrate. Repeat to limit the run; defaults to all supported targets.",
    )
    parser.add_argument("--concurrency", type=int, default=32, help="Max in-flight requests per target (default: 32).")
    parser.add_argument(
        "--warmup-interval",
        type=float,
        default=None,
        help="Optional warmup interval override in seconds.",
    )
    parser.add_argument(
        "--stage-intervals",
        default=None,
        help="Optional comma-separated stage interval override in seconds.",
    )
    parser.add_argument("--warmup-duration", type=float, default=10.0, help="Warmup duration per target in seconds.")
    parser.add_argument("--stage-duration", type=float, default=15.0, help="Per-stage duration in seconds.")
    parser.add_argument("--validation-duration", type=float, default=30.0, help="Validation duration in seconds.")
    parser.add_argument("--timeout", type=float, default=20.0, help="HTTP timeout per request.")
    parser.add_argument(
        "--max-error-rate",
        type=float,
        default=0.005,
        help="Allowed (5xx + transport errors) rate before failing a stage.",
    )
    parser.add_argument(
        "--max-requests-per-target",
        type=int,
        default=None,
        help="Optional request cap per target as a safety limit.",
    )
    parser.add_argument("--json-out", default=None, help="Optional path to write a JSON report.")
    parser.add_argument(
        "--user-agent",
        default="",
        help="Optional User-Agent override. Blank uses the same default header behavior as the app/httpx client.",
    )
    return parser


async def calibrate_target(
    client: httpx.AsyncClient,
    resolver: RDAPBootstrapResolver,
    profile: CalibrationTargetProfile,
    args: argparse.Namespace,
) -> Dict[str, object]:
    base_url = await resolve_base_url(client, resolver, profile.tld)
    host = httpx.URL(base_url).host or base_url
    warmup_interval = (
        float(profile.warmup_interval_seconds)
        if args.warmup_interval is None
        else float(args.warmup_interval)
    )
    stage_intervals = (
        profile.stage_intervals
        if args.stage_intervals is None
        else parse_stage_intervals(args.stage_intervals)
    )
    request_index = 0
    remaining = (
        int(args.max_requests_per_target)
        if args.max_requests_per_target is not None and int(args.max_requests_per_target) > 0
        else None
    )
    run_token = uuid.uuid4().hex[:12]

    print("")
    print(f"Target .{profile.tld}")
    if profile.tld in FORCE_IPV4_TLDS:
        print(f"- RDAP base: {base_url} (forcing IPv4)")
    else:
        print(f"- RDAP base: {base_url}")
    print(f"- Host: {host}")
    print(f"- Policy env: {profile.policy_env}")
    print(f"- Warmup interval: {warmup_interval:.5f}s")
    print(f"- Stage intervals: {', '.join(f'{value:.5f}' for value in stage_intervals)}")
    print(
        "          Stage | Interval |    Req | 200  | 404  | 429  | 5xx  | Other | Errors |     RPS |   P95ms | Stat | Reason",
        flush=True,
    )
    print("-" * 134, flush=True)

    warmup_name = f"warmup-{warmup_interval:.5f}s"
    warmup_metrics, used = await run_stage_concurrent(
        client=client,
        base_url=base_url,
        tld=profile.tld,
        run_token=run_token,
        start_index=request_index,
        stage_name=warmup_name,
        interval_seconds=warmup_interval,
        duration_seconds=float(args.warmup_duration),
        timeout_seconds=float(args.timeout),
        user_agent=args.user_agent,
        max_requests_remaining=remaining,
        max_in_flight=int(args.concurrency),
    )
    request_index += used
    if remaining is not None:
        remaining -= used
    print(render_row(warmup_metrics), flush=True)

    report: Dict[str, object] = {
        "tld": profile.tld,
        "host": host,
        "base_url": base_url,
        "policy_env": profile.policy_env,
        "warmup": asdict(warmup_metrics),
        "stages": [],
        "validation": None,
        "decision": None,
    }

    stage_metrics_list: List[StageMetrics] = []
    for interval in stage_intervals:
        if remaining is not None and remaining <= 0:
            print("Request cap reached before all stages completed.", flush=True)
            break

        stage_name = f"stage-{interval:.5f}s"
        metrics, used = await run_stage_concurrent(
            client=client,
            base_url=base_url,
            tld=profile.tld,
            run_token=run_token,
            start_index=request_index,
            stage_name=stage_name,
            interval_seconds=interval,
            duration_seconds=float(args.stage_duration),
            timeout_seconds=float(args.timeout),
            user_agent=args.user_agent,
            max_requests_remaining=remaining,
            max_in_flight=int(args.concurrency),
        )
        request_index += used
        if remaining is not None:
            remaining -= used

        verdict = evaluate_stage(metrics, max_error_rate=float(args.max_error_rate))
        stage_metrics_list.append(metrics)
        report["stages"].append({**asdict(metrics), "passed": verdict[0], "reason": verdict[1]})
        print(render_row(metrics, verdict), flush=True)
        if not verdict[0]:
            print(f"Stopping escalation at first failed stage for .{profile.tld}.", flush=True)
            break

    decision = choose_winning_interval(
        stage_metrics_list,
        validation_result=None,
        max_error_rate=float(args.max_error_rate),
    )

    if decision.winning_interval_seconds is not None and not (remaining is not None and remaining <= 0):
        validation_name = f"validation-{decision.winning_interval_seconds:.5f}s"
        validation_metrics, used = await run_stage_concurrent(
            client=client,
            base_url=base_url,
            tld=profile.tld,
            run_token=run_token,
            start_index=request_index,
            stage_name=validation_name,
            interval_seconds=float(decision.winning_interval_seconds),
            duration_seconds=float(args.validation_duration),
            timeout_seconds=float(args.timeout),
            user_agent=args.user_agent,
            max_requests_remaining=remaining,
            max_in_flight=int(args.concurrency),
        )
        request_index += used
        validation_verdict = evaluate_stage(validation_metrics, max_error_rate=float(args.max_error_rate))
        report["validation"] = {
            **asdict(validation_metrics),
            "passed": validation_verdict[0],
            "reason": validation_verdict[1],
        }
        print(render_row(validation_metrics, validation_verdict), flush=True)
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
    print(f"Decision for .{profile.tld}: {decision.reason}", flush=True)
    if decision.winning_interval_seconds is not None:
        print(f"- Recommended floor for .{profile.tld}: {decision.winning_interval_seconds:.5f}s", flush=True)
    else:
        print(f"- Recommended floor for .{profile.tld}: none", flush=True)

    return report


def aggregate_host_recommendations(target_reports: Sequence[Dict[str, object]]) -> List[Dict[str, object]]:
    grouped: Dict[Tuple[str, str], List[Dict[str, object]]] = {}
    for report in target_reports:
        key = (str(report["host"]), str(report["policy_env"]))
        grouped.setdefault(key, []).append(report)

    recommendations: List[Dict[str, object]] = []
    for (host, policy_env), reports in sorted(grouped.items()):
        target_results = []
        winning_intervals: List[float] = []
        failed_targets: List[str] = []
        for report in reports:
            tld = str(report["tld"])
            decision = dict(report.get("decision") or {})
            winning = decision.get("winning_interval_seconds")
            target_results.append(
                {
                    "tld": tld,
                    "winning_interval_seconds": winning,
                    "reason": decision.get("reason"),
                }
            )
            if winning is None:
                failed_targets.append(tld)
            else:
                winning_intervals.append(float(winning))

        if winning_intervals and not failed_targets:
            recommended = max(winning_intervals)
            reason = "Slowest safe winning interval across all TLDs sharing this host."
        elif winning_intervals:
            recommended = max(winning_intervals)
            reason = (
                f"Some TLDs failed calibration ({', '.join(sorted(failed_targets))}); "
                "using the slowest safe passing interval from the remaining targets."
            )
        else:
            recommended = None
            reason = "No target on this host produced a safe winning interval."

        recommendations.append(
            {
                "host": host,
                "policy_env": policy_env,
                "recommended_min_interval_seconds": recommended,
                "targets": target_results,
                "reason": reason,
            }
        )

    return recommendations


async def run_calibration(args: argparse.Namespace) -> int:
    targets = [SUPPORTED_TARGETS[key] for key in (args.target or list(SUPPORTED_TARGETS.keys()))]
    if int(args.concurrency) < 1:
        raise ValueError("Concurrency must be at least 1.")

    report: Dict[str, object] = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "concurrency": int(args.concurrency),
        "max_error_rate": float(args.max_error_rate),
        "durations": {
            "warmup_duration_seconds": float(args.warmup_duration),
            "stage_duration_seconds": float(args.stage_duration),
            "validation_duration_seconds": float(args.validation_duration),
        },
        "targets": [],
        "host_recommendations": [],
    }

    print("RDAP Host Speed Calibration")
    print(f"Targets: {', '.join('.' + item.tld for item in targets)}")
    print(f"Concurrency: {int(args.concurrency)}")
    print(
        f"Durations: warmup={float(args.warmup_duration)}s, "
        f"stage={float(args.stage_duration)}s, validation={float(args.validation_duration)}s",
        flush=True,
    )

    resolver = RDAPBootstrapResolver()
    for profile in targets:
        client_kwargs = (
            {"transport": httpx.AsyncHTTPTransport(local_address="0.0.0.0")}
            if profile.tld in FORCE_IPV4_TLDS
            else {}
        )
        async with httpx.AsyncClient(**client_kwargs) as client:
            target_report = await calibrate_target(client, resolver, profile, args)
            report["targets"].append(target_report)

    recommendations = aggregate_host_recommendations(report["targets"])
    report["host_recommendations"] = recommendations
    report["finished_at"] = datetime.now(timezone.utc).isoformat()

    print("")
    print("Host Recommendations")
    exit_code = 0
    for item in recommendations:
        if item["recommended_min_interval_seconds"] is None:
            exit_code = 2
            print(f"- {item['host']} -> no safe floor determined ({item['reason']})", flush=True)
            continue
        print(
            f"- {item['host']} -> {item['recommended_min_interval_seconds']:.5f}s "
            f"(set {item['policy_env']})",
            flush=True,
        )
        print(f"  {item['reason']}", flush=True)

    if args.json_out:
        path = Path(args.json_out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"JSON report written to: {path}", flush=True)

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
