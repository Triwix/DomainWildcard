import asyncio
import httpx
import pytest

from app.bootstrap import RDAPBootstrapResolver


def _load_resolver(payload):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    async def run():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            resolver = RDAPBootstrapResolver(bootstrap_url="https://bootstrap.test/dns.json")
            await resolver.ensure_loaded(client)
            return resolver

    return asyncio.run(run())


def test_resolver_uses_longest_matching_suffix():
    resolver = _load_resolver(
        {
            "services": [
                [["uk"], ["https://rdap.uk/"]],
                [["co.uk"], ["https://rdap.co.uk/"]],
                [["com"], ["https://rdap.com/"]],
            ]
        }
    )
    assert resolver.resolve_base_url("alpha.co.uk") == "https://rdap.co.uk"
    assert resolver.resolve_base_url("alpha.uk") == "https://rdap.uk"
    assert resolver.resolve_base_url("alpha.com") == "https://rdap.com"


def test_resolver_uses_iana_ai_mapping_when_present():
    resolver = _load_resolver(
        {
            "services": [
                [["ai"], ["https://rdap.iana-ai.test/rdap/"]],
                [["com"], ["https://rdap.com/"]],
            ]
        }
    )
    assert resolver.resolve_base_url("alpha.ai") == "https://rdap.iana-ai.test/rdap"


def test_resolver_uses_ai_fallback_when_bootstrap_missing_ai():
    resolver = _load_resolver({"services": [[["com"], ["https://rdap.com/"]]]})
    assert resolver.resolve_base_url("alpha.ai") == "https://rdap.identitydigital.services/rdap"


@pytest.mark.parametrize(
    ("domain", "expected"),
    [
        ("alpha.net", "https://rdap.verisign.com/net/v1"),
        ("alpha.org", "https://rdap.publicinterestregistry.org/rdap"),
        ("alpha.io", "https://rdap.identitydigital.services/rdap"),
        ("alpha.info", "https://rdap.identitydigital.services/rdap"),
        ("alpha.co", "https://rdap.registry.co/co"),
        ("alpha.xyz", "https://rdap.centralnic.com/xyz"),
        ("alpha.shop", "https://rdap.gmoregistry.net/rdap"),
        ("alpha.store", "https://rdap.radix.host/rdap"),
        ("alpha.online", "https://rdap.radix.host/rdap"),
        ("alpha.de", "https://rdap.denic.de"),
        ("alpha.uk", "https://rdap.nominet.uk/uk"),
        ("alpha.nl", "https://rdap.sidn.nl"),
        ("alpha.br", "https://rdap.registro.br"),
        ("alpha.au", "https://rdap.cctld.au/rdap"),
    ],
)
def test_resolver_uses_requested_tld_fallbacks_when_bootstrap_missing(domain, expected):
    resolver = _load_resolver({"services": [[["com"], ["https://rdap.com/"]]]})
    assert resolver.resolve_base_url(domain) == expected


def test_resolver_returns_none_for_unresolved_non_fallback_suffix():
    resolver = _load_resolver({"services": [[["com"], ["https://rdap.com/"]]]})
    assert resolver.resolve_base_url("alpha.unknownsuffix") is None
    assert resolver.resolve_base_url("alpha.cn") is None
    assert resolver.resolve_base_url("alpha.ru") is None


def test_resolver_prefers_iana_matches_over_fallback_and_keeps_longest_suffix():
    resolver = _load_resolver(
        {
            "services": [
                [["ai"], ["https://rdap.iana-ai.test/"]],
                [["co.ai"], ["https://rdap.co-ai.test/"]],
            ]
        }
    )
    assert resolver.resolve_base_url("alpha.ai") == "https://rdap.iana-ai.test"
    assert resolver.resolve_base_url("alpha.co.ai") == "https://rdap.co-ai.test"
