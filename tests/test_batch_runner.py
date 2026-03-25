import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.batch_runner import ensure_unique_path, parse_formats, run_batch, sanitize_pattern_for_filename
from app.models import DomainResult
from app.result_cache import DomainResultCache


class FakeRDAPClient:
    def __init__(self):
        self.seen = []

    async def check_domain(self, domain: str) -> DomainResult:
        self.seen.append(domain)
        await asyncio.sleep(0)
        if domain.startswith("free"):
            return DomainResult(
                domain=domain,
                state="available",
                rdap_host="fake",
                http_status=404,
                source="rdap:fake",
                checked_at="2026-03-17T00:00:00+00:00",
                ttl_seconds=600,
                expires_at="2026-03-17T00:10:00+00:00",
                from_cache=False,
            )
        if domain.startswith("taken"):
            return DomainResult(
                domain=domain,
                state="taken",
                rdap_host="fake",
                http_status=200,
                source="rdap:fake",
                checked_at="2026-03-17T00:00:00+00:00",
                ttl_seconds=600,
                expires_at="2026-03-17T00:10:00+00:00",
                from_cache=False,
            )
        return DomainResult(
            domain=domain,
            state="unknown",
            rdap_host="fake",
            http_status=503,
            error="mock",
            source="rdap:fake",
            checked_at="2026-03-17T00:00:00+00:00",
            ttl_seconds=300,
            expires_at="2026-03-17T00:05:00+00:00",
            from_cache=False,
        )


class SlowAvailableRDAPClient:
    async def check_domain(self, domain: str) -> DomainResult:
        await asyncio.sleep(0.2)
        return DomainResult(
            domain=domain,
            state="available",
            rdap_host="fake",
            http_status=404,
            source="rdap:fake",
            checked_at="2026-03-17T00:00:00+00:00",
            ttl_seconds=600,
            expires_at="2026-03-17T00:10:00+00:00",
            from_cache=False,
        )


class RaisingRDAPClient:
    async def check_domain(self, domain: str) -> DomainResult:  # pragma: no cover - should not be called
        raise RuntimeError(f"unexpected network call for {domain}")


def write_wordlist(path: Path, lines):
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_parse_formats_supports_csv_list_and_rejects_invalid():
    assert parse_formats(None) == ["txt"]
    assert parse_formats("txt,csv,txt,json") == ["txt", "csv", "json"]
    assert parse_formats(["json", "txt"]) == ["json", "txt"]

    with pytest.raises(ValueError):
        parse_formats("txt,xml")


def test_run_batch_rejects_zero_concurrency(tmp_path):
    wordlist = tmp_path / "words.txt"
    write_wordlist(wordlist, ["free"])
    with pytest.raises(ValueError):
        asyncio.run(
            run_batch(
                patterns=["*example.com"],
                wordlist_path=wordlist,
                output_dir=tmp_path / "Output",
                rdap_client_override=FakeRDAPClient(),
                concurrency=0,
            )
        )


def test_run_batch_rejects_cache_only_with_force_recheck(tmp_path):
    wordlist = tmp_path / "words.txt"
    write_wordlist(wordlist, ["free"])
    with pytest.raises(ValueError):
        asyncio.run(
            run_batch(
                patterns=["*example.com"],
                wordlist_path=wordlist,
                output_dir=tmp_path / "Output",
                cache_only=True,
                force_recheck=True,
            )
        )


def test_ensure_unique_path_appends_numeric_suffix(tmp_path):
    first = tmp_path / "alpha.txt"
    first.write_text("x", encoding="utf-8")
    second = ensure_unique_path(first)
    assert second.name == "alpha-2.txt"

    second.write_text("y", encoding="utf-8")
    third = ensure_unique_path(first)
    assert third.name == "alpha-3.txt"


def test_sanitize_pattern_for_filename_defaults_to_w_and_supports_override():
    assert sanitize_pattern_for_filename("*example.com") == "wexample.com"
    assert sanitize_pattern_for_filename("*example.com", wildcard_token="star") == "starexample.com"
    assert sanitize_pattern_for_filename("*example.com", wildcard_token="@@") == "wexample.com"


def test_run_batch_writes_selected_formats_and_summary(tmp_path):
    wordlist = tmp_path / "words.txt"
    write_wordlist(wordlist, ["free", "taken", "free2"])
    output_dir = tmp_path / "Output"
    fake = FakeRDAPClient()

    summary = asyncio.run(
        run_batch(
            patterns=["*example.com", "*go.com"],
            wordlist_path=wordlist,
            formats="txt,csv,json",
            output_dir=output_dir,
            rdap_client_override=fake,
        )
    )

    assert [item.pattern for item in summary.patterns] == ["*example.com", "*go.com"]
    assert all(item.status == "completed" for item in summary.patterns)
    assert Path(summary.summary_path).exists()

    for item in summary.patterns:
        assert len(item.output_files) == 3
        txt_path = Path([path for path in item.output_files if path.endswith(".txt")][0])
        csv_path = Path([path for path in item.output_files if path.endswith(".csv")][0])
        json_path = Path([path for path in item.output_files if path.endswith(".json") and "batch-" not in Path(path).name][0])

        txt_lines = txt_path.read_text(encoding="utf-8").strip().splitlines()
        assert all(line.endswith(".com") for line in txt_lines)
        assert "taken" not in "\n".join(txt_lines)

        csv_lines = csv_path.read_text(encoding="utf-8").splitlines()
        assert csv_lines[0] == "domain,state,source,checked_at,ttl_seconds,expires_at,from_cache"
        assert any("available,rdap:fake,2026-03-17T00:00:00+00:00,600,2026-03-17T00:10:00+00:00,False" in row for row in csv_lines[1:])

        json_payload = json.loads(json_path.read_text(encoding="utf-8"))
        assert "results" in json_payload
        assert json_payload["results"]
        first = json_payload["results"][0]
        assert "source" in first
        assert "checked_at" in first
        assert "ttl_seconds" in first
        assert "from_cache" in first


def test_run_batch_export_filename_uses_default_and_custom_wildcard_token(tmp_path):
    wordlist = tmp_path / "words.txt"
    write_wordlist(wordlist, ["free"])

    default_summary = asyncio.run(
        run_batch(
            patterns=["*example.com"],
            wordlist_path=wordlist,
            formats="txt",
            output_dir=tmp_path / "default",
            rdap_client_override=FakeRDAPClient(),
        )
    )
    default_file = Path(default_summary.patterns[0].output_files[0]).name
    assert default_file.startswith("wexample.com-")

    custom_summary = asyncio.run(
        run_batch(
            patterns=["*example.com"],
            wordlist_path=wordlist,
            formats="txt",
            wildcard_token="star",
            output_dir=tmp_path / "custom",
            rdap_client_override=FakeRDAPClient(),
        )
    )
    custom_file = Path(custom_summary.patterns[0].output_files[0]).name
    assert custom_file.startswith("starexample.com-")


def test_run_batch_continue_on_error_and_fail_fast(tmp_path):
    wordlist = tmp_path / "words.txt"
    write_wordlist(wordlist, ["free"])

    continue_summary = asyncio.run(
        run_batch(
            patterns=["example.com", "*ok.com"],
            wordlist_path=wordlist,
            output_dir=tmp_path / "continue",
            rdap_client_override=FakeRDAPClient(),
        )
    )
    assert len(continue_summary.patterns) == 2
    assert continue_summary.patterns[0].status == "failed"
    assert continue_summary.patterns[1].status == "completed"

    fail_fast_summary = asyncio.run(
        run_batch(
            patterns=["example.com", "*ok.com"],
            wordlist_path=wordlist,
            output_dir=tmp_path / "failfast",
            rdap_client_override=FakeRDAPClient(),
            fail_fast=True,
        )
    )
    assert len(fail_fast_summary.patterns) == 1
    assert fail_fast_summary.patterns[0].status == "failed"


def test_two_wildcards_secondary_and_primary_reuse(tmp_path):
    primary = tmp_path / "primary.txt"
    secondary = tmp_path / "secondary.txt"
    write_wordlist(primary, ["free", "taken"])
    write_wordlist(secondary, ["one"])

    fake_with_secondary = FakeRDAPClient()
    with_secondary = asyncio.run(
        run_batch(
            patterns=["*-*.com"],
            wordlist_path=primary,
            wordlist_secondary_path=secondary,
            output_dir=tmp_path / "with_secondary",
            rdap_client_override=fake_with_secondary,
        )
    )
    assert with_secondary.patterns[0].status == "completed"
    assert with_secondary.patterns[0].counts["available_count"] == 1
    assert set(fake_with_secondary.seen) == {"free-one.com", "taken-one.com"}

    fake_reuse = FakeRDAPClient()
    reuse_primary = asyncio.run(
        run_batch(
            patterns=["*-*.com"],
            wordlist_path=primary,
            output_dir=tmp_path / "reuse_primary",
            rdap_client_override=fake_reuse,
        )
    )
    assert reuse_primary.patterns[0].status == "completed"
    assert reuse_primary.patterns[0].counts["available_count"] == 2
    assert set(fake_reuse.seen) == {"free-free.com", "free-taken.com", "taken-free.com", "taken-taken.com"}


def test_three_and_four_wildcards_apply_secondary_to_positions_two_to_n(tmp_path):
    primary = tmp_path / "primary.txt"
    secondary = tmp_path / "secondary.txt"
    write_wordlist(primary, ["free", "taken"])
    write_wordlist(secondary, ["one", "two"])

    fake_three = FakeRDAPClient()
    three = asyncio.run(
        run_batch(
            patterns=["*-*-*example.com"],
            wordlist_path=primary,
            wordlist_secondary_path=secondary,
            output_dir=tmp_path / "three",
            rdap_client_override=fake_three,
        )
    )
    assert three.patterns[0].status == "completed"
    assert three.patterns[0].counts["available_count"] == 4
    assert three.patterns[0].counts["taken_count"] == 4

    fake_four = FakeRDAPClient()
    four = asyncio.run(
        run_batch(
            patterns=["*-*-*-*example.com"],
            wordlist_path=primary,
            wordlist_secondary_path=secondary,
            output_dir=tmp_path / "four",
            rdap_client_override=fake_four,
        )
    )
    assert four.patterns[0].status == "completed"
    assert four.patterns[0].counts["available_count"] == 8
    assert four.patterns[0].counts["taken_count"] == 8
    assert "free-one-one-oneexample.com" in set(fake_four.seen)
    assert "taken-two-two-twoexample.com" in set(fake_four.seen)


def test_run_batch_graceful_stop_exports_partial_results(tmp_path):
    wordlist = tmp_path / "words.txt"
    write_wordlist(wordlist, [f"w{i}" for i in range(30)])

    polls = {"count": 0}

    def stop_requested() -> bool:
        polls["count"] += 1
        return polls["count"] >= 2

    summary = asyncio.run(
        run_batch(
            patterns=["*example.com", "*next.com"],
            wordlist_path=wordlist,
            output_dir=tmp_path / "Output",
            formats="txt",
            concurrency=1,
            stop_requested=stop_requested,
            rdap_client_override=SlowAvailableRDAPClient(),
        )
    )

    assert len(summary.patterns) == 1
    first = summary.patterns[0]
    assert first.status == "cancelled"
    assert first.output_files
    assert Path(first.output_files[0]).exists()


def test_run_batch_dry_run_does_not_execute_checks(tmp_path):
    wordlist = tmp_path / "words.txt"
    write_wordlist(wordlist, ["free", "taken"])
    summary = asyncio.run(
        run_batch(
            patterns=["*example.com"],
            wordlist_path=wordlist,
            output_dir=tmp_path / "Output",
            dry_run=True,
            rdap_client_override=RaisingRDAPClient(),
        )
    )
    assert len(summary.patterns) == 1
    first = summary.patterns[0]
    assert first.status == "completed"
    assert first.counts["total_candidates"] == 2
    assert first.counts["processed"] == 0
    assert first.output_files == []
    assert first.error == "Dry run: no RDAP checks executed."


def test_run_batch_dry_run_accepts_manual_speed_overrides(tmp_path):
    wordlist = tmp_path / "words.txt"
    write_wordlist(wordlist, ["free", "taken"])
    summary = asyncio.run(
        run_batch(
            patterns=["*example.com"],
            wordlist_path=wordlist,
            output_dir=tmp_path / "Output",
            dry_run=True,
            verisign_min_interval_seconds=0.02,
            publicinterestregistry_min_interval_seconds=0.2,
            identitydigital_min_interval_seconds=1.5,
            registry_co_min_interval_seconds=0.05,
            rdap_client_override=RaisingRDAPClient(),
        )
    )
    assert len(summary.patterns) == 1
    assert summary.patterns[0].status == "completed"


def test_run_batch_cache_only_uses_local_cache(tmp_path):
    cache_db = tmp_path / "cache.sqlite3"

    async def seed_cache():
        cache = DomainResultCache(cache_db)
        now = datetime.now(timezone.utc)
        await cache.put(
            domain="freeexample.com",
            state="available",
            rdap_host="rdap.example",
            http_status=404,
            error=None,
            source="rdap:rdap.example",
            checked_at=now.isoformat(),
            ttl_seconds=600,
            expires_at=(now + timedelta(seconds=600)).isoformat(),
        )
        await cache.close()

    asyncio.run(seed_cache())

    wordlist = tmp_path / "words.txt"
    write_wordlist(wordlist, ["free", "taken"])
    summary = asyncio.run(
        run_batch(
            patterns=["*example.com"],
            wordlist_path=wordlist,
            output_dir=tmp_path / "Output",
            cache_only=True,
            formats="txt",
            cache_db_path=cache_db,
        )
    )
    first = summary.patterns[0]
    assert first.status == "completed"
    assert first.counts["cache_hits"] == 1
    assert first.counts["cache_misses"] == 1
    assert first.counts["available_count"] == 1
    txt_path = Path(first.output_files[0])
    content = txt_path.read_text(encoding="utf-8")
    assert "freeexample.com" in content
    assert "takenexample.com" not in content
