"""Tests for in-memory rate limiter."""

import time

from app.rate_limiter import RateLimiter


class TestRateLimiter:
    def setup_method(self):
        self.rl = RateLimiter()

    def test_household_limit_allows_under_limit(self):
        assert self.rl.check_household_limit("hh-1", token_count=1) is True

    def test_household_limit_blocks_over_limit(self):
        # Fill up the bucket
        for _ in range(100):
            self.rl._household_buckets.setdefault("hh-1", __import__("app.rate_limiter", fromlist=["RateBucket"]).RateBucket())
            self.rl._household_buckets["hh-1"].add()
        assert self.rl.check_household_limit("hh-1", token_count=1) is False

    def test_token_limit_allows_under_limit(self):
        assert self.rl.check_token_limit("token-1") is True

    def test_token_limit_blocks_over_limit(self):
        from app.rate_limiter import RateBucket
        self.rl._token_buckets["token-1"] = RateBucket()
        for _ in range(20):
            self.rl._token_buckets["token-1"].add()
        assert self.rl.check_token_limit("token-1") is False

    def test_burst_limit_allows_under_limit(self):
        assert self.rl.check_burst_limit("hh-1", token_count=1) is True

    def test_burst_limit_blocks_over_limit(self):
        from app.rate_limiter import RateBucket
        self.rl._burst_buckets["hh-1"] = RateBucket()
        for _ in range(10):
            self.rl._burst_buckets["hh-1"].add()
        assert self.rl.check_burst_limit("hh-1", token_count=1) is False

    def test_record_request_tracks_tokens(self):
        self.rl.record_request("hh-1", ["t1", "t2"])
        assert self.rl._household_buckets["hh-1"].count_in_window(3600) == 2
        assert self.rl._token_buckets["t1"].count_in_window(3600) == 1
        assert self.rl._token_buckets["t2"].count_in_window(3600) == 1

    def test_record_request_resets_consecutive_429s(self):
        self.rl.record_rate_limit_hit("hh-1")
        self.rl.record_rate_limit_hit("hh-1")
        assert self.rl._household_state["hh-1"].consecutive_429s == 2
        self.rl.record_request("hh-1", ["t1"])
        assert self.rl._household_state["hh-1"].consecutive_429s == 0

    def test_record_rate_limit_hit_increments(self):
        assert self.rl.record_rate_limit_hit("hh-1") == 1
        assert self.rl.record_rate_limit_hit("hh-1") == 2
        assert self.rl.record_rate_limit_hit("hh-1") == 3

    def test_suspension_after_threshold(self):
        for _ in range(10):
            self.rl.record_rate_limit_hit("hh-1")
        assert self.rl.check_suspended("hh-1") is True

    def test_not_suspended_before_threshold(self):
        for _ in range(9):
            self.rl.record_rate_limit_hit("hh-1")
        assert self.rl.check_suspended("hh-1") is False

    def test_suspension_expires(self):
        for _ in range(10):
            self.rl.record_rate_limit_hit("hh-1")
        assert self.rl.check_suspended("hh-1") is True

        # Simulate expiry
        self.rl._household_state["hh-1"].suspended_until = time.time() - 1
        assert self.rl.check_suspended("hh-1") is False
        assert self.rl._household_state["hh-1"].consecutive_429s == 0

    def test_get_tokens_over_limit(self):
        from app.rate_limiter import RateBucket
        self.rl._token_buckets["t-over"] = RateBucket()
        for _ in range(20):
            self.rl._token_buckets["t-over"].add()

        over = self.rl.get_tokens_over_limit(["t-over", "t-ok"])
        assert "t-over" in over
        assert "t-ok" not in over
