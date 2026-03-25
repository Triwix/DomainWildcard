#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import signal
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.batch_runner import (
    DEFAULT_OUTPUT_DIR,
    BatchRunSummary,
    build_summary_filename,
    ensure_unique_path,
    parse_formats,
    run_batch,
)
from app.patterns import PatternValidationError, validate_pattern


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run sequential domain wildcard scans from the terminal.")
    parser.add_argument(
        "--pattern",
        action="append",
        required=False,
        help="Wildcard pattern to run. Repeat for multiple patterns (example: --pattern '*.com' --pattern '*go.com').",
    )
    parser.add_argument(
        "--patterns-file",
        default=None,
        help="Optional path to newline-delimited patterns file. Use '-' to read patterns from stdin.",
    )
    parser.add_argument(
        "--resume",
        default=None,
        help="Optional prior batch summary JSON path; completed patterns are skipped.",
    )
    parser.add_argument("--wordlist", required=True, help="Primary wordlist file path.")
    parser.add_argument(
        "--wordlist-secondary",
        default=None,
        help="Optional secondary wordlist path for patterns with 2-4 wildcards (applies to wildcard positions 2..N).",
    )
    parser.add_argument(
        "--formats",
        default="txt",
        help="Comma-separated output formats: txt,csv,json (default: txt).",
    )
    parser.add_argument(
        "--export-wildcard-token",
        default="w",
        help="Token used in export filenames in place of '*' (default: w).",
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help=f"Output directory (default: {DEFAULT_OUTPUT_DIR}).",
    )
    parser.add_argument("--force-recheck", action="store_true", help="Bypass local RDAP cache for this run.")
    parser.add_argument("--cache-only", action="store_true", help="Use local cache only; do not send RDAP network requests.")
    parser.add_argument(
        "--cache-db",
        default=None,
        help="Optional cache SQLite path override (default: project data/rdap_cache.sqlite3).",
    )
    parser.add_argument(
        "--available-ttl-seconds",
        type=int,
        default=None,
        help="Optional cache TTL for available results in seconds (default: 900).",
    )
    parser.add_argument(
        "--taken-ttl-seconds",
        type=int,
        default=None,
        help="Optional cache TTL for taken results in seconds (default: 21600).",
    )
    parser.add_argument(
        "--unknown-ttl-seconds",
        type=int,
        default=None,
        help="Optional cache TTL for unknown results in seconds (default: 300).",
    )
    parser.add_argument("--dry-run", action="store_true", help="Validate and estimate only; do not execute RDAP checks.")
    parser.add_argument(
        "--stats-interval",
        type=float,
        default=0.0,
        help="Minimum seconds between progress renders (default: 0, render on every update).",
    )
    parser.add_argument("--quiet", action="store_true", help="Suppress live progress output; print final summary only.")
    parser.add_argument("--concurrency", type=int, default=32, help="Concurrent RDAP checks per pattern (default: 32).")
    parser.add_argument(
        "--verisign-min-interval",
        type=float,
        default=None,
        help="Manual min interval seconds for rdap.verisign.com (.com/.net).",
    )
    parser.add_argument(
        "--pir-min-interval",
        type=float,
        default=None,
        help="Manual min interval seconds for rdap.publicinterestregistry.org (.org).",
    )
    parser.add_argument(
        "--identitydigital-min-interval",
        type=float,
        default=None,
        help="Manual min interval seconds for rdap.identitydigital.services (.ai/.io/.info).",
    )
    parser.add_argument(
        "--registryco-min-interval",
        type=float,
        default=None,
        help="Manual min interval seconds for rdap.registry.co (.co).",
    )
    parser.add_argument(
        "--centralnic-min-interval",
        type=float,
        default=None,
        help="Manual min interval seconds for rdap.centralnic.com (.xyz).",
    )
    parser.add_argument(
        "--gmoregistry-min-interval",
        type=float,
        default=None,
        help="Manual min interval seconds for rdap.gmoregistry.net (.shop).",
    )
    parser.add_argument(
        "--radix-min-interval",
        type=float,
        default=None,
        help="Manual min interval seconds for rdap.radix.host (.store/.online).",
    )
    parser.add_argument(
        "--denic-min-interval",
        type=float,
        default=None,
        help="Manual min interval seconds for rdap.denic.de (.de).",
    )
    parser.add_argument(
        "--nominet-min-interval",
        type=float,
        default=None,
        help="Manual min interval seconds for rdap.nominet.uk (.uk).",
    )
    parser.add_argument(
        "--sidn-min-interval",
        type=float,
        default=None,
        help="Manual min interval seconds for rdap.sidn.nl (.nl).",
    )
    parser.add_argument(
        "--registrobr-min-interval",
        type=float,
        default=None,
        help="Manual min interval seconds for rdap.registro.br (.br).",
    )
    parser.add_argument(
        "--au-min-interval",
        type=float,
        default=None,
        help="Manual min interval seconds for rdap.cctld.au (.au).",
    )
    parser.add_argument(
        "--fail-fast",
        action="store_true",
        help="Stop the batch on first failed pattern. Default behavior continues to next pattern.",
    )
    return parser


def parse_patterns_text(raw_text: str) -> List[str]:
    patterns: List[str] = []
    for line in str(raw_text or "").splitlines():
        value = line.strip()
        if not value or value.startswith("#"):
            continue
        patterns.append(value)
    return patterns


def read_patterns_source(patterns_file: Optional[str]) -> List[str]:
    source = str(patterns_file or "").strip()
    if not source:
        return []
    if source == "-":
        return parse_patterns_text(sys.stdin.read())
    return parse_patterns_text(Path(source).read_text(encoding="utf-8"))


def resolve_patterns(
    cli_patterns: Optional[List[str]],
    patterns_file: Optional[str],
    resume_path: Optional[str],
) -> Tuple[List[str], List[str], Optional[str]]:
    direct = [str(item).strip() for item in (cli_patterns or []) if str(item).strip()]
    from_file = read_patterns_source(patterns_file)
    patterns = [*direct, *from_file]
    skipped: List[str] = []
    resumed_from: Optional[str] = None

    if resume_path:
        resumed_from = str(Path(resume_path).expanduser().resolve())
        payload = json.loads(Path(resumed_from).read_text(encoding="utf-8"))
        summary_patterns = []
        completed_normalized = set()
        completed_raw = set()
        for entry in payload.get("patterns", []):
            if not isinstance(entry, dict):
                continue
            pattern = str(entry.get("pattern", "")).strip()
            if not pattern:
                continue
            summary_patterns.append(pattern)
            if str(entry.get("status", "")).lower() != "completed":
                continue
            completed_raw.add(pattern)
            normalized_value = entry.get("normalized_pattern")
            if isinstance(normalized_value, str) and normalized_value.strip():
                completed_normalized.add(normalized_value.strip().lower())
                continue
            try:
                completed_normalized.add(validate_pattern(pattern))
            except PatternValidationError:
                continue

        if not patterns:
            patterns = summary_patterns

        filtered: List[str] = []
        for pattern in patterns:
            normalized_candidate: Optional[str] = None
            try:
                normalized_candidate = validate_pattern(pattern)
            except PatternValidationError:
                normalized_candidate = None

            if pattern in completed_raw or (normalized_candidate is not None and normalized_candidate in completed_normalized):
                skipped.append(pattern)
                continue
            filtered.append(pattern)
        patterns = filtered

    return patterns, skipped, resumed_from


def format_eta(seconds: Optional[float]) -> str:
    if seconds is None or seconds < 0 or seconds == float("inf"):
        return "--:--"
    total_seconds = int(round(seconds))
    minutes, secs = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def _progress_line(
    pattern: str,
    snapshot: Dict[str, object],
    network_request_rps: Optional[float] = None,
    network_request_avg_rps: Optional[float] = None,
    eta_seconds: Optional[float] = None,
    terminal_width: Optional[int] = None,
) -> str:
    width = None if terminal_width is None else max(40, int(terminal_width))
    done = int(snapshot.get("progress_processed", 0) or 0)
    total = int(snapshot.get("total_candidates", 0) or 0)
    bounded_done = max(0, done)
    bounded_total = max(0, total)
    ratio = (min(1.0, float(bounded_done) / float(bounded_total)) if bounded_total > 0 else 0.0)
    bar_width = 8 if width is not None and width <= 90 else 12
    filled = int(round(ratio * bar_width))
    bar = f"{'#' * filled}{'-' * max(0, bar_width - filled)}"
    percent = ratio * 100.0
    available = int(snapshot.get("available_count", 0) or 0)
    taken = int(snapshot.get("taken_count", 0) or 0)
    unknown = int(snapshot.get("unknown_count", 0) or 0)
    current_speed = 0.0 if network_request_rps is None else max(0.0, float(network_request_rps))
    average_speed = 0.0 if network_request_avg_rps is None else max(0.0, float(network_request_avg_rps))
    eta_text = format_eta(eta_seconds)
    pattern_max = 18 if width is not None and width <= 90 else 28
    pattern_label = pattern if len(pattern) <= pattern_max else f"{pattern[: pattern_max - 1]}…"
    base = (
        f"[{pattern_label}] [{bar}] {bounded_done}/{bounded_total} ({percent:5.1f}%) "
        f"a={available} t={taken} u={unknown}"
    )
    suffix_full = f" net={current_speed:.2f}/s avg={average_speed:.2f}/s eta={eta_text}"
    suffix_mid = f" net={current_speed:.2f}/s eta={eta_text}"
    suffix_short = f" net={current_speed:.2f}/s"

    if width is None:
        return f"{base}{suffix_full}"

    for suffix in (suffix_full, suffix_mid, suffix_short, ""):
        candidate = f"{base}{suffix}"
        if len(candidate) <= max(1, width - 1):
            return candidate
    return f"{base[: max(1, width - 1)]}"


def _make_progress_callback(stats_interval_seconds: float = 0.0, quiet: bool = False, cache_only: bool = False):
    last_state: Dict[str, Tuple[int, int, int, int, int, int, int, str]] = {}
    rate_state: Dict[str, Tuple[float, int, int, float]] = {}
    last_emit_at: Dict[str, float] = {}
    supports_inplace = sys.stdout.isatty()
    render_state = {"line_open": False, "last_pattern": None}

    def fit_terminal_width(line: str) -> str:
        if not supports_inplace:
            return line
        terminal_width = max(40, int(shutil.get_terminal_size(fallback=(120, 24)).columns))
        max_len = max(1, terminal_width - 1)
        if len(line) <= max_len:
            return line
        if max_len <= 3:
            return line[:max_len]
        return f"{line[: max_len - 1]}…"

    def render_in_place(pattern: str, line: str, terminal: bool) -> None:
        if not supports_inplace:
            print(line)
            return

        display = fit_terminal_width(line)

        # If switching patterns, close the previous in-place line first.
        if render_state["line_open"] and render_state["last_pattern"] != pattern and not terminal:
            print()
            render_state["line_open"] = False

        # Clear the current line before redraw so wrapped/stale chars do not remain.
        print(f"\r\033[2K{display}", end="", flush=True)
        render_state["line_open"] = True
        if terminal:
            print()
            render_state["line_open"] = False

    def callback(pattern: str, snapshot: Dict[str, object], is_terminal: bool) -> None:
        marker = (
            int(snapshot.get("progress_processed", 0) or 0),
            int(snapshot.get("total_candidates", 0) or 0),
            int(snapshot.get("available_count", 0) or 0),
            int(snapshot.get("taken_count", 0) or 0),
            int(snapshot.get("unknown_count", 0) or 0),
            int(snapshot.get("invalid_count", 0) or 0),
            int(snapshot.get("duplicate_count", 0) or 0),
            str(snapshot.get("status", "")),
        )
        now = time.monotonic()
        done = int(snapshot.get("progress_processed", 0) or 0)
        misses = int(snapshot.get("cache_misses", 0) or 0)
        if pattern not in rate_state:
            rate_state[pattern] = (now, misses, done, now)
        last_time, last_misses, last_done, start_time = rate_state[pattern]
        delta_time = max(0.0, now - last_time)
        delta_misses = max(0, misses - last_misses)
        delta_done = max(0, done - last_done)
        current_network_rps = 0.0
        average_network_rps = 0.0
        if not cache_only:
            current_network_rps = (float(delta_misses) / delta_time) if delta_time > 0 else 0.0
            elapsed_network = max(0.0, now - start_time)
            average_network_rps = (float(misses) / elapsed_network) if elapsed_network > 0 else 0.0
        current_done_rps = (float(delta_done) / delta_time) if delta_time > 0 else 0.0
        elapsed = max(0.0, now - start_time)
        average_done_rps = (float(done) / elapsed) if elapsed > 0 else 0.0
        total = int(snapshot.get("total_candidates", 0) or 0)
        remaining = max(0, total - done)
        rate_for_eta = current_done_rps if current_done_rps > 0 else average_done_rps
        eta_seconds = (float(remaining) / rate_for_eta) if rate_for_eta > 0 else None
        should_emit = is_terminal or marker != last_state.get(pattern)
        if not should_emit:
            return
        if quiet:
            last_state[pattern] = marker
            rate_state[pattern] = (now, misses, done, start_time)
            return
        if not is_terminal and stats_interval_seconds > 0:
            previous_emit = last_emit_at.get(pattern, 0.0)
            if now - previous_emit < stats_interval_seconds:
                return
        terminal_width = None
        if supports_inplace:
            terminal_width = int(shutil.get_terminal_size(fallback=(120, 24)).columns)
        line = _progress_line(
            pattern,
            snapshot,
            network_request_rps=current_network_rps,
            network_request_avg_rps=average_network_rps,
            eta_seconds=eta_seconds,
            terminal_width=terminal_width,
        )
        render_state["last_pattern"] = pattern
        render_in_place(pattern, line, terminal=is_terminal)
        last_state[pattern] = marker
        rate_state[pattern] = (now, misses, done, start_time)
        last_emit_at[pattern] = now

    def finalize() -> None:
        if supports_inplace and render_state["line_open"]:
            print()
            render_state["line_open"] = False

    return callback, finalize


def print_batch_summary(summary: BatchRunSummary) -> None:
    print("")
    print("Batch Summary")
    print(f"- Started: {summary.started_at}")
    print(f"- Finished: {summary.finished_at}")
    print(f"- Output directory: {summary.output_dir}")
    print(f"- Summary file: {summary.summary_path}")
    for item in summary.patterns:
        line = f"- Pattern: {item.pattern} -> {item.status}"
        if item.error:
            line += f" ({item.error})"
        print(line)
        print(
            "  "
            + f"available={item.counts.get('available_count', 0)} "
            + f"taken={item.counts.get('taken_count', 0)} "
            + f"unknown={item.counts.get('unknown_count', 0)} "
            + f"cache_hits={item.counts.get('cache_hits', 0)} "
            + f"cache_misses={item.counts.get('cache_misses', 0)}"
        )
        for path in item.output_files:
            print(f"  output: {path}")


def write_empty_summary(
    *,
    output_dir: Path,
    formats: str,
    force_recheck: bool,
    concurrency: int,
    fail_fast: bool,
) -> str:
    output_dir = output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    summary_path = ensure_unique_path(output_dir / build_summary_filename())
    summary = BatchRunSummary(
        started_at=datetime.now().astimezone().isoformat(),
        finished_at=datetime.now().astimezone().isoformat(),
        output_dir=str(output_dir),
        formats=parse_formats(formats),
        force_recheck=bool(force_recheck),
        concurrency=int(concurrency),
        fail_fast=bool(fail_fast),
        summary_path=str(summary_path),
        patterns=[],
    )
    summary_path.write_text(json.dumps(summary.to_dict(), indent=2), encoding="utf-8")
    return str(summary_path)


async def _run(args: argparse.Namespace, stop_state: Dict[str, bool]) -> int:
    if args.stats_interval < 0:
        raise ValueError("--stats-interval must be >= 0.")
    for option_name, value in [
        ("--verisign-min-interval", args.verisign_min_interval),
        ("--pir-min-interval", args.pir_min_interval),
        ("--identitydigital-min-interval", args.identitydigital_min_interval),
        ("--registryco-min-interval", args.registryco_min_interval),
        ("--centralnic-min-interval", args.centralnic_min_interval),
        ("--gmoregistry-min-interval", args.gmoregistry_min_interval),
        ("--radix-min-interval", args.radix_min_interval),
        ("--denic-min-interval", args.denic_min_interval),
        ("--nominet-min-interval", args.nominet_min_interval),
        ("--sidn-min-interval", args.sidn_min_interval),
        ("--registrobr-min-interval", args.registrobr_min_interval),
        ("--au-min-interval", args.au_min_interval),
    ]:
        if value is not None and float(value) <= 0:
            raise ValueError(f"{option_name} must be > 0.")
    for option_name, value in [
        ("--available-ttl-seconds", args.available_ttl_seconds),
        ("--taken-ttl-seconds", args.taken_ttl_seconds),
        ("--unknown-ttl-seconds", args.unknown_ttl_seconds),
    ]:
        if value is not None and int(value) <= 0:
            raise ValueError(f"{option_name} must be > 0.")

    patterns, skipped_patterns, resumed_from = resolve_patterns(
        cli_patterns=list(args.pattern or []),
        patterns_file=args.patterns_file,
        resume_path=args.resume,
    )
    if not patterns:
        if resumed_from:
            summary_path = write_empty_summary(
                output_dir=Path(args.output_dir),
                formats=args.formats,
                force_recheck=bool(args.force_recheck),
                concurrency=int(args.concurrency),
                fail_fast=bool(args.fail_fast),
            )
            print("No patterns to run after applying resume filters.")
            print(f"Summary file: {summary_path}")
            return 0
        raise ValueError("At least one pattern is required (use --pattern, --patterns-file, or --resume).")

    if resumed_from:
        print(f"Resuming from: {resumed_from}")
        if skipped_patterns:
            print(f"Skipping {len(skipped_patterns)} already-completed pattern(s).")

    progress_callback, finalize_progress = _make_progress_callback(
        stats_interval_seconds=float(args.stats_interval),
        quiet=bool(args.quiet),
        cache_only=bool(args.cache_only),
    )
    summary = await run_batch(
        patterns=patterns,
        wordlist_path=Path(args.wordlist),
        wordlist_secondary_path=Path(args.wordlist_secondary) if args.wordlist_secondary else None,
        formats=args.formats,
        wildcard_token=str(args.export_wildcard_token),
        available_ttl_seconds=args.available_ttl_seconds,
        taken_ttl_seconds=args.taken_ttl_seconds,
        unknown_ttl_seconds=args.unknown_ttl_seconds,
        output_dir=Path(args.output_dir),
        force_recheck=bool(args.force_recheck),
        cache_only=bool(args.cache_only),
        cache_db_path=Path(args.cache_db).expanduser().resolve() if args.cache_db else None,
        dry_run=bool(args.dry_run),
        concurrency=int(args.concurrency),
        fail_fast=bool(args.fail_fast),
        verisign_min_interval_seconds=args.verisign_min_interval,
        publicinterestregistry_min_interval_seconds=args.pir_min_interval,
        identitydigital_min_interval_seconds=args.identitydigital_min_interval,
        registry_co_min_interval_seconds=args.registryco_min_interval,
        centralnic_min_interval_seconds=args.centralnic_min_interval,
        gmoregistry_min_interval_seconds=args.gmoregistry_min_interval,
        radix_min_interval_seconds=args.radix_min_interval,
        denic_min_interval_seconds=args.denic_min_interval,
        nominet_min_interval_seconds=args.nominet_min_interval,
        sidn_min_interval_seconds=args.sidn_min_interval,
        registro_br_min_interval_seconds=args.registrobr_min_interval,
        au_min_interval_seconds=args.au_min_interval,
        progress_callback=progress_callback,
        stop_requested=lambda: bool(stop_state.get("requested", False)),
    )
    finalize_progress()
    print_batch_summary(summary)
    if stop_state.get("requested"):
        print("- Graceful stop requested; partial results were exported where available.")
        return 130
    has_failures = any(item.status != "completed" for item in summary.patterns)
    return 1 if has_failures else 0


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    stop_state = {"requested": False}
    previous_handler = signal.getsignal(signal.SIGINT)

    def _handle_sigint(_signum, _frame):
        if not stop_state["requested"]:
            stop_state["requested"] = True
            print("\nInterrupt received. Stopping gracefully and exporting partial results...")
            signal.signal(signal.SIGINT, signal.default_int_handler)

    signal.signal(signal.SIGINT, _handle_sigint)
    try:
        return asyncio.run(_run(args, stop_state))
    except KeyboardInterrupt:
        print("\nInterrupted.")
        return 130
    except Exception as exc:
        print(f"Batch run failed: {exc}")
        return 2
    finally:
        signal.signal(signal.SIGINT, previous_handler)


if __name__ == "__main__":
    raise SystemExit(main())
