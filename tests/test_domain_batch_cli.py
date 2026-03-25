import json

from pathlib import Path

from scripts.domain_batch_run import (
    _progress_line,
    build_parser,
    parse_patterns_text,
    read_patterns_source,
    resolve_patterns,
    write_empty_summary,
)


def test_cli_parser_supports_repeated_pattern_args():
    parser = build_parser()
    args = parser.parse_args(
        [
            "--wordlist",
            "/tmp/words.txt",
            "--pattern",
            "*.com",
            "--pattern",
            "*go.com",
            "--stats-interval",
            "1.5",
            "--quiet",
            "--export-wildcard-token",
            "star",
            "--cache-db",
            "/tmp/custom-cache.sqlite3",
            "--identitydigital-min-interval",
            "1.25",
            "--identitydigital-whois-min-interval",
            "0.3",
        ]
    )
    assert args.pattern == ["*.com", "*go.com"]
    assert args.formats == "txt"
    assert args.stats_interval == 1.5
    assert args.quiet is True
    assert args.export_wildcard_token == "star"
    assert args.cache_db == "/tmp/custom-cache.sqlite3"
    assert args.identitydigital_min_interval == 1.25
    assert args.identitydigital_whois_min_interval == 0.3
    assert args.fail_fast is False


def test_progress_line_includes_speed_values():
    line = _progress_line(
        "*.com",
        {
            "progress_processed": 10,
            "total_candidates": 100,
            "available_count": 3,
            "taken_count": 6,
            "unknown_count": 1,
            "invalid_count": 0,
            "duplicate_count": 0,
        },
        network_request_rps=2.5,
        network_request_avg_rps=1.25,
        eta_seconds=61,
    )
    assert "[" in line and "]" in line
    assert "%" in line
    assert "net=2.50/s" in line
    assert "avg=1.25/s" in line
    assert "eta=01:01" in line


def test_parse_patterns_text_ignores_blank_and_comments():
    lines = parse_patterns_text(
        """
        # comment
        *.com

        *go.com
        """
    )
    assert lines == ["*.com", "*go.com"]


def test_read_patterns_source_from_file_and_stdin(tmp_path, monkeypatch):
    path = tmp_path / "patterns.txt"
    path.write_text("*.com\n*go.com\n", encoding="utf-8")
    assert read_patterns_source(str(path)) == ["*.com", "*go.com"]

    class FakeStdin:
        def read(self):
            return "alpha*.com\n#comment\n\n"

    monkeypatch.setattr("sys.stdin", FakeStdin())
    assert read_patterns_source("-") == ["alpha*.com"]


def test_resolve_patterns_with_resume_skips_completed(tmp_path):
    resume = tmp_path / "resume.json"
    payload = {
        "patterns": [
            {"pattern": "*.com", "status": "completed"},
            {"pattern": "*go.com", "status": "failed"},
        ]
    }
    resume.write_text(json.dumps(payload), encoding="utf-8")
    patterns, skipped, resumed = resolve_patterns(
        cli_patterns=[],
        patterns_file=None,
        resume_path=str(resume),
    )
    assert patterns == ["*go.com"]
    assert skipped == ["*.com"]
    assert resumed is not None


def test_resolve_patterns_resume_matches_normalized_equivalent_pattern(tmp_path):
    resume = tmp_path / "resume.json"
    payload = {
        "patterns": [
            {"pattern": " *.COM ", "status": "completed"},
            {"pattern": "*go.com", "status": "failed"},
        ]
    }
    resume.write_text(json.dumps(payload), encoding="utf-8")
    patterns, skipped, _ = resolve_patterns(
        cli_patterns=["*.com", "*go.com"],
        patterns_file=None,
        resume_path=str(resume),
    )
    assert patterns == ["*go.com"]
    assert skipped == ["*.com"]


def test_write_empty_summary_creates_batch_file(tmp_path):
    summary_path = write_empty_summary(
        output_dir=tmp_path / "out",
        formats="txt",
        force_recheck=False,
        concurrency=5,
        fail_fast=False,
    )
    assert Path(summary_path).exists()
    payload = json.loads(Path(summary_path).read_text(encoding="utf-8"))
    assert payload["patterns"] == []
