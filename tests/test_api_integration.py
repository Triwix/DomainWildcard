import asyncio
import re
import time

from fastapi.testclient import TestClient

from app.jobs import JobManager
from app.main import app
from app.models import DomainResult


class FakeRDAPClient:
    async def check_domain(self, domain: str) -> DomainResult:
        await asyncio.sleep(0)
        if domain.startswith("free"):
            return DomainResult(domain=domain, state="available", rdap_host="fake", http_status=404)
        if domain.startswith("taken"):
            return DomainResult(domain=domain, state="taken", rdap_host="fake", http_status=200)
        return DomainResult(domain=domain, state="unknown", rdap_host="fake", http_status=503, error="mock")


class SlowRDAPClient:
    async def check_domain(self, domain: str) -> DomainResult:
        await asyncio.sleep(0.3)
        return DomainResult(domain=domain, state="available", rdap_host="fake", http_status=404)


def wait_for_completion(client: TestClient, job_id: str, timeout_seconds: float = 5.0):
    deadline = time.time() + timeout_seconds
    snapshot = None
    while time.time() < deadline:
        response = client.get(f"/api/jobs/{job_id}")
        assert response.status_code == 200
        snapshot = response.json()
        if snapshot["status"] in {"completed", "failed", "cancelled"}:
            return snapshot
        time.sleep(0.05)
    raise AssertionError("Job did not complete before timeout")


def assert_export_filename(header_value: str, expected_prefix: str, extension: str):
    pattern = re.compile(
        rf"^attachment;\s*filename=([a-z0-9.-]+-\d{{4}}-\d{{2}}-\d{{2}}-\d{{2}}-\d{{2}}-\d{{2}}\.{re.escape(extension)})$"
    )
    assert header_value is not None
    match = pattern.match(header_value)
    assert match, header_value
    filename = match.group(1)
    assert filename.startswith(f"{expected_prefix}-"), filename


def test_index_and_static_cache_headers():
    with TestClient(app) as client:
        index = client.get("/")
        assert index.status_code == 200
        assert "no-cache" in (index.headers.get("cache-control") or "")

        static_js = client.get("/static/app.js")
        assert static_js.status_code == 200
        cache_control = static_js.headers.get("cache-control") or ""
        assert "max-age=" in cache_control or "no-cache" in cache_control


def test_job_lifecycle_and_exports():
    with TestClient(app) as client:
        client.app.state.jobs = JobManager(FakeRDAPClient(), concurrency=5)

        files = {"wordlist": ("words.txt", b"free\ntaken\nfree2\nweird\n", "text/plain")}
        data = {"pattern": "*example.com"}

        create_response = client.post("/api/jobs", data=data, files=files)
        assert create_response.status_code == 200
        job_id = create_response.json()["job_id"]

        snapshot = wait_for_completion(client, job_id)
        assert snapshot["status"] == "completed"
        assert snapshot["available_count"] == 2
        assert snapshot["taken_count"] == 1
        assert snapshot["unknown_count"] == 1
        assert snapshot["cache_hits"] == 0
        assert snapshot["cache_misses"] == 4
        assert snapshot["available_domains"] == ["freeexample.com", "free2example.com"]

        txt = client.get(f"/api/jobs/{job_id}/export.txt")
        assert txt.status_code == 200
        assert_export_filename(txt.headers.get("content-disposition"), expected_prefix="wexample.com", extension="txt")
        assert txt.text.strip().splitlines() == ["freeexample.com", "free2example.com"]

        txt_recent = client.get(f"/api/jobs/{job_id}/export.txt?sort=recent")
        assert txt_recent.status_code == 200
        assert txt_recent.text.strip().splitlines() == ["free2example.com", "freeexample.com"]

        csv = client.get(f"/api/jobs/{job_id}/export.csv?sort=az&q=2")
        assert csv.status_code == 200
        assert_export_filename(csv.headers.get("content-disposition"), expected_prefix="wexample.com", extension="csv")
        assert csv.text.splitlines()[0] == "domain,state,source,checked_at,ttl_seconds,expires_at,from_cache"
        assert "free2example.com,available" in csv.text
        assert "freeexample.com,available" not in csv.text

        csv_custom_token = client.get(f"/api/jobs/{job_id}/export.csv?sort=az&q=2&wildcard_token=star")
        assert csv_custom_token.status_code == 200
        assert_export_filename(csv_custom_token.headers.get("content-disposition"), expected_prefix="starexample.com", extension="csv")

        json_export = client.get(f"/api/jobs/{job_id}/export.json?sort=len_desc")
        assert json_export.status_code == 200
        assert_export_filename(
            json_export.headers.get("content-disposition"),
            expected_prefix="wexample.com",
            extension="json",
        )
        payload = json_export.json()
        assert [item["domain"] for item in payload["results"]] == ["free2example.com", "freeexample.com"]
        assert all("state" in item for item in payload["results"])
        assert all("source" in item for item in payload["results"])
        assert all("checked_at" in item for item in payload["results"])
        assert all("ttl_seconds" in item for item in payload["results"])


def test_job_creation_accepts_generated_editor_snapshot_payload():
    with TestClient(app) as client:
        client.app.state.jobs = JobManager(FakeRDAPClient(), concurrency=5)

        snapshot_words = ["free", "taken", "free2"]
        snapshot_text = "\n".join(snapshot_words) + "\n"
        files = {"wordlist": ("editor-wordlist.txt", snapshot_text.encode("utf-8"), "text/plain")}
        data = {"pattern": "*example.com"}

        create_response = client.post("/api/jobs", data=data, files=files)
        assert create_response.status_code == 200
        job_id = create_response.json()["job_id"]

        snapshot = wait_for_completion(client, job_id)
        assert snapshot["status"] == "completed"
        assert snapshot["available_count"] == 2
        assert snapshot["taken_count"] == 1


def test_events_endpoint_streams_snapshot_and_updates():
    with TestClient(app) as client:
        client.app.state.jobs = JobManager(FakeRDAPClient(), concurrency=5)

        files = {"wordlist": ("words.txt", b"free\ntaken\n", "text/plain")}
        data = {"pattern": "*example.com"}
        create_response = client.post("/api/jobs", data=data, files=files)
        job_id = create_response.json()["job_id"]

        seen_snapshot = False
        seen_progress = False
        seen_available_batch = False

        with client.stream("GET", f"/api/jobs/{job_id}/events") as response:
            assert response.status_code == 200
            for line in response.iter_lines():
                if not line:
                    continue
                if line.startswith("event: snapshot"):
                    seen_snapshot = True
                if line.startswith("event: progress"):
                    seen_progress = True
                if line.startswith("event: available_batch"):
                    seen_available_batch = True
                if line.startswith("event: completed"):
                    break

        assert seen_snapshot
        assert seen_progress
        assert seen_available_batch


def test_two_wildcards_accept_optional_secondary_wordlist():
    with TestClient(app) as client:
        client.app.state.jobs = JobManager(FakeRDAPClient(), concurrency=5)

        files = {
            "wordlist": ("first.txt", b"free\ntaken\n", "text/plain"),
            "wordlist_secondary": ("second.txt", b"one\n", "text/plain"),
        }
        data = {"pattern": "*-*example.com"}
        create_response = client.post("/api/jobs", data=data, files=files)
        assert create_response.status_code == 200
        job_id = create_response.json()["job_id"]

        snapshot = wait_for_completion(client, job_id)
        assert snapshot["status"] == "completed"
        assert snapshot["available_count"] == 1
        assert snapshot["taken_count"] == 1
        assert snapshot["available_domains"] == ["free-oneexample.com"]


def test_two_wildcards_without_secondary_reuses_primary_list():
    with TestClient(app) as client:
        client.app.state.jobs = JobManager(FakeRDAPClient(), concurrency=5)

        files = {"wordlist": ("first.txt", b"free\ntaken\n", "text/plain")}
        data = {"pattern": "*-*example.com"}
        create_response = client.post("/api/jobs", data=data, files=files)
        assert create_response.status_code == 200
        job_id = create_response.json()["job_id"]

        snapshot = wait_for_completion(client, job_id)
        assert snapshot["status"] == "completed"
        assert snapshot["available_count"] == 2
        assert snapshot["taken_count"] == 2


def test_three_wildcards_accept_secondary_wordlist_for_positions_two_and_three():
    with TestClient(app) as client:
        client.app.state.jobs = JobManager(FakeRDAPClient(), concurrency=5)

        files = {
            "wordlist": ("first.txt", b"free\ntaken\n", "text/plain"),
            "wordlist_secondary": ("second.txt", b"one\ntwo\n", "text/plain"),
        }
        data = {"pattern": "*-*-*example.com"}
        create_response = client.post("/api/jobs", data=data, files=files)
        assert create_response.status_code == 200
        job_id = create_response.json()["job_id"]

        snapshot = wait_for_completion(client, job_id)
        assert snapshot["status"] == "completed"
        assert snapshot["available_count"] == 4
        assert snapshot["taken_count"] == 4


def test_four_wildcards_without_secondary_reuses_primary_for_positions_two_to_four():
    with TestClient(app) as client:
        client.app.state.jobs = JobManager(FakeRDAPClient(), concurrency=5)

        files = {"wordlist": ("first.txt", b"free\ntaken\n", "text/plain")}
        data = {"pattern": "*-*-*-*example.com"}
        create_response = client.post("/api/jobs", data=data, files=files)
        assert create_response.status_code == 200
        job_id = create_response.json()["job_id"]

        snapshot = wait_for_completion(client, job_id)
        assert snapshot["status"] == "completed"
        assert snapshot["available_count"] == 8
        assert snapshot["taken_count"] == 8


def test_cancel_running_job():
    with TestClient(app) as client:
        client.app.state.jobs = JobManager(SlowRDAPClient(), concurrency=1)

        files = {"wordlist": ("words.txt", b"alpha\nbeta\ngamma\n", "text/plain")}
        data = {"pattern": "*example.com"}
        create_response = client.post("/api/jobs", data=data, files=files)
        job_id = create_response.json()["job_id"]

        cancel_response = client.post(f"/api/jobs/{job_id}/cancel")
        assert cancel_response.status_code == 200
        assert cancel_response.json()["status"] == "cancelled"

        snapshot = wait_for_completion(client, job_id)
        assert snapshot["status"] == "cancelled"


def test_rejects_wordlist_over_max_limit():
    with TestClient(app) as client:
        client.app.state.jobs = JobManager(FakeRDAPClient(), concurrency=5)

        oversized = "\n".join([f"w{i}" for i in range(50001)]) + "\n"
        files = {"wordlist": ("oversized.txt", oversized.encode("utf-8"), "text/plain")}
        data = {"pattern": "*example.com"}

        response = client.post("/api/jobs", data=data, files=files)
        assert response.status_code == 400
        assert "max of 50000 words" in response.json()["detail"]


def test_rejects_expansion_over_candidate_cap():
    with TestClient(app) as client:
        client.app.state.jobs = JobManager(FakeRDAPClient(), concurrency=5)

        first = "\n".join([f"a{i}" for i in range(1001)]) + "\n"
        second = "\n".join([f"b{i}" for i in range(1001)]) + "\n"
        files = {
            "wordlist": ("first.txt", first.encode("utf-8"), "text/plain"),
            "wordlist_secondary": ("second.txt", second.encode("utf-8"), "text/plain"),
        }
        data = {"pattern": "*-*.com"}

        response = client.post("/api/jobs", data=data, files=files)
        assert response.status_code == 400
        assert "exceeds current max of 1000000" in response.json()["detail"]


def test_rejects_upload_over_size_limit():
    with TestClient(app) as client:
        client.app.state.jobs = JobManager(FakeRDAPClient(), concurrency=5)

        too_large = b"a" * ((8 * 1024 * 1024) + 1)
        files = {"wordlist": ("huge.txt", too_large, "text/plain")}
        data = {"pattern": "*example.com"}

        response = client.post("/api/jobs", data=data, files=files)
        assert response.status_code == 413
        assert "Upload exceeds" in response.json()["detail"]


def test_rate_status_endpoint_works_with_mock_clients():
    with TestClient(app) as client:
        client.app.state.jobs = JobManager(FakeRDAPClient(), concurrency=5)
        response = client.get("/api/rate-status")
        assert response.status_code == 200
        assert response.json() == {"hosts": []}


def test_rate_config_endpoints_apply_and_clear_overrides():
    with TestClient(app) as client:
        config = client.get("/api/rate-config")
        assert config.status_code == 200
        payload = config.json()
        assert "supported_hosts" in payload
        assert "rdap.identitydigital.services" in payload["supported_hosts"]

        apply_response = client.post(
            "/api/rate-config",
            json={
                "overrides": {
                    "rdap.identitydigital.services": 1.5,
                },
                "replace": True,
                "reset_backoff": True,
            },
        )
        assert apply_response.status_code == 200
        applied = apply_response.json()
        assert applied["overrides"]["rdap.identitydigital.services"] == 1.5

        clear_response = client.delete("/api/rate-config?reset_backoff=true")
        assert clear_response.status_code == 200
        cleared = clear_response.json()
        assert cleared["overrides"] == {}
