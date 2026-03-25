from __future__ import annotations

import asyncio
import time
from typing import Dict, Optional

import httpx

_FALLBACK_RDAP_SERVICES: Dict[str, str] = {
    "ai": "https://rdap.identitydigital.services/rdap",
    "xyz": "https://rdap.centralnic.com/xyz",
    "shop": "https://rdap.gmoregistry.net/rdap",
    "store": "https://rdap.radix.host/rdap",
    "online": "https://rdap.radix.host/rdap",
    "net": "https://rdap.verisign.com/net/v1",
    "org": "https://rdap.publicinterestregistry.org/rdap",
    "io": "https://rdap.identitydigital.services/rdap",
    "info": "https://rdap.identitydigital.services/rdap",
    "co": "https://rdap.registry.co/co",
    "de": "https://rdap.denic.de",
    "uk": "https://rdap.nominet.uk/uk",
    "nl": "https://rdap.sidn.nl",
    "br": "https://rdap.registro.br",
    "au": "https://rdap.cctld.au/rdap",
}


class RDAPBootstrapResolver:
    def __init__(
        self,
        bootstrap_url: str = "https://data.iana.org/rdap/dns.json",
        cache_ttl_seconds: int = 24 * 60 * 60,
    ):
        self.bootstrap_url = bootstrap_url
        self.cache_ttl_seconds = cache_ttl_seconds
        self._lock = asyncio.Lock()
        self._expires_at = 0.0
        self._services: Dict[str, str] = {}
        self._fallback_services: Dict[str, str] = {
            suffix.strip().lower().lstrip("."): url.rstrip("/")
            for suffix, url in _FALLBACK_RDAP_SERVICES.items()
            if str(suffix).strip() and str(url).strip()
        }

    async def ensure_loaded(self, client: httpx.AsyncClient, force: bool = False) -> None:
        now = time.time()
        if not force and self._services and now < self._expires_at:
            return

        async with self._lock:
            now = time.time()
            if not force and self._services and now < self._expires_at:
                return

            response = await client.get(self.bootstrap_url, timeout=20.0)
            response.raise_for_status()
            payload = response.json()

            services: Dict[str, str] = {}
            for item in payload.get("services", []):
                if len(item) != 2:
                    continue
                suffixes, urls = item
                if not suffixes or not urls:
                    continue
                preferred = self._pick_url(urls)
                if not preferred:
                    continue
                for suffix in suffixes:
                    key = str(suffix).strip().lower().lstrip(".")
                    if key:
                        services[key] = preferred.rstrip("/")

            if not services:
                raise RuntimeError("IANA RDAP bootstrap payload contained no usable services")

            self._services = services
            self._expires_at = now + float(self.cache_ttl_seconds)

    @staticmethod
    def _pick_url(urls) -> Optional[str]:
        for url in urls:
            if str(url).lower().startswith("https://"):
                return str(url)
        if urls:
            return str(urls[0])
        return None

    def resolve_base_url(self, domain: str) -> Optional[str]:
        cleaned = domain.strip().lower().rstrip(".")
        if not cleaned:
            return None
        labels = cleaned.split(".")
        if len(labels) < 2:
            return None

        for i in range(len(labels)):
            suffix = ".".join(labels[i:])
            if suffix in self._services:
                return self._services[suffix]

        for i in range(len(labels)):
            suffix = ".".join(labels[i:])
            if suffix in self._fallback_services:
                return self._fallback_services[suffix]
        return None
