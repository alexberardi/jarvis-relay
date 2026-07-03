"""The limiter's in-memory bucket maps must stay bounded.

Security (P1.7 DoS): the maps were never evicted, so a flood of distinct keys
(spoofed IPs on /v1/register, distinct push tokens on /v1/send) grew without
limit and could OOM the relay that every self-hosted install depends on. The cap
(RATE_LIMIT_MAX_TRACKED_KEYS, 200 in test settings) must hold under a flood.
"""
from app.rate_limiter import RateLimiter


class TestLimiterBounds:
    def test_register_ip_buckets_bounded_under_flood(self):
        rl = RateLimiter()
        for i in range(500):
            rl.check_register_limit(f"ip-{i}")
            rl.record_register(f"ip-{i}")
        assert len(rl._ip_register_buckets) <= 200

    def test_token_buckets_bounded_under_flood(self):
        rl = RateLimiter()
        for i in range(500):
            rl.record_request("hh-1", [f"tok-{i}"])
        assert len(rl._token_buckets) <= 200

    def test_no_eviction_under_cap(self):
        """Below the cap, every bucket persists — eviction is a last-resort valve,
        not something that silently forgets counters during normal operation."""
        rl = RateLimiter()
        for i in range(150):  # under the 200-key test cap
            rl.record_register(f"ip-{i}")
        assert len(rl._ip_register_buckets) == 150
        # A counter recorded earlier is still intact.
        assert rl._ip_register_buckets["ip-0"].count_in_window(3600) == 1
