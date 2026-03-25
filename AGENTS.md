# AGENTS Runbook

This file is for automation agents (including OpenClaw-style agents) to run the repo safely with minimal discovery.

## Repo Facts

- Python app root: `app/`
- Web entrypoint: `python -m app.launcher`
- Terminal batch entrypoint: `python scripts/domain_batch_run.py`
- Canonical runtime output directory: `output/`
- Canonical runtime cache directory: `data/`
- Wordlist defaults: `default wordlists/`

## Safety Limits

- Max words per wordlist: `50,000`
- Max expanded candidates per run: `1,000,000`
- Max upload size (web API): `8 MiB`

## Fast Start (Agent)

1. Run preflight:
   - `python3 scripts/preflight.py`
2. Run tests:
   - `pytest -q`
   - `node --test tests/test_wordlist_utils.js`
3. Start web UI:
   - `python -m app.launcher --open`
4. Run terminal batch:
   - `python scripts/domain_batch_run.py --wordlist examples/wordlist.txt --patterns-file examples/patterns.txt --formats txt`

## Make Targets

- `make preflight`
- `make setup`
- `make run`
- `make run-reload`
- `make test`
- `make test-js`
- `make batch-help`

## API Surface

- `GET /`
- `POST /api/jobs`
- `GET /api/jobs/{job_id}`
- `GET /api/jobs/{job_id}/events`
- `POST /api/jobs/{job_id}/cancel`
- `GET /api/rate-status`
- `GET /api/rate-config`
- `POST /api/rate-config`
- `DELETE /api/rate-config`
- `GET /api/jobs/{job_id}/export.txt`
- `GET /api/jobs/{job_id}/export.csv`
- `GET /api/jobs/{job_id}/export.json`

## Output Contract (Batch)

- One export file per pattern per selected format in `output/`
- One batch summary JSON in `output/`:
  - `batch-YYYY-MM-DD-HH-mm-ss.json`
- See schema example:
  - `examples/batch-summary.schema.json`

## Notes for Automation

- Use lowercase `output/` in scripts and docs to avoid case-sensitive path mismatches on Linux.
- `txt` exports contain domains only.
- `csv`/`json` exports include metadata (`state`, `source`, `checked_at`, `ttl_seconds`, `expires_at`, `from_cache`).
- A single `Ctrl+C` during terminal batch requests graceful cancellation with partial export write.
