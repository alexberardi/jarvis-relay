import logging
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    relay_port: int = Field(7735, alias="RELAY_PORT")

    # Expo
    expo_access_token: str = Field("", alias="EXPO_ACCESS_TOKEN")

    # Household JWT
    relay_jwt_secret: str = Field("", alias="RELAY_JWT_SECRET")

    # Rate limiting
    rate_limit_per_household_per_hour: int = Field(100, alias="RATE_LIMIT_PER_HOUSEHOLD_PER_HOUR")
    rate_limit_per_token_per_hour: int = Field(20, alias="RATE_LIMIT_PER_TOKEN_PER_HOUR")
    rate_limit_burst_per_second: int = Field(10, alias="RATE_LIMIT_BURST_PER_SECOND")

    # Abuse alerting
    alert_webhook_url: str | None = Field(None, alias="ALERT_WEBHOOK_URL")
    consecutive_429_alert_threshold: int = Field(3, alias="CONSECUTIVE_429_ALERT_THRESHOLD")
    consecutive_429_suspend_threshold: int = Field(10, alias="CONSECUTIVE_429_SUSPEND_THRESHOLD")
    suspension_cooldown_hours: int = Field(1, alias="SUSPENSION_COOLDOWN_HOURS")

    model_config = SettingsConfigDict(
        env_file=".env", case_sensitive=False, extra="ignore"
    )


logger = logging.getLogger(__name__)


@lru_cache
def get_settings() -> Settings:
    try:
        settings = Settings()
    except PermissionError:
        logger.warning("Unable to read .env; continuing with environment variables only")
        settings = Settings(_env_file=None)
    return settings
