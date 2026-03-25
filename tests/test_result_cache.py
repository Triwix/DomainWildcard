import asyncio
from datetime import datetime, timedelta, timezone

from app.result_cache import DomainResultCache


def test_domain_result_cache_put_get_and_expire(tmp_path):
    async def run():
        cache = DomainResultCache(tmp_path / "cache.sqlite3")
        now = datetime.now(timezone.utc)
        await cache.put(
            domain="alpha.com",
            state="available",
            rdap_host="rdap.test",
            http_status=404,
            error=None,
            source="rdap:rdap.test",
            checked_at=now.isoformat(),
            ttl_seconds=600,
            expires_at=(now + timedelta(minutes=10)).isoformat(),
        )
        record = await cache.get("alpha.com")
        assert record is not None
        assert record.domain == "alpha.com"
        assert record.state == "available"
        assert record.ttl_seconds == 600

        await cache.put(
            domain="beta.com",
            state="unknown",
            rdap_host="rdap.test",
            http_status=503,
            error="temporary",
            source="rdap:rdap.test",
            checked_at=now.isoformat(),
            ttl_seconds=5,
            expires_at=(now - timedelta(seconds=1)).isoformat(),
        )
        expired = await cache.get("beta.com")
        assert expired is None
        await cache.close()

    asyncio.run(run())
