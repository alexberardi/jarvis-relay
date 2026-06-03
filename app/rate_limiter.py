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
        if household_id not in self._household_buckets:
            self._household_buckets[household_id] = RateBucket()
        bucket = self._household_buckets[household_id]
        count = bucket.count_in_window(3600)  # 1 hour window
        return count + token_count <= settings.rate_limit_per_household_per_hour

    def check_token_limit(self, push_token: str) -> bool:
        """Check per-token rate limit. Returns True if allowed."""
        settings = get_settings()
        if push_token not in self._token_buckets:
            self._token_buckets[push_token] = RateBucket()
        bucket = self._token_buckets[push_token]
        count = bucket.count_in_window(3600)
        return count + 1 <= settings.rate_limit_per_token_per_hour

    def check_burst_limit(self, household_id: str, token_count: int = 1) -> bool:
        """Check burst rate limit. Returns True if allowed."""
        settings = get_settings()
        if household_id not in self._burst_buckets:
            self._burst_buckets[household_id] = RateBucket()
        bucket = self._burst_buckets[household_id]
        count = bucket.count_in_window(1)  # 1 second window
        return count + token_count <= settings.rate_limit_burst_per_second

    def record_request(self, household_id: str, tokens: list[str]) -> None:
        """Record a successful request for rate tracking."""
        if household_id not in self._household_buckets:
            self._household_buckets[household_id] = RateBucket()
        for _ in tokens:
            self._household_buckets[household_id].add()

        for token in tokens:
            if token not in self._token_buckets:
                self._token_buckets[token] = RateBucket()
            self._token_buckets[token].add()

        if household_id not in self._burst_buckets:
            self._burst_buckets[household_id] = RateBucket()
        self._burst_buckets[household_id].add()

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
        if ip not in self._ip_register_buckets:
            self._ip_register_buckets[ip] = RateBucket()
        bucket = self._ip_register_buckets[ip]
        count = bucket.count_in_window(3600)
        return count + 1 <= settings.rate_limit_register_per_ip_per_hour

    def record_register(self, ip: str) -> None:
        """Record a successful /v1/register call for the given IP."""
        if ip not in self._ip_register_buckets:
            self._ip_register_buckets[ip] = RateBucket()
        self._ip_register_buckets[ip].add()


# Module-level singleton
rate_limiter = RateLimiter()
