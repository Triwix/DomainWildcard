from __future__ import annotations

import asyncio
import contextlib
import email.utils
import os
import random
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple
from urllib.parse import quote

import httpx

from app.bootstrap import RDAPBootstrapResolver
from app.models import DomainResult
from app.result_cache import DomainResultCache

_ASCII_LABEL_RE = re.compile(r"^[a-z0-9-]{1,63}$")
_IPV4_FALLBACK_HOST_CONTAINS = ("rdap.identitydigital.services",)
_IDENTITYDIGITAL_WHOIS_HOST = "whois.nic.ai"
_IDENTITYDIGITAL_WHOIS_PORT = 43
_IDENTITYDIGITAL_WHOIS_TIMEOUT_SECONDS = 12.0
_IDENTITYDIGITAL_WHOIS_REFUSED_PENALTY_SECONDS = 24.0
_IDENTITYDIGITAL_WHOIS_NETWORK_PENALTY_SECONDS = 8.0


class DomainValidationError(ValueError):
    pass


@dataclass
class HostRatePolicy:
    host_contains: str
    min_interval_seconds: float
    label: str


@dataclass
class HostRuntime:
    host: str
    policy_label: str
    floor_interval_seconds: float
    current_interval_seconds: float
    next_allowed_monotonic: float = 0.0
    total_requests: int = 0
    total_429: int = 0
    total_5xx: int = 0
    total_errors: int = 0
    success_streak: int = 0
    last_status: Optional[int] = None
    last_error: Optional[str] = None
    last_retry_after_seconds: Optional[float] = None
    last_updated_epoch: float = 0.0


VERISIGN_MIN_INTERVAL_ENV = "RDAP_VERISIGN_MIN_INTERVAL_SECONDS"
DEFAULT_VERISIGN_MIN_INTERVAL_SECONDS = 0.0001
PUBLICINTERESTREGISTRY_MIN_INTERVAL_ENV = "RDAP_PUBLICINTERESTREGISTRY_MIN_INTERVAL_SECONDS"
DEFAULT_PUBLICINTERESTREGISTRY_MIN_INTERVAL_SECONDS = 0.02
IDENTITYDIGITAL_MIN_INTERVAL_ENV = "RDAP_IDENTITYDIGITAL_MIN_INTERVAL_SECONDS"
DEFAULT_IDENTITYDIGITAL_MIN_INTERVAL_SECONDS = 0.85
IDENTITYDIGITAL_WHOIS_MIN_INTERVAL_ENV = "RDAP_IDENTITYDIGITAL_WHOIS_MIN_INTERVAL_SECONDS"
DEFAULT_IDENTITYDIGITAL_WHOIS_MIN_INTERVAL_SECONDS = 0.2
REGISTRY_CO_MIN_INTERVAL_ENV = "RDAP_REGISTRY_CO_MIN_INTERVAL_SECONDS"
DEFAULT_REGISTRY_CO_MIN_INTERVAL_SECONDS = 0.0125
CENTRALNIC_MIN_INTERVAL_ENV = "RDAP_CENTRALNIC_MIN_INTERVAL_SECONDS"
DEFAULT_CENTRALNIC_MIN_INTERVAL_SECONDS = 0.025
GMOREGISTRY_MIN_INTERVAL_ENV = "RDAP_GMOREGISTRY_MIN_INTERVAL_SECONDS"
DEFAULT_GMOREGISTRY_MIN_INTERVAL_SECONDS = 2.0
RADIX_MIN_INTERVAL_ENV = "RDAP_RADIX_MIN_INTERVAL_SECONDS"
DEFAULT_RADIX_MIN_INTERVAL_SECONDS = 0.125
DENIC_MIN_INTERVAL_ENV = "RDAP_DENIC_MIN_INTERVAL_SECONDS"
DEFAULT_DENIC_MIN_INTERVAL_SECONDS = 0.15
NOMINET_MIN_INTERVAL_ENV = "RDAP_NOMINET_MIN_INTERVAL_SECONDS"
DEFAULT_NOMINET_MIN_INTERVAL_SECONDS = 0.15
SIDN_MIN_INTERVAL_ENV = "RDAP_SIDN_MIN_INTERVAL_SECONDS"
DEFAULT_SIDN_MIN_INTERVAL_SECONDS = 1.0
REGISTRO_BR_MIN_INTERVAL_ENV = "RDAP_REGISTRO_BR_MIN_INTERVAL_SECONDS"
DEFAULT_REGISTRO_BR_MIN_INTERVAL_SECONDS = 0.025
AU_MIN_INTERVAL_ENV = "RDAP_AU_MIN_INTERVAL_SECONDS"
DEFAULT_AU_MIN_INTERVAL_SECONDS = 2.0


def _parse_positive_float_env(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    text = raw.strip()
    if not text:
        return default
    try:
        value = float(text)
    except ValueError:
        return default
    if value <= 0:
        return default
    return value


def build_default_known_policies(
    *,
    verisign_min_interval_seconds: Optional[float] = None,
    publicinterestregistry_min_interval_seconds: Optional[float] = None,
    identitydigital_min_interval_seconds: Optional[float] = None,
    identitydigital_whois_min_interval_seconds: Optional[float] = None,
    registry_co_min_interval_seconds: Optional[float] = None,
    centralnic_min_interval_seconds: Optional[float] = None,
    gmoregistry_min_interval_seconds: Optional[float] = None,
    radix_min_interval_seconds: Optional[float] = None,
    denic_min_interval_seconds: Optional[float] = None,
    nominet_min_interval_seconds: Optional[float] = None,
    sidn_min_interval_seconds: Optional[float] = None,
    registro_br_min_interval_seconds: Optional[float] = None,
    au_min_interval_seconds: Optional[float] = None,
) -> Tuple[HostRatePolicy, ...]:
    verisign_floor = (
        _parse_positive_float_env(VERISIGN_MIN_INTERVAL_ENV, DEFAULT_VERISIGN_MIN_INTERVAL_SECONDS)
        if verisign_min_interval_seconds is None
        else max(0.000001, float(verisign_min_interval_seconds))
    )
    publicinterestregistry_floor = (
        _parse_positive_float_env(
            PUBLICINTERESTREGISTRY_MIN_INTERVAL_ENV,
            DEFAULT_PUBLICINTERESTREGISTRY_MIN_INTERVAL_SECONDS,
        )
        if publicinterestregistry_min_interval_seconds is None
        else max(0.000001, float(publicinterestregistry_min_interval_seconds))
    )
    identitydigital_floor = (
        _parse_positive_float_env(
            IDENTITYDIGITAL_MIN_INTERVAL_ENV,
            DEFAULT_IDENTITYDIGITAL_MIN_INTERVAL_SECONDS,
        )
        if identitydigital_min_interval_seconds is None
        else max(0.000001, float(identitydigital_min_interval_seconds))
    )
    identitydigital_whois_floor = (
        _parse_positive_float_env(
            IDENTITYDIGITAL_WHOIS_MIN_INTERVAL_ENV,
            DEFAULT_IDENTITYDIGITAL_WHOIS_MIN_INTERVAL_SECONDS,
        )
        if identitydigital_whois_min_interval_seconds is None
        else max(0.000001, float(identitydigital_whois_min_interval_seconds))
    )
    registry_co_floor = (
        _parse_positive_float_env(
            REGISTRY_CO_MIN_INTERVAL_ENV,
            DEFAULT_REGISTRY_CO_MIN_INTERVAL_SECONDS,
        )
        if registry_co_min_interval_seconds is None
        else max(0.000001, float(registry_co_min_interval_seconds))
    )
    centralnic_floor = (
        _parse_positive_float_env(
            CENTRALNIC_MIN_INTERVAL_ENV,
            DEFAULT_CENTRALNIC_MIN_INTERVAL_SECONDS,
        )
        if centralnic_min_interval_seconds is None
        else max(0.000001, float(centralnic_min_interval_seconds))
    )
    gmoregistry_floor = (
        _parse_positive_float_env(
            GMOREGISTRY_MIN_INTERVAL_ENV,
            DEFAULT_GMOREGISTRY_MIN_INTERVAL_SECONDS,
        )
        if gmoregistry_min_interval_seconds is None
        else max(0.000001, float(gmoregistry_min_interval_seconds))
    )
    radix_floor = (
        _parse_positive_float_env(
            RADIX_MIN_INTERVAL_ENV,
            DEFAULT_RADIX_MIN_INTERVAL_SECONDS,
        )
        if radix_min_interval_seconds is None
        else max(0.000001, float(radix_min_interval_seconds))
    )
    denic_floor = (
        _parse_positive_float_env(
            DENIC_MIN_INTERVAL_ENV,
            DEFAULT_DENIC_MIN_INTERVAL_SECONDS,
        )
        if denic_min_interval_seconds is None
        else max(0.000001, float(denic_min_interval_seconds))
    )
    nominet_floor = (
        _parse_positive_float_env(
            NOMINET_MIN_INTERVAL_ENV,
            DEFAULT_NOMINET_MIN_INTERVAL_SECONDS,
        )
        if nominet_min_interval_seconds is None
        else max(0.000001, float(nominet_min_interval_seconds))
    )
    sidn_floor = (
        _parse_positive_float_env(
            SIDN_MIN_INTERVAL_ENV,
            DEFAULT_SIDN_MIN_INTERVAL_SECONDS,
        )
        if sidn_min_interval_seconds is None
        else max(0.000001, float(sidn_min_interval_seconds))
    )
    registro_br_floor = (
        _parse_positive_float_env(
            REGISTRO_BR_MIN_INTERVAL_ENV,
            DEFAULT_REGISTRO_BR_MIN_INTERVAL_SECONDS,
        )
        if registro_br_min_interval_seconds is None
        else max(0.000001, float(registro_br_min_interval_seconds))
    )
    au_floor = (
        _parse_positive_float_env(
            AU_MIN_INTERVAL_ENV,
            DEFAULT_AU_MIN_INTERVAL_SECONDS,
        )
        if au_min_interval_seconds is None
        else max(0.000001, float(au_min_interval_seconds))
    )

    return (
        HostRatePolicy("rdap.verisign.com", verisign_floor, f"Verisign calibrated floor: {verisign_floor:.6f}s"),
        HostRatePolicy(
            "rdap.publicinterestregistry.org",
            publicinterestregistry_floor,
            f"Public Interest Registry calibrated floor: {publicinterestregistry_floor:.6f}s",
        ),
        HostRatePolicy(
            "rdap.identitydigital.services",
            identitydigital_floor,
            f"Identity Digital calibrated floor: {identitydigital_floor:.6f}s",
        ),
        HostRatePolicy(
            "whois.nic.ai",
            identitydigital_whois_floor,
            f"Identity Digital WHOIS fallback floor: {identitydigital_whois_floor:.6f}s",
        ),
        HostRatePolicy(
            "rdap.registry.co",
            registry_co_floor,
            f"Registry.co calibrated floor: {registry_co_floor:.6f}s",
        ),
        HostRatePolicy(
            "rdap.centralnic.com",
            centralnic_floor,
            f"CentralNic calibrated floor: {centralnic_floor:.6f}s",
        ),
        HostRatePolicy(
            "rdap.gmoregistry.net",
            gmoregistry_floor,
            f"GMO Registry conservative floor: {gmoregistry_floor:.6f}s",
        ),
        HostRatePolicy("rdap.radix.host", radix_floor, f"Radix calibrated floor: {radix_floor:.6f}s"),
        HostRatePolicy("rdap.denic.de", denic_floor, f"DENIC calibrated floor: {denic_floor:.6f}s"),
        HostRatePolicy("rdap.nominet.uk", nominet_floor, f"Nominet calibrated floor: {nominet_floor:.6f}s"),
        HostRatePolicy("rdap.sidn.nl", sidn_floor, f"SIDN calibrated floor: {sidn_floor:.6f}s"),
        HostRatePolicy(
            "rdap.registro.br",
            registro_br_floor,
            f"Registro.br calibrated floor: {registro_br_floor:.6f}s",
        ),
        HostRatePolicy("rdap.cctld.au", au_floor, f"auDA conservative floor: {au_floor:.6f}s"),
        HostRatePolicy("tucowsdomains.com", 60.0, "Tucows published limit: 1 query per 60s"),
        HostRatePolicy("registry.godaddy", 36.0, "GoDaddy policy: 100 queries per hour"),
        HostRatePolicy("godaddyregistry.com", 36.0, "GoDaddy policy: 100 queries per hour"),
    )


DEFAULT_KNOWN_POLICIES: Tuple[HostRatePolicy, ...] = build_default_known_policies()


class HostRateLimiter:
    """Per-host adaptive limiter with policy overrides and status tracking."""

    def __init__(
        self,
        base_interval_seconds: float = 1.0,
        min_interval_seconds: float = 0.167,
        max_interval_seconds: float = 120.0,
        success_window: int = 25,
        decrease_factor: float = 0.9,
        known_policies: Optional[Tuple[HostRatePolicy, ...]] = None,
    ):
        self.base_interval_seconds = max(0.0, base_interval_seconds)
        self.min_interval_seconds = max(0.0, min_interval_seconds)
        self.known_policies = known_policies or DEFAULT_KNOWN_POLICIES
        max_policy_floor = max((policy.min_interval_seconds for policy in self.known_policies), default=0.0)
        self.max_interval_seconds = max(self.base_interval_seconds, max_interval_seconds, max_policy_floor)
        self.success_window = max(1, success_window)
        self.decrease_factor = min(max(decrease_factor, 0.1), 1.0)
        self._manual_policy_overrides: Dict[str, float] = {}

        self._locks: Dict[str, asyncio.Lock] = {}
        self._hosts: Dict[str, HostRuntime] = {}

    def _get_lock(self, host: str) -> asyncio.Lock:
        lock = self._locks.get(host)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[host] = lock
        return lock

    def _match_policy(self, host: str) -> Optional[HostRatePolicy]:
        lowered = host.lower()
        for host_contains, min_interval in sorted(
            self._manual_policy_overrides.items(),
            key=lambda item: len(item[0]),
            reverse=True,
        ):
            if host_contains in lowered:
                return HostRatePolicy(
                    host_contains=host_contains,
                    min_interval_seconds=min_interval,
                    label=f"Manual override: {host_contains} floor {min_interval:.6f}s",
                )
        for policy in self.known_policies:
            if policy.host_contains in lowered:
                return policy
        return None

    def _default_adaptive_floor(self) -> float:
        return min(self.base_interval_seconds, self.min_interval_seconds)

    def _manual_override_for_host(self, host: str) -> Optional[Tuple[str, float]]:
        lowered = host.lower()
        for host_contains, min_interval in sorted(
            self._manual_policy_overrides.items(),
            key=lambda item: len(item[0]),
            reverse=True,
        ):
            if host_contains in lowered:
                return host_contains, min_interval
        return None

    def _state_for_host(self, host: str) -> HostRuntime:
        existing = self._hosts.get(host)
        if existing is not None:
            return existing

        policy = self._match_policy(host)
        if policy is not None:
            floor = policy.min_interval_seconds
            start = floor
            label = policy.label
        else:
            floor = self._default_adaptive_floor()
            start = self.base_interval_seconds
            label = "Adaptive default"

        runtime = HostRuntime(
            host=host,
            policy_label=label,
            floor_interval_seconds=floor,
            current_interval_seconds=start,
            last_updated_epoch=time.time(),
        )
        self._hosts[host] = runtime
        return runtime

    async def set_manual_overrides(self, overrides: Dict[str, float], replace: bool = False, reset_backoff: bool = True) -> None:
        normalized: Dict[str, float] = {}
        for raw_host, raw_value in dict(overrides or {}).items():
            host_contains = str(raw_host or "").strip().lower()
            if not host_contains:
                continue
            value = float(raw_value)
            if value <= 0:
                raise ValueError(f"Manual min interval must be > 0 for host key '{host_contains}'.")
            normalized[host_contains] = value

        if replace:
            self._manual_policy_overrides = normalized
        else:
            self._manual_policy_overrides.update(normalized)
        if self._manual_policy_overrides:
            self.max_interval_seconds = max(self.max_interval_seconds, max(self._manual_policy_overrides.values()))

        now = time.monotonic()
        for host in list(self._hosts.keys()):
            lock = self._get_lock(host)
            async with lock:
                state = self._state_for_host(host)
                policy = self._match_policy(host)
                if policy is not None:
                    state.floor_interval_seconds = policy.min_interval_seconds
                    state.policy_label = policy.label
                else:
                    state.floor_interval_seconds = self._default_adaptive_floor()
                    state.policy_label = "Adaptive default"

                if reset_backoff:
                    state.current_interval_seconds = state.floor_interval_seconds
                    state.next_allowed_monotonic = now + state.current_interval_seconds
                    state.success_streak = 0
                else:
                    state.current_interval_seconds = max(state.current_interval_seconds, state.floor_interval_seconds)
                state.last_updated_epoch = time.time()

    async def clear_manual_overrides(self, reset_backoff: bool = True) -> None:
        self._manual_policy_overrides.clear()

        now = time.monotonic()
        for host in list(self._hosts.keys()):
            lock = self._get_lock(host)
            async with lock:
                state = self._state_for_host(host)
                policy = self._match_policy(host)
                if policy is not None:
                    state.floor_interval_seconds = policy.min_interval_seconds
                    state.policy_label = policy.label
                else:
                    state.floor_interval_seconds = self._default_adaptive_floor()
                    state.policy_label = "Adaptive default"

                if reset_backoff:
                    state.current_interval_seconds = state.floor_interval_seconds
                    state.next_allowed_monotonic = now + state.current_interval_seconds
                    state.success_streak = 0
                else:
                    state.current_interval_seconds = max(state.current_interval_seconds, state.floor_interval_seconds)
                state.last_updated_epoch = time.time()

    def get_manual_override_snapshot(self) -> Dict[str, float]:
        return {key: float(value) for key, value in sorted(self._manual_policy_overrides.items())}

    def get_policy_defaults_snapshot(self) -> Dict[str, float]:
        defaults: Dict[str, float] = {}
        for policy in self.known_policies:
            defaults[policy.host_contains] = float(policy.min_interval_seconds)
        return dict(sorted(defaults.items()))

    async def acquire(self, host: str) -> None:
        lock = self._get_lock(host)
        async with lock:
            state = self._state_for_host(host)

            now = time.monotonic()
            wait_for = max(0.0, state.next_allowed_monotonic - now)
            if wait_for > 0:
                await asyncio.sleep(wait_for)
                now = time.monotonic()

            state.next_allowed_monotonic = max(state.next_allowed_monotonic, now) + state.current_interval_seconds
            state.total_requests += 1
            state.last_updated_epoch = time.time()

    async def record_success(self, host: str, status_code: int) -> None:
        lock = self._get_lock(host)
        async with lock:
            state = self._state_for_host(host)
            state.last_status = status_code
            state.last_error = None
            state.last_retry_after_seconds = None
            state.success_streak += 1

            if state.success_streak >= self.success_window:
                lowered = state.current_interval_seconds * self.decrease_factor
                state.current_interval_seconds = max(state.floor_interval_seconds, lowered)
                state.success_streak = 0

            state.last_updated_epoch = time.time()

    async def record_throttle(
        self,
        host: str,
        penalty_seconds: float,
        retry_after_seconds: Optional[float],
        status_code: int = 429,
    ) -> None:
        lock = self._get_lock(host)
        async with lock:
            state = self._state_for_host(host)
            state.total_429 += 1
            state.last_status = status_code
            state.last_error = None
            state.last_retry_after_seconds = retry_after_seconds
            state.success_streak = 0

            capped_retry_after: Optional[float] = None
            if retry_after_seconds is not None:
                capped_retry_after = max(0.0, min(float(retry_after_seconds), self.max_interval_seconds))

            doubled = state.current_interval_seconds * 2.0
            if capped_retry_after is not None:
                state.current_interval_seconds = min(
                    self.max_interval_seconds,
                    max(state.floor_interval_seconds, doubled, capped_retry_after),
                )
            else:
                state.current_interval_seconds = min(
                    self.max_interval_seconds,
                    max(state.floor_interval_seconds, doubled),
                )

            capped_penalty = max(0.0, min(float(penalty_seconds or 0.0), self.max_interval_seconds))
            if capped_penalty > 0:
                now = time.monotonic()
                state.next_allowed_monotonic = max(state.next_allowed_monotonic, now + capped_penalty)

            state.last_updated_epoch = time.time()

    async def record_server_error(self, host: str, penalty_seconds: float, status_code: int) -> None:
        lock = self._get_lock(host)
        async with lock:
            state = self._state_for_host(host)
            state.total_5xx += 1
            state.last_status = status_code
            state.last_error = None
            state.last_retry_after_seconds = None
            state.success_streak = 0

            raised = state.current_interval_seconds * 1.4
            state.current_interval_seconds = min(
                self.max_interval_seconds,
                max(state.floor_interval_seconds, raised),
            )

            capped_penalty = max(0.0, min(float(penalty_seconds or 0.0), self.max_interval_seconds))
            if capped_penalty > 0:
                now = time.monotonic()
                state.next_allowed_monotonic = max(state.next_allowed_monotonic, now + capped_penalty)

            state.last_updated_epoch = time.time()

    async def record_forbidden(self, host: str, penalty_seconds: float, status_code: int = 403) -> None:
        lock = self._get_lock(host)
        async with lock:
            state = self._state_for_host(host)
            state.total_errors += 1
            state.last_status = status_code
            state.last_error = "HTTP 403 forbidden"
            state.last_retry_after_seconds = None
            state.success_streak = 0

            raised = state.current_interval_seconds * 2.0
            state.current_interval_seconds = min(
                self.max_interval_seconds,
                max(state.floor_interval_seconds, raised, penalty_seconds),
            )

            capped_penalty = max(0.0, min(float(penalty_seconds or 0.0), self.max_interval_seconds))
            if capped_penalty > 0:
                now = time.monotonic()
                state.next_allowed_monotonic = max(state.next_allowed_monotonic, now + capped_penalty)

            state.last_updated_epoch = time.time()

    async def record_network_error(self, host: str, penalty_seconds: float, error_text: str) -> None:
        lock = self._get_lock(host)
        async with lock:
            state = self._state_for_host(host)
            state.total_errors += 1
            state.last_status = None
            state.last_error = error_text
            state.last_retry_after_seconds = None
            state.success_streak = 0

            raised = state.current_interval_seconds * 1.6
            state.current_interval_seconds = min(
                self.max_interval_seconds,
                max(state.floor_interval_seconds, raised),
            )

            capped_penalty = max(0.0, min(float(penalty_seconds or 0.0), self.max_interval_seconds))
            if capped_penalty > 0:
                now = time.monotonic()
                state.next_allowed_monotonic = max(state.next_allowed_monotonic, now + capped_penalty)

            state.last_updated_epoch = time.time()

    def get_status_snapshot(self) -> List[Dict[str, object]]:
        rows: List[Dict[str, object]] = []
        for host in sorted(self._hosts.keys()):
            state = self._hosts[host]
            rows.append(
                {
                    "host": state.host,
                    "policy": state.policy_label,
                    "manual_override": self._manual_override_for_host(host) is not None,
                    "interval_seconds": round(state.current_interval_seconds, 6),
                    "floor_interval_seconds": round(state.floor_interval_seconds, 6),
                    "total_requests": state.total_requests,
                    "total_429": state.total_429,
                    "total_5xx": state.total_5xx,
                    "total_errors": state.total_errors,
                    "last_status": state.last_status,
                    "last_error": state.last_error,
                    "last_retry_after_seconds": state.last_retry_after_seconds,
                    "last_updated_at": datetime.fromtimestamp(state.last_updated_epoch, timezone.utc).isoformat(),
                }
            )
        return rows


def normalize_domain(candidate: str) -> str:
    name = candidate.strip().lower().rstrip(".")
    if not name:
        raise DomainValidationError("Empty domain")
    if any(ch in name for ch in [" ", "/", "*", "@"]):
        raise DomainValidationError("Domain contains invalid characters")

    labels = name.split(".")
    if len(labels) < 2:
        raise DomainValidationError("Domain must include a TLD")
    if any(not label for label in labels):
        raise DomainValidationError("Domain contains empty labels")

    ascii_labels = []
    for label in labels:
        try:
            ascii_label = label.encode("idna").decode("ascii")
        except UnicodeError as exc:
            raise DomainValidationError("Invalid IDN label") from exc

        if not _ASCII_LABEL_RE.match(ascii_label):
            raise DomainValidationError("Label has unsupported characters")
        if ascii_label.startswith("-") or ascii_label.endswith("-"):
            raise DomainValidationError("Labels cannot start or end with a hyphen")
        ascii_labels.append(ascii_label)

    ascii_domain = ".".join(ascii_labels)
    if len(ascii_domain) > 253:
        raise DomainValidationError("Domain exceeds maximum length")

    return ascii_domain


def parse_retry_after(value: Optional[str], now: Optional[datetime] = None) -> Optional[float]:
    if value is None:
        return None

    stripped = value.strip()
    if not stripped:
        return None

    if stripped.isdigit():
        return max(0.0, float(stripped))

    try:
        parsed = email.utils.parsedate_to_datetime(stripped)
    except (TypeError, ValueError, IndexError, OverflowError):
        return None
    if parsed is None:
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)

    current = now or datetime.now(timezone.utc)
    delta = (parsed - current).total_seconds()
    return max(0.0, delta)


def parse_identitydigital_whois_state(payload: str) -> Optional[str]:
    text = str(payload or "").lower()
    if not text:
        return None
    if "domain not found." in text:
        return "available"
    if "currently available for application via the identity digital dropzone service" in text:
        return "available"
    if "domain name:" in text:
        return "taken"
    if "this name is reserved by the registry" in text:
        return "taken"
    return None


class RDAPClient:
    def __init__(
        self,
        http_client: httpx.AsyncClient,
        resolver: RDAPBootstrapResolver,
        ipv4_http_client: Optional[httpx.AsyncClient] = None,
        limiter: Optional[HostRateLimiter] = None,
        result_cache: Optional[DomainResultCache] = None,
        max_retries: int = 4,
        base_backoff_seconds: float = 1.0,
        max_backoff_seconds: float = 30.0,
        jitter_seconds: float = 0.5,
        available_ttl_seconds: int = 15 * 60,
        taken_ttl_seconds: int = 6 * 60 * 60,
        unknown_ttl_seconds: int = 5 * 60,
        enable_identitydigital_whois_fallback: bool = True,
        identitydigital_whois_host: str = _IDENTITYDIGITAL_WHOIS_HOST,
        identitydigital_whois_port: int = _IDENTITYDIGITAL_WHOIS_PORT,
        identitydigital_whois_timeout_seconds: float = _IDENTITYDIGITAL_WHOIS_TIMEOUT_SECONDS,
    ):
        self.http_client = http_client
        self.ipv4_http_client = ipv4_http_client
        self.resolver = resolver
        self.limiter = limiter or HostRateLimiter()
        self.result_cache = result_cache
        self.max_retries = max_retries
        self.base_backoff_seconds = base_backoff_seconds
        self.max_backoff_seconds = max_backoff_seconds
        self.jitter_seconds = jitter_seconds
        self.available_ttl_seconds = max(60, int(available_ttl_seconds))
        self.taken_ttl_seconds = max(60, int(taken_ttl_seconds))
        self.unknown_ttl_seconds = max(30, int(unknown_ttl_seconds))
        self.enable_identitydigital_whois_fallback = bool(enable_identitydigital_whois_fallback)
        self.identitydigital_whois_host = str(identitydigital_whois_host or _IDENTITYDIGITAL_WHOIS_HOST).strip().lower()
        self.identitydigital_whois_port = int(identitydigital_whois_port)
        self.identitydigital_whois_timeout_seconds = max(1.0, float(identitydigital_whois_timeout_seconds))
        # Some RDAP hosts intermittently throttle HEAD while serving GET normally.
        # Prefer direct GET checks for stable high-volume scans.
        self._prefer_head_exists_probe = False
        self._head_supported_by_host: Dict[str, bool] = {}

    def _supports_ipv4_fallback(self, host: str) -> bool:
        lowered = str(host or "").lower()
        return any(piece in lowered for piece in _IPV4_FALLBACK_HOST_CONTAINS)

    async def _perform_request(
        self,
        client: httpx.AsyncClient,
        *,
        method: str,
        endpoint: str,
        headers: Dict[str, str],
    ) -> httpx.Response:
        if method == "HEAD":
            return await client.head(
                endpoint,
                headers=headers,
                follow_redirects=True,
                timeout=20.0,
            )
        return await client.get(
            endpoint,
            headers=headers,
            follow_redirects=True,
            timeout=20.0,
        )

    async def _request_with_optional_ipv4_fallback(
        self,
        *,
        host: str,
        method: str,
        endpoint: str,
        headers: Dict[str, str],
    ) -> httpx.Response:
        response = await self._perform_request(
            self.http_client,
            method=method,
            endpoint=endpoint,
            headers=headers,
        )
        if response.status_code != 403:
            return response
        if self.ipv4_http_client is None:
            return response
        if not self._supports_ipv4_fallback(host):
            return response

        try:
            ipv4_response = await self._perform_request(
                self.ipv4_http_client,
                method=method,
                endpoint=endpoint,
                headers=headers,
            )
        except (httpx.TimeoutException, httpx.TransportError):
            return response

        if ipv4_response.status_code != 403:
            return ipv4_response
        return response

    def _compute_backoff(self, attempt: int) -> float:
        raw = min(self.max_backoff_seconds, self.base_backoff_seconds * (2 ** max(0, attempt - 1)))
        jitter = random.uniform(0.0, self.jitter_seconds)
        return raw + jitter

    def get_rate_status(self) -> List[Dict[str, object]]:
        return self.limiter.get_status_snapshot()

    def get_rate_config(self) -> Dict[str, Dict[str, float]]:
        return {
            "defaults": self.limiter.get_policy_defaults_snapshot(),
            "overrides": self.limiter.get_manual_override_snapshot(),
        }

    async def set_rate_overrides(self, overrides: Dict[str, float], replace: bool = False, reset_backoff: bool = True) -> None:
        await self.limiter.set_manual_overrides(overrides, replace=replace, reset_backoff=reset_backoff)

    async def clear_rate_overrides(self, reset_backoff: bool = True) -> None:
        await self.limiter.clear_manual_overrides(reset_backoff=reset_backoff)

    async def _request_domain_endpoint(self, host: str, endpoint: str) -> httpx.Response:
        headers = {"Accept": "application/rdap+json, application/json"}
        head_supported = self._head_supported_by_host.get(host, True)
        if self._prefer_head_exists_probe and head_supported:
            await self.limiter.acquire(host)
            head_response = await self._request_with_optional_ipv4_fallback(
                host=host,
                method="HEAD",
                endpoint=endpoint,
                headers=headers,
            )
            if head_response.status_code not in {405, 501}:
                return head_response
            self._head_supported_by_host[host] = False

        await self.limiter.acquire(host)
        return await self._request_with_optional_ipv4_fallback(
            host=host,
            method="GET",
            endpoint=endpoint,
            headers=headers,
        )

    def _can_use_identitydigital_whois_fallback(self, domain: str, rdap_host: str) -> bool:
        if not self.enable_identitydigital_whois_fallback:
            return False
        lowered_domain = str(domain or "").strip().lower().rstrip(".")
        if not lowered_domain.endswith(".ai"):
            return False
        return self._supports_ipv4_fallback(rdap_host)

    async def _query_identitydigital_whois(self, domain: str) -> str:
        reader = None
        writer = None
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self.identitydigital_whois_host, self.identitydigital_whois_port),
                timeout=self.identitydigital_whois_timeout_seconds,
            )
            writer.write(f"{domain}\r\n".encode("utf-8", errors="ignore"))
            await asyncio.wait_for(writer.drain(), timeout=self.identitydigital_whois_timeout_seconds)

            chunks: List[bytes] = []
            while True:
                chunk = await asyncio.wait_for(
                    reader.read(4096),
                    timeout=self.identitydigital_whois_timeout_seconds,
                )
                if not chunk:
                    break
                chunks.append(chunk)
            return b"".join(chunks).decode("utf-8", errors="ignore")
        finally:
            if writer is not None:
                writer.close()
                with contextlib.suppress(Exception):
                    await writer.wait_closed()

    async def _check_domain_via_identitydigital_whois(self, domain: str) -> Optional[DomainResult]:
        whois_host = self.identitydigital_whois_host
        await self.limiter.acquire(whois_host)
        try:
            payload = await self._query_identitydigital_whois(domain)
        except (asyncio.TimeoutError, TimeoutError):
            wait_seconds = max(_IDENTITYDIGITAL_WHOIS_NETWORK_PENALTY_SECONDS, self._compute_backoff(1))
            await self.limiter.record_network_error(
                whois_host,
                wait_seconds,
                "Identity Digital WHOIS timeout",
            )
            return None
        except ConnectionRefusedError:
            wait_seconds = max(_IDENTITYDIGITAL_WHOIS_REFUSED_PENALTY_SECONDS, self._compute_backoff(1))
            await self.limiter.record_forbidden(whois_host, wait_seconds, status_code=403)
            return None
        except OSError as exc:
            lowered = str(exc).lower()
            if "refused" in lowered:
                wait_seconds = max(_IDENTITYDIGITAL_WHOIS_REFUSED_PENALTY_SECONDS, self._compute_backoff(1))
                await self.limiter.record_forbidden(whois_host, wait_seconds, status_code=403)
            else:
                wait_seconds = max(_IDENTITYDIGITAL_WHOIS_NETWORK_PENALTY_SECONDS, self._compute_backoff(1))
                await self.limiter.record_network_error(
                    whois_host,
                    wait_seconds,
                    f"Identity Digital WHOIS error: {exc}",
                )
            return None

        state = parse_identitydigital_whois_state(payload)
        if state is None:
            wait_seconds = max(_IDENTITYDIGITAL_WHOIS_NETWORK_PENALTY_SECONDS, self._compute_backoff(1))
            await self.limiter.record_network_error(
                whois_host,
                wait_seconds,
                "Identity Digital WHOIS returned unclassified payload",
            )
            return None

        await self.limiter.record_success(whois_host, 200)
        return await self._cache_result(
            DomainResult(
                domain=domain,
                state=state,
                rdap_host=whois_host,
                http_status=200,
                source=f"whois:{whois_host}",
            )
        )

    def _ttl_for_state(self, state: str) -> int:
        if state == "available":
            return self.available_ttl_seconds
        if state == "taken":
            return self.taken_ttl_seconds
        return self.unknown_ttl_seconds

    async def _cache_result(self, result: DomainResult, *, cacheable: bool = True) -> DomainResult:
        checked_at = datetime.now(timezone.utc)
        ttl_seconds = self._ttl_for_state(result.state)
        expires_at = checked_at + timedelta(seconds=ttl_seconds)

        if result.source is None:
            if result.rdap_host:
                result.source = f"rdap:{result.rdap_host}"
            else:
                result.source = "rdap"
        result.checked_at = checked_at.isoformat()
        result.ttl_seconds = ttl_seconds
        result.expires_at = expires_at.isoformat()
        result.from_cache = False

        if cacheable and self.result_cache:
            await self.result_cache.put(
                domain=result.domain,
                state=result.state,
                rdap_host=result.rdap_host,
                http_status=result.http_status,
                error=result.error,
                source=result.source,
                checked_at=result.checked_at,
                ttl_seconds=ttl_seconds,
                expires_at=result.expires_at,
            )

        return result

    async def check_domain(self, domain: str, force_recheck: bool = False) -> DomainResult:
        if self.result_cache and not force_recheck:
            cached = await self.result_cache.get(domain)
            if cached is not None:
                return DomainResult(
                    domain=cached.domain,
                    state=cached.state,
                    rdap_host=cached.rdap_host,
                    http_status=cached.http_status,
                    error=cached.error,
                    source=f"cache:{cached.source}",
                    checked_at=cached.checked_at,
                    ttl_seconds=cached.ttl_seconds,
                    expires_at=cached.expires_at,
                    from_cache=True,
                )

        await self.resolver.ensure_loaded(self.http_client)
        base_url = self.resolver.resolve_base_url(domain)
        if not base_url:
            return await self._cache_result(
                DomainResult(
                    domain=domain,
                    state="unknown",
                    rdap_host=None,
                    http_status=None,
                    error="No RDAP service found for domain suffix",
                    source="resolver:no-service",
                )
            )

        endpoint = f"{base_url.rstrip('/')}/domain/{quote(domain, safe='.-')}"
        host = httpx.URL(base_url).host or base_url

        for attempt in range(1, self.max_retries + 1):
            try:
                response = await self._request_domain_endpoint(host, endpoint)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                wait_seconds = self._compute_backoff(attempt)
                await self.limiter.record_network_error(host, wait_seconds, str(exc))
                if self._can_use_identitydigital_whois_fallback(domain, host):
                    fallback_result = await self._check_domain_via_identitydigital_whois(domain)
                    if fallback_result is not None:
                        return fallback_result
                if attempt == self.max_retries:
                    return await self._cache_result(
                        DomainResult(
                            domain=domain,
                            state="unknown",
                            rdap_host=host,
                            error=f"Network error: {exc}",
                            source=f"rdap:{host}",
                        ),
                        cacheable=False,
                    )
                continue

            status = response.status_code
            if status == 200:
                await self.limiter.record_success(host, status)
                return await self._cache_result(
                    DomainResult(
                        domain=domain,
                        state="taken",
                        rdap_host=host,
                        http_status=status,
                        source=f"rdap:{host}",
                    )
                )
            if status == 404:
                await self.limiter.record_success(host, status)
                return await self._cache_result(
                    DomainResult(
                        domain=domain,
                        state="available",
                        rdap_host=host,
                        http_status=status,
                        source=f"rdap:{host}",
                    )
                )

            if status == 403 or status == 429 or status >= 500:
                if status == 403:
                    wait_seconds = max(15.0, self._compute_backoff(attempt))
                    await self.limiter.record_forbidden(host, wait_seconds, status_code=status)
                elif status == 429:
                    retry_after = parse_retry_after(response.headers.get("Retry-After"))
                    if retry_after is None:
                        wait_seconds = self._compute_backoff(attempt)
                    else:
                        wait_seconds = retry_after
                    await self.limiter.record_throttle(
                        host,
                        penalty_seconds=wait_seconds,
                        retry_after_seconds=retry_after,
                        status_code=status,
                    )
                else:
                    wait_seconds = self._compute_backoff(attempt)
                    await self.limiter.record_server_error(host, wait_seconds, status)

                if self._can_use_identitydigital_whois_fallback(domain, host):
                    fallback_result = await self._check_domain_via_identitydigital_whois(domain)
                    if fallback_result is not None:
                        return fallback_result

                if attempt == self.max_retries:
                    return await self._cache_result(
                        DomainResult(
                            domain=domain,
                            state="unknown",
                            rdap_host=host,
                            http_status=status,
                            error=f"RDAP returned {status} after retries",
                            source=f"rdap:{host}",
                        ),
                        cacheable=False,
                    )
                continue

            await self.limiter.record_success(host, status)
            return await self._cache_result(
                DomainResult(
                    domain=domain,
                    state="unknown",
                    rdap_host=host,
                    http_status=status,
                    error=f"Unexpected HTTP status {status}",
                    source=f"rdap:{host}",
                ),
                cacheable=False,
            )

        return await self._cache_result(
            DomainResult(
                domain=domain,
                state="unknown",
                rdap_host=host,
                error="Retries exhausted",
                source=f"rdap:{host}",
            ),
            cacheable=False,
        )
