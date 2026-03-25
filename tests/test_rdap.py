import asyncio
import time
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.rdap import (
    DEFAULT_AU_MIN_INTERVAL_SECONDS,
    DEFAULT_CENTRALNIC_MIN_INTERVAL_SECONDS,
    DEFAULT_DENIC_MIN_INTERVAL_SECONDS,
    DEFAULT_GMOREGISTRY_MIN_INTERVAL_SECONDS,
    DEFAULT_VERISIGN_MIN_INTERVAL_SECONDS,
    DEFAULT_PUBLICINTERESTREGISTRY_MIN_INTERVAL_SECONDS,
    DEFAULT_IDENTITYDIGITAL_MIN_INTERVAL_SECONDS,
    DEFAULT_NOMINET_MIN_INTERVAL_SECONDS,
    DEFAULT_RADIX_MIN_INTERVAL_SECONDS,
    DEFAULT_REGISTRO_BR_MIN_INTERVAL_SECONDS,
    DEFAULT_REGISTRY_CO_MIN_INTERVAL_SECONDS,
    DEFAULT_SIDN_MIN_INTERVAL_SECONDS,
    DomainValidationError,
    HostRateLimiter,
    RDAPClient,
    build_default_known_policies,
    normalize_domain,
    parse_retry_after,
)
from app.result_cache import DomainResultCache


class StaticResolver:
    def __init__(self, base_url: str):
        self.base_url = base_url

    async def ensure_loaded(self, _client: httpx.AsyncClient, force: bool = False) -> None:
        return None

    def resolve_base_url(self, _domain: str):
        return self.base_url


class NoSleepLimiter:
    def __init__(self):
        self.forbidden_calls = 0

    async def acquire(self, _host: str) -> None:
        return None

    async def record_success(self, _host: str, _status_code: int) -> None:
        return None

    async def record_throttle(
        self,
        _host: str,
        _penalty_seconds: float,
        _retry_after_seconds,
        status_code: int = 429,
    ) -> None:
        _ = status_code
        return None

    async def record_server_error(self, _host: str, _penalty_seconds: float, _status_code: int) -> None:
        return None

    async def record_network_error(self, _host: str, _penalty_seconds: float, _error_text: str) -> None:
        return None

    async def record_forbidden(self, _host: str, _penalty_seconds: float, status_code: int = 403) -> None:
        _ = status_code
        self.forbidden_calls += 1
        return None


def test_normalize_domain_accepts_idn_and_rejects_invalid():
    assert normalize_domain("tést.com") == "xn--tst-bma.com"

    with pytest.raises(DomainValidationError):
        normalize_domain("bad domain.com")


def test_parse_retry_after_numeric_and_http_date():
    assert parse_retry_after("8") == 8.0
    assert parse_retry_after("not-a-date") is None

    now = datetime(2026, 3, 15, 12, 0, tzinfo=timezone.utc)
    future = (now + timedelta(seconds=25)).strftime("%a, %d %b %Y %H:%M:%S GMT")
    parsed = parse_retry_after(future, now=now)
    assert parsed == pytest.approx(25.0, abs=1.0)


@pytest.mark.parametrize(
    ("host", "default_value", "override_kwargs", "override_value"),
    [
        (
            "rdap.verisign.com",
            DEFAULT_VERISIGN_MIN_INTERVAL_SECONDS,
            {"verisign_min_interval_seconds": 0.02},
            0.02,
        ),
        (
            "rdap.publicinterestregistry.org",
            DEFAULT_PUBLICINTERESTREGISTRY_MIN_INTERVAL_SECONDS,
            {"publicinterestregistry_min_interval_seconds": 0.05},
            0.05,
        ),
        (
            "rdap.identitydigital.services",
            DEFAULT_IDENTITYDIGITAL_MIN_INTERVAL_SECONDS,
            {"identitydigital_min_interval_seconds": 0.01},
            0.01,
        ),
        (
            "rdap.registry.co",
            DEFAULT_REGISTRY_CO_MIN_INTERVAL_SECONDS,
            {"registry_co_min_interval_seconds": 0.03},
            0.03,
        ),
        (
            "rdap.centralnic.com",
            DEFAULT_CENTRALNIC_MIN_INTERVAL_SECONDS,
            {"centralnic_min_interval_seconds": 0.03},
            0.03,
        ),
        (
            "rdap.radix.host",
            DEFAULT_RADIX_MIN_INTERVAL_SECONDS,
            {"radix_min_interval_seconds": 0.2},
            0.2,
        ),
        (
            "rdap.denic.de",
            DEFAULT_DENIC_MIN_INTERVAL_SECONDS,
            {"denic_min_interval_seconds": 0.25},
            0.25,
        ),
        (
            "rdap.nominet.uk",
            DEFAULT_NOMINET_MIN_INTERVAL_SECONDS,
            {"nominet_min_interval_seconds": 0.3},
            0.3,
        ),
        (
            "rdap.sidn.nl",
            DEFAULT_SIDN_MIN_INTERVAL_SECONDS,
            {"sidn_min_interval_seconds": 1.5},
            1.5,
        ),
        (
            "rdap.registro.br",
            DEFAULT_REGISTRO_BR_MIN_INTERVAL_SECONDS,
            {"registro_br_min_interval_seconds": 0.04},
            0.04,
        ),
        (
            "rdap.gmoregistry.net",
            DEFAULT_GMOREGISTRY_MIN_INTERVAL_SECONDS,
            {"gmoregistry_min_interval_seconds": 2.5},
            2.5,
        ),
        (
            "rdap.cctld.au",
            DEFAULT_AU_MIN_INTERVAL_SECONDS,
            {"au_min_interval_seconds": 3.0},
            3.0,
        ),
    ],
)
def test_known_policy_floors_support_defaults_and_overrides(host, default_value, override_kwargs, override_value):
    default_policies = build_default_known_policies()
    default_policy = next(policy for policy in default_policies if policy.host_contains == host)
    assert default_policy.min_interval_seconds == pytest.approx(default_value)

    override_policies = build_default_known_policies(**override_kwargs)
    override_policy = next(policy for policy in override_policies if policy.host_contains == host)
    assert override_policy.min_interval_seconds == pytest.approx(override_value)


def test_host_rate_limiter_applies_verisign_policy_label_and_floor():
    limiter = HostRateLimiter(base_interval_seconds=1.0, min_interval_seconds=0.167)
    state = limiter._state_for_host("rdap.verisign.com")
    assert state.floor_interval_seconds == pytest.approx(DEFAULT_VERISIGN_MIN_INTERVAL_SECONDS)
    assert state.current_interval_seconds == pytest.approx(DEFAULT_VERISIGN_MIN_INTERVAL_SECONDS)
    assert "Verisign calibrated floor" in state.policy_label


def test_host_rate_limiter_max_interval_covers_known_policy_floors():
    limiter = HostRateLimiter(base_interval_seconds=1.0, min_interval_seconds=0.167, max_interval_seconds=2.0)
    assert limiter.max_interval_seconds >= 60.0


@pytest.mark.parametrize(
    ("host", "override_kwargs", "expected_floor", "label_fragment"),
    [
        (
            "rdap.publicinterestregistry.org",
            {"publicinterestregistry_min_interval_seconds": 0.05},
            0.05,
            "Public Interest Registry calibrated floor",
        ),
        (
            "rdap.identitydigital.services",
            {"identitydigital_min_interval_seconds": 0.01},
            0.01,
            "Identity Digital calibrated floor",
        ),
        (
            "rdap.registry.co",
            {"registry_co_min_interval_seconds": 0.03},
            0.03,
            "Registry.co calibrated floor",
        ),
    ],
)
def test_host_rate_limiter_applies_extended_host_policy_floor(host, override_kwargs, expected_floor, label_fragment):
    limiter = HostRateLimiter(
        base_interval_seconds=1.0,
        min_interval_seconds=0.167,
        known_policies=build_default_known_policies(**override_kwargs),
    )
    state = limiter._state_for_host(host)
    assert state.floor_interval_seconds == pytest.approx(expected_floor)
    assert state.current_interval_seconds == pytest.approx(expected_floor)
    assert label_fragment in state.policy_label


def test_host_rate_limiter_keeps_base_interval_for_non_policy_hosts():
    limiter = HostRateLimiter(base_interval_seconds=1.0, min_interval_seconds=0.167)
    state = limiter._state_for_host("rdap.example")
    assert state.floor_interval_seconds == pytest.approx(0.167)
    assert state.current_interval_seconds == pytest.approx(1.0)
    assert state.policy_label == "Adaptive default"


def test_host_rate_limiter_manual_overrides_apply_and_clear():
    async def run():
        limiter = HostRateLimiter(base_interval_seconds=1.0, min_interval_seconds=0.167)
        await limiter.acquire("rdap.verisign.com")
        await limiter.set_manual_overrides(
            {"rdap.verisign.com": 0.5},
            replace=True,
            reset_backoff=True,
        )
        after_set = limiter.get_status_snapshot()
        await limiter.clear_manual_overrides(reset_backoff=True)
        after_clear = limiter.get_status_snapshot()
        return after_set, after_clear, limiter.get_manual_override_snapshot()

    after_set, after_clear, overrides = asyncio.run(run())
    assert overrides == {}
    assert after_set[0]["manual_override"] is True
    assert after_set[0]["floor_interval_seconds"] == pytest.approx(0.5)
    assert "Manual override" in after_set[0]["policy"]
    assert after_clear[0]["manual_override"] is False
    assert after_clear[0]["floor_interval_seconds"] == pytest.approx(DEFAULT_VERISIGN_MIN_INTERVAL_SECONDS)


def test_host_rate_limiter_reset_backoff_clears_existing_penalty_window():
    async def run():
        host = "rdap.identitydigital.services"
        limiter = HostRateLimiter(base_interval_seconds=1.0, min_interval_seconds=0.167)
        await limiter.acquire(host)
        await limiter.record_throttle(host, penalty_seconds=30.0, retry_after_seconds=None, status_code=429)
        before_wait = limiter._state_for_host(host).next_allowed_monotonic - time.monotonic()
        await limiter.set_manual_overrides({host: 1.0}, replace=True, reset_backoff=True)
        after_wait = limiter._state_for_host(host).next_allowed_monotonic - time.monotonic()
        return before_wait, after_wait

    before_wait, after_wait = asyncio.run(run())
    assert before_wait > 1.0
    assert after_wait < before_wait


def test_rdap_client_classifies_200_and_404():
    responses = {
        "https://rdap.example/domain/taken.com": 200,
        "https://rdap.example/domain/free.com": 404,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        status = responses[str(request.url)]
        return httpx.Response(status_code=status)

    async def run():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            checker = RDAPClient(
                http_client=client,
                resolver=StaticResolver("https://rdap.example"),
                limiter=HostRateLimiter(base_interval_seconds=0),
                max_retries=2,
                base_backoff_seconds=0,
                jitter_seconds=0,
            )
            taken = await checker.check_domain("taken.com")
            available = await checker.check_domain("free.com")
            return taken, available

    taken, available = asyncio.run(run())
    assert taken.state == "taken"
    assert taken.http_status == 200
    assert available.state == "available"
    assert available.http_status == 404


def test_rdap_client_retries_on_429_then_succeeds():
    call_count = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        call_count["count"] += 1
        if call_count["count"] == 1:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(404)

    async def run():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            checker = RDAPClient(
                http_client=client,
                resolver=StaticResolver("https://rdap.example"),
                limiter=HostRateLimiter(base_interval_seconds=0),
                max_retries=3,
                base_backoff_seconds=0,
                jitter_seconds=0,
            )
            return await checker.check_domain("free.com")

    result = asyncio.run(run())
    assert call_count["count"] == 2
    assert result.state == "available"
    assert result.http_status == 404


def test_rdap_client_uses_head_probe_when_supported():
    methods = []

    def handler(request: httpx.Request) -> httpx.Response:
        methods.append(request.method)
        if request.method == "HEAD":
            return httpx.Response(404)
        return httpx.Response(500)

    async def run():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            checker = RDAPClient(
                http_client=client,
                resolver=StaticResolver("https://rdap.example"),
                limiter=HostRateLimiter(base_interval_seconds=0),
                max_retries=2,
                base_backoff_seconds=0,
                jitter_seconds=0,
            )
            checker._prefer_head_exists_probe = True
            return await checker.check_domain("free.com")

    result = asyncio.run(run())
    assert methods == ["HEAD"]
    assert result.state == "available"
    assert result.http_status == 404


def test_rdap_client_falls_back_to_get_when_head_not_supported():
    methods = []

    def handler(request: httpx.Request) -> httpx.Response:
        methods.append(request.method)
        if request.method == "HEAD":
            return httpx.Response(405)
        return httpx.Response(404)

    async def run():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            checker = RDAPClient(
                http_client=client,
                resolver=StaticResolver("https://rdap.example"),
                limiter=HostRateLimiter(base_interval_seconds=0),
                max_retries=2,
                base_backoff_seconds=0,
                jitter_seconds=0,
            )
            checker._prefer_head_exists_probe = True
            first = await checker.check_domain("free.com")
            second = await checker.check_domain("open.com")
            return first, second

    first, second = asyncio.run(run())
    assert methods == ["HEAD", "GET", "GET"]
    assert first.state == "available"
    assert second.state == "available"


def test_rdap_client_retries_on_403_then_succeeds():
    call_count = {"count": 0}
    limiter = NoSleepLimiter()

    def handler(_request: httpx.Request) -> httpx.Response:
        call_count["count"] += 1
        if call_count["count"] == 1:
            return httpx.Response(403)
        return httpx.Response(404)

    async def run():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            checker = RDAPClient(
                http_client=client,
                resolver=StaticResolver("https://rdap.example"),
                limiter=limiter,
                max_retries=3,
                base_backoff_seconds=0,
                jitter_seconds=0,
            )
            return await checker.check_domain("free.com")

    result = asyncio.run(run())
    assert call_count["count"] == 2
    assert limiter.forbidden_calls == 1
    assert result.state == "available"
    assert result.http_status == 404


def test_rdap_client_uses_ipv4_fallback_for_identity_digital_403():
    primary_methods = []
    ipv4_methods = []

    def primary_handler(request: httpx.Request) -> httpx.Response:
        primary_methods.append(request.method)
        return httpx.Response(403)

    def ipv4_handler(request: httpx.Request) -> httpx.Response:
        ipv4_methods.append(request.method)
        return httpx.Response(404)

    async def run():
        primary_transport = httpx.MockTransport(primary_handler)
        ipv4_transport = httpx.MockTransport(ipv4_handler)
        async with httpx.AsyncClient(transport=primary_transport) as primary_client:
            async with httpx.AsyncClient(transport=ipv4_transport) as ipv4_client:
                checker = RDAPClient(
                    http_client=primary_client,
                    resolver=StaticResolver("https://rdap.identitydigital.services/rdap"),
                    ipv4_http_client=ipv4_client,
                    limiter=HostRateLimiter(base_interval_seconds=0),
                    max_retries=1,
                    base_backoff_seconds=0,
                    jitter_seconds=0,
                )
                checker._prefer_head_exists_probe = True
                return await checker.check_domain("free.ai")

    result = asyncio.run(run())
    assert result.state == "available"
    assert result.http_status == 404
    assert primary_methods == ["HEAD"]
    assert ipv4_methods == ["HEAD"]


def test_rdap_client_does_not_use_ipv4_fallback_for_non_identitydigital_host():
    primary_calls = {"count": 0}
    ipv4_calls = {"count": 0}

    def primary_handler(_request: httpx.Request) -> httpx.Response:
        primary_calls["count"] += 1
        return httpx.Response(403)

    def ipv4_handler(_request: httpx.Request) -> httpx.Response:
        ipv4_calls["count"] += 1
        return httpx.Response(404)

    async def run():
        primary_transport = httpx.MockTransport(primary_handler)
        ipv4_transport = httpx.MockTransport(ipv4_handler)
        async with httpx.AsyncClient(transport=primary_transport) as primary_client:
            async with httpx.AsyncClient(transport=ipv4_transport) as ipv4_client:
                checker = RDAPClient(
                    http_client=primary_client,
                    resolver=StaticResolver("https://rdap.example"),
                    ipv4_http_client=ipv4_client,
                    limiter=NoSleepLimiter(),
                    max_retries=1,
                    base_backoff_seconds=0,
                    jitter_seconds=0,
                )
                return await checker.check_domain("blocked.com")

    result = asyncio.run(run())
    assert result.state == "unknown"
    assert result.http_status == 403
    assert primary_calls["count"] == 1
    assert ipv4_calls["count"] == 0


def test_rdap_client_uses_cache_and_force_recheck(tmp_path):
    call_count = {"count": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        call_count["count"] += 1
        return httpx.Response(404)

    async def run():
        cache = DomainResultCache(tmp_path / "rdap-cache.sqlite3")
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            checker = RDAPClient(
                http_client=client,
                resolver=StaticResolver("https://rdap.example"),
                limiter=HostRateLimiter(base_interval_seconds=0),
                result_cache=cache,
                max_retries=2,
                base_backoff_seconds=0,
                jitter_seconds=0,
                available_ttl_seconds=600,
            )
            first = await checker.check_domain("free.com")
            second = await checker.check_domain("free.com")
            third = await checker.check_domain("free.com", force_recheck=True)
        await cache.close()
        return first, second, third

    first, second, third = asyncio.run(run())
    assert call_count["count"] == 2
    assert first.state == "available"
    assert first.from_cache is False
    assert first.source == "rdap:rdap.example"
    assert second.from_cache is True
    assert second.source == "cache:rdap:rdap.example"
    assert second.ttl_seconds == 600
    assert third.from_cache is False


def test_rdap_client_does_not_cache_unknown_from_403(tmp_path):
    call_count = {"count": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        call_count["count"] += 1
        return httpx.Response(403)

    async def run():
        cache = DomainResultCache(tmp_path / "rdap-cache.sqlite3")
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            checker = RDAPClient(
                http_client=client,
                resolver=StaticResolver("https://rdap.example"),
                limiter=NoSleepLimiter(),
                result_cache=cache,
                max_retries=1,
                base_backoff_seconds=0,
                jitter_seconds=0,
            )
            first = await checker.check_domain("blocked.ai")
            second = await checker.check_domain("blocked.ai")
            cached = await cache.get("blocked.ai")
        await cache.close()
        return first, second, cached

    first, second, cached = asyncio.run(run())
    assert call_count["count"] == 2
    assert first.state == "unknown"
    assert first.http_status == 403
    assert first.from_cache is False
    assert second.from_cache is False
    assert cached is None
