# Domain Wildcard Availability Checker

Local FastAPI app for RDAP-based wildcard domain availability scanning, with both a web UI and terminal batch runner.

## Features

- One- to four-wildcard pattern support:
  - `*.com`
  - `**.com`
  - `***.com`
  - `****.com`
  - For patterns with 2-4 wildcards, wildcard #1 uses the primary wordlist and wildcard positions #2..N use the secondary wordlist (or primary when secondary is not provided).
- RDAP bootstrap discovery from IANA `dns.json`.
- TLD handling is automatic from the pattern suffix (no manual TLD selection in the UI).
- Built-in fallback coverage for `*.ai`, `*.xyz`, `*.shop`, `*.store`, `*.online`, `*.net`, `*.org`, `*.io`, `*.info`, `*.co`, `*.de`, `*.uk`, `*.nl`, `*.br`, and `*.au` when bootstrap data is missing a suffix.
- `.cn` and `.ru` depend on IANA bootstrap availability in this build (no safe hardcoded fallback endpoint configured).
- Uses direct RDAP `GET` existence checks by default (the `HEAD` probe path exists in code but is disabled in this build for stability).
- For Identity Digital hosts (`.ai`, `.io`, `.info`), the client automatically retries over IPv4 when dual-stack paths return HTTP `403`.
- Adaptive per-host pacing with backoff on `403`/`429`/`5xx`.
- `.com` (Verisign) uses an aggressive default floor of `0.0001s` (adaptive backoff still applies on `403`/`429`/`5xx`).
- Persistent local RDAP cache (SQLite TTL by result state).
- Web UI with live progress, filtering/sorting, exports, and wordlist editor.
- Manual speed controls in the UI for per-host min interval overrides (with reset).
- Static asset delivery uses gzip compression and explicit cache headers (default `no-cache` for local reliability during updates).
- No third-party font CDN dependency on first page load.
- Terminal batch runner for sequential multi-pattern runs to `output/`.

## Safety Limits

- `50,000` words max per wordlist.
- `1,000,000` expanded candidates max per run.
- `8 MiB` upload size max per file (web API uploads).

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Preflight (Recommended)

Run a non-interactive environment check before first use:

```bash
python3 scripts/preflight.py
```

## Make Targets

Common shortcuts:

```bash
make setup
make preflight
make run
make run-reload
make test
make test-js
make batch-help
```

## Agent Runbook

Automation-focused instructions live in:

- `AGENTS.md`

Machine-friendly sample inputs and summary schema live in:

- `examples/wordlist.txt`
- `examples/patterns.txt`
- `examples/batch-summary.schema.json`

## Quick Start (No Manual Setup)

From a cloned repo:

```bash
./start.sh
```

or:

```bash
python3 scripts/quickstart.py
```

This will create `.venv` if needed, install dependencies, start the web server, and open the browser automatically.

Platform helpers:

- macOS: double-click `start.command`
- Linux: run `./start.sh`
- Windows: run `start.bat`

## Run Web UI (Direct)

User mode (no auto-reload):

```bash
python -m app.launcher --open
```

Development mode (reload enabled):

```bash
python -m app.launcher --open --reload
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000)

## pipx Install (One-Time)

Install from local path:

```bash
pipx install .
```

Then run from anywhere:

```bash
domain-search --open
```

Optional static cache tuning:

- `DOMAIN_SEARCH_STATIC_MAX_AGE_SECONDS` (default `0`, no-cache/no-store for local dev)

### Manual Speed Controls (Web UI)

Use the **Manual Speed Controls** panel to adjust per-host minimum interval seconds at runtime:

- Leave a host field blank to keep automatic defaults.
- Enter a larger interval (for example `1.0`) to slow a throttled host.
- Click **Apply Speed Overrides** to apply immediately (active scans included).
- Click **Reset Defaults** to clear manual overrides.
- Keep **Reset current host backoff timers** checked if you want changes to take effect right away.

## RDAP Speed Tuning

Default behavior:

- Verisign (`.com`, `.net`) floor is `0.0001s` via `RDAP_VERISIGN_MIN_INTERVAL_SECONDS`.
- Public Interest Registry (`.org`) floor is `0.02s` via `RDAP_PUBLICINTERESTREGISTRY_MIN_INTERVAL_SECONDS`.
- Identity Digital (`.ai`, `.io`, `.info`) floor is `0.85s` via `RDAP_IDENTITYDIGITAL_MIN_INTERVAL_SECONDS`.
- Registry.co (`.co`) floor is `0.0125s` via `RDAP_REGISTRY_CO_MIN_INTERVAL_SECONDS`.
- CentralNic (`.xyz`) floor is `0.025s` via `RDAP_CENTRALNIC_MIN_INTERVAL_SECONDS`.
- Radix (`.store`, `.online`) floor is `0.125s` via `RDAP_RADIX_MIN_INTERVAL_SECONDS`.
- DENIC (`.de`) floor is `0.15s` via `RDAP_DENIC_MIN_INTERVAL_SECONDS`.
- Nominet (`.uk`) floor is `0.15s` via `RDAP_NOMINET_MIN_INTERVAL_SECONDS`.
- SIDN (`.nl`) floor is `1.0s` via `RDAP_SIDN_MIN_INTERVAL_SECONDS`.
- Registro.br (`.br`) floor is `0.025s` via `RDAP_REGISTRO_BR_MIN_INTERVAL_SECONDS`.
- `.shop` and `.au` keep conservative defaults (`2.0s`) via `RDAP_GMOREGISTRY_MIN_INTERVAL_SECONDS` and `RDAP_AU_MIN_INTERVAL_SECONDS` due strict/variable throttling.

Override example (more conservative):

```bash
export RDAP_VERISIGN_MIN_INTERVAL_SECONDS=0.01
export RDAP_PUBLICINTERESTREGISTRY_MIN_INTERVAL_SECONDS=0.1
export RDAP_IDENTITYDIGITAL_MIN_INTERVAL_SECONDS=1.0
export RDAP_REGISTRY_CO_MIN_INTERVAL_SECONDS=0.03
export RDAP_CENTRALNIC_MIN_INTERVAL_SECONDS=0.05
export RDAP_GMOREGISTRY_MIN_INTERVAL_SECONDS=3.0
export RDAP_RADIX_MIN_INTERVAL_SECONDS=0.2
export RDAP_DENIC_MIN_INTERVAL_SECONDS=0.25
export RDAP_NOMINET_MIN_INTERVAL_SECONDS=0.25
export RDAP_SIDN_MIN_INTERVAL_SECONDS=1.5
export RDAP_REGISTRO_BR_MIN_INTERVAL_SECONDS=0.05
export RDAP_AU_MIN_INTERVAL_SECONDS=3.0
```

Manual runtime overrides (UI/API) can be applied per host without restarting the app.

Soak validation command at current floor:

```bash
python scripts/rdap_safe_speed_calibration.py \
  --tld com \
  --warmup-duration 60 \
  --stage-intervals 0.0001 \
  --stage-duration 1800 \
  --validation-duration 300 \
  --json-out data/calibration-com-soak-364rps.json
```

Calibrate all supported RDAP hosts with the concurrent host-aware tuner:

```bash
python scripts/rdap_host_speed_calibration.py \
  --json-out data/calibration-supported-hosts.json
```

Note: calibration scripts force IPv4 for Identity Digital targets (`.ai`, `.io`, `.info`) so results are not skewed by IPv6 `403` paths.

Target a single host/TLD with a custom ladder:

```bash
python scripts/rdap_host_speed_calibration.py \
  --target org \
  --warmup-interval 0.2 \
  --stage-intervals 0.1,0.05,0.033,0.025,0.02 \
  --json-out data/calibration-org.json
```

## Terminal Batch Usage

Use the terminal runner to execute multiple patterns sequentially:

```bash
python scripts/domain_batch_run.py \
  --wordlist "/absolute/path/to/wordlist.txt" \
  --pattern "*.com" \
  --pattern "*agent.com" \
  --formats txt
```

### Terminal Options

- `--pattern` repeatable pattern input (optional if using `--patterns-file` or `--resume`).
- `--patterns-file` newline-delimited pattern file path. Use `-` to read patterns from stdin.
- `--resume` prior batch summary JSON path; completed patterns are skipped.
- `--wordlist` required primary wordlist path.
- `--wordlist-secondary` optional secondary list for patterns with 2-4 wildcards (applies to wildcard positions #2..N).
- `--formats` comma-separated: `txt,csv,json` (default `txt`).
- `--export-wildcard-token` replacement token used in exported filenames for `*` (default `w`).
- `--output-dir` output folder (default project `output/`).
- `--force-recheck` bypass cache for this batch.
- `--cache-only` use local cache only (no RDAP network requests).
- `--cache-db` optional cache SQLite path override.
- `--dry-run` validate patterns and counts only (no RDAP checks, no exports).
- `--stats-interval <sec>` throttle live progress rendering frequency.
- `--quiet` suppress live progress output.
- `--concurrency` per-pattern concurrent checks (default `32`).
- `--verisign-min-interval` manual min interval seconds for `rdap.verisign.com` (`.com/.net`).
- `--pir-min-interval` manual min interval seconds for `rdap.publicinterestregistry.org` (`.org`).
- `--identitydigital-min-interval` manual min interval seconds for `rdap.identitydigital.services` (`.ai/.io/.info`).
- `--registryco-min-interval` manual min interval seconds for `rdap.registry.co` (`.co`).
- `--centralnic-min-interval` manual min interval seconds for `rdap.centralnic.com` (`.xyz`).
- `--gmoregistry-min-interval` manual min interval seconds for `rdap.gmoregistry.net` (`.shop`).
- `--radix-min-interval` manual min interval seconds for `rdap.radix.host` (`.store/.online`).
- `--denic-min-interval` manual min interval seconds for `rdap.denic.de` (`.de`).
- `--nominet-min-interval` manual min interval seconds for `rdap.nominet.uk` (`.uk`).
- `--sidn-min-interval` manual min interval seconds for `rdap.sidn.nl` (`.nl`).
- `--registrobr-min-interval` manual min interval seconds for `rdap.registro.br` (`.br`).
- `--au-min-interval` manual min interval seconds for `rdap.cctld.au` (`.au`).
- `--fail-fast` stop on first failed pattern (default: continue).

Examples:

```bash
python scripts/domain_batch_run.py --wordlist words.txt --patterns-file patterns.txt
```

```bash
cat patterns.txt | python scripts/domain_batch_run.py --wordlist words.txt --patterns-file -
```

```bash
python scripts/domain_batch_run.py --wordlist words.txt --resume output/batch-2026-03-17-16-01-40.json
```

### Terminal Progress Output

Each pattern logs live counters and request speed:

- `net=<current RDAP requests/sec>`
- `avg=<average RDAP requests/sec for current pattern>`
- `eta=<estimated time remaining for current pattern>`
- In interactive terminals, progress updates in-place on a single line per active pattern.
- On narrow terminals, the line auto-compacts (for example, `avg` may be omitted) to avoid wrapped/corrupted output.
- Includes a text progress bar based on `progress_processed / total_candidates` (same completion basis as the web UI).

Network request speed is based on actual RDAP network checks. In `--cache-only` mode, network speed reports as `0.00/s`.

Press `Ctrl+C` once for graceful stop:

- active pattern is cancelled
- partial available-domain exports are written for that pattern
- batch summary JSON is still written

If `--resume` leaves nothing to run, the command still writes an empty batch summary JSON for that invocation.

### Terminal Output Files

For each completed pattern:

- `txt`: domains only
- `csv/json`: include metadata (`state`, `source`, `checked_at`, `ttl_seconds`, `expires_at`, `from_cache`)

Written to `output/` with timestamped filenames and collision-safe suffixing.
By default, `*` in pattern filenames is replaced with `w` (for example `*ai.com` -> `wai.com-...txt`).
Override in terminal mode with `--export-wildcard-token`.

Each batch also writes:

- `batch-YYYY-MM-DD-HH-mm-ss.json` summary (per-pattern status, counts, errors, and file paths)

## Post-Run Domain Ranking

After generating many `.txt` exports, run the deterministic ranker to scan every line,
dedupe all domains, score resale + future upside, and output a shortlist:

```bash
python scripts/domain_value_ranker.py --input-dir output --top 300 --min-score 55
```

By default the shortlist excludes domains flagged as trademark-conflict risk using
a conservative built-in blocklist (plus any optional custom terms):

```bash
python scripts/domain_value_ranker.py \
  --input-dir output \
  --trademark-blocklist /absolute/path/to/extra-trademarks.txt
```

If you want to inspect everything without trademark filtering:

```bash
python scripts/domain_value_ranker.py --input-dir output --disable-trademark-filter
```

Outputs include:

- full ranked CSV of all unique domains
- top ranked CSV
- top ranked TXT (domains only)
- summary JSON with per-file line coverage (`coverage_ok`) and trademark risk counts

## API (Web)

- `GET /` - web UI
- `POST /api/jobs` - create job (`pattern`, `wordlist`, optional `wordlist_secondary` for 2-4 wildcard patterns, optional `force_recheck`)
- `GET /api/jobs/{job_id}` - status/progress snapshot
- `GET /api/jobs/{job_id}/events` - SSE stream
- `POST /api/jobs/{job_id}/cancel` - cancel active job
- `GET /api/rate-status` - host pacing/runtime stats
- `GET /api/rate-config` - supported hosts, policy defaults, current manual overrides
- `POST /api/rate-config` - apply manual overrides (`overrides`, optional `replace`, optional `reset_backoff`)
- `DELETE /api/rate-config` - clear manual overrides (optional `reset_backoff`)
- `GET /api/jobs/{job_id}/export.txt?sort=...&q=...`
- `GET /api/jobs/{job_id}/export.csv?sort=...&q=...`
- `GET /api/jobs/{job_id}/export.json?sort=...&q=...`
- Optional export query override: `wildcard_token=...` (default `w`) for filename token replacement.

## Tests

```bash
pytest -q
```

```bash
node --test tests/test_wordlist_utils.js
```
