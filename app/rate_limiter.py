"""In-memory rate limiting with abuse detection.

Tracks per-household and per-token request counts, plus consecutive 429 hits
for abuse escalation.
"""

import logging
import time
from dataclasses import dataclass, field

from app.config import get_settings

logger = logging.getLogger(__name__)


@dataclass
class RateBucket:
    """Sliding window counter for rate limiting."""
    timestamps: list[float] = field(default_factory=list)

    def count_in_window(self, window_seconds: float) -> int:
        now = time.time()
        cutoff = now - window_seconds
        self.timestamps = [t for t in self.timestamps if t > cutoff]
        return len(self.timestamps)

    def add(self) -> None:
        self.timestamps.append(time.time())


@dataclass
class HouseholdRateState:
    """Abuse detection state per household."""
    consecutive_429s: int = 0
    last_429_at: float | None = None
    suspended_until: float | None = None


class RateLimiter:
    """In-memory rate limiter with per-household and per-token tracking."""

    def __init__(self) -> None:
        self._household_buckets: dict[str, RateBucket] = {}
        self._token_buckets: dict[str, RateBucket] = {}
        self._burst_buckets: dict[str, RateBucket] = {}
        self._household_state: dict[str, HouseholdRateState] = {}
        self._ip_register_buckets: dict[str, RateBucket] = {}

    def _get_state(self, household_id: str) -> HouseholdRateState:
        if household_id not in self._household_state:
            self._household_state[household_id] = HouseholdRateState()
        return self._household_state[household_id]

    def _ensure_capacity(self, buckets: dict[str, RateBucket], window_seconds: float) -> None:
        """Keep a bucket map bounded so a flood of distinct keys can't OOM us.

        Only does work once the map reaches the configured cap: first it drops
        idle buckets (nothing within the window — safe to forget, the key just
        starts fresh next time), then, if still full (all buckets active — a real
        flood), it evicts the least-recently-active tenth. Evicting an active
        limiter bucket can only *reset* someone's counter early, never grant more
        than a fresh caller already gets, so it fails safe.
        """
        cap = get_settings().rate_limit_max_tracked_keys
        if len(buckets) < cap:
            return
        idle = [k for k, b in buckets.items() if b.count_in_window(window_seconds) == 0]
        for k in idle:
            del buckets[k]
        if len(buckets) >= cap:
            ordered = sorted(
                buckets.items(),
                key=lambda kv: kv[1].timestamps[-1] if kv[1].timestamps else 0.0,
            )
            for k, _ in ordered[: max(1, cap // 10)]:
                buckets.pop(k, None)

    def _get_or_create(
        self, buckets: dict[str, RateBucket], key: str, window_seconds: float
    ) -> RateBucket:
        if key not in buckets:
            self._ensure_capacity(buckets, window_seconds)
            buckets[key] = RateBucket()
        return buckets[key]

    def check_suspended(self, household_id: str) -> bool:
        """Check if household is currently suspended. Returns True if suspended."""
        state = self._get_state(household_id)
        if state.suspended_until is None:
            return False
        if time.time() >= state.suspended_until:
            # Suspension expired, reset
            state.suspended_until = None
            state.consecutive_429s = 0
            logger.info("Household %s suspension expired, state reset", household_id)
            return False
        return True

    def check_household_limit(self, household_id: str, token_count: int = 1) -> bool:
        """Check per-household rate limit. Returns True if allowed."""
        settings = get_settings()
        bucket = self._get_or_create(self._household_buckets, household_id, 3600)
        count = bucket.count_in_window(3600)  # 1 hour window
        return count + token_count <= settings.rate_limit_per_household_per_hour

    def check_token_limit(self, push_token: str) -> bool:
        """Check per-token rate limit. Returns True if allowed."""
        settings = get_settings()
        bucket = self._get_or_create(self._token_buckets, push_token, 3600)
        count = bucket.count_in_window(3600)
        return count + 1 <= settings.rate_limit_per_token_per_hour

    def check_burst_limit(self, household_id: str, token_count: int = 1) -> bool:
        """Check burst rate limit. Returns True if allowed."""
        settings = get_settings()
        bucket = self._get_or_create(self._burst_buckets, household_id, 1)
        count = bucket.count_in_window(1)  # 1 second window
        return count + token_count <= settings.rate_limit_burst_per_second

    def record_request(self, household_id: str, tokens: list[str]) -> None:
        """Record a successful request for rate tracking."""
        household_bucket = self._get_or_create(self._household_buckets, household_id, 3600)
        for _ in tokens:
            household_bucket.add()

        for token in tokens:
            self._get_or_create(self._token_buckets, token, 3600).add()

        self._get_or_create(self._burst_buckets, household_id, 1).add()

        # Reset consecutive 429s on success
        state = self._get_state(household_id)
        state.consecutive_429s = 0

    def record_rate_limit_hit(self, household_id: str) -> int:
        """Record a 429 hit for abuse detection. Returns consecutive count."""
        settings = get_settings()
        state = self._get_state(household_id)
        state.consecutive_429s += 1
        state.last_429_at = time.time()

        if state.consecutive_429s >= settings.consecutive_429_suspend_threshold:
            cooldown_seconds = settings.suspension_cooldown_hours * 3600
            state.suspended_until = time.time() + cooldown_seconds
            logger.critical(
                "Household %s suspended for %dh after %d consecutive 429s",
                household_id, settings.suspension_cooldown_hours, state.consecutive_429s,
            )

        return state.consecutive_429s

    def get_tokens_over_limit(self, tokens: list[str]) -> list[str]:
        """Return tokens that are over their per-token rate limit."""
        return [t for t in tokens if not self.check_token_limit(t)]

    def check_register_limit(self, ip: str) -> bool:
        """Check per-IP rate limit for /v1/register. Returns True if allowed."""
        settings = get_settings()
        bucket = self._get_or_create(self._ip_register_buckets, ip, 3600)
        count = bucket.count_in_window(3600)
        return count + 1 <= settings.rate_limit_register_per_ip_per_hour

    def record_register(self, ip: str) -> None:
        """Record a successful /v1/register call for the given IP."""
        self._get_or_create(self._ip_register_buckets, ip, 3600).add()


# Module-level singleton
rate_limiter = RateLimiter()
