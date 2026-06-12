from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    secret_key: str
    database_url: str = "sqlite:////data/planer.db"
    individual_threshold: int = 3
    planning_horizon_weeks: int = 1
    token_max_age_hours: int = 168
    public_base_url: str = "http://localhost:8000"

    log_level: str = "INFO"  # DEBUG | INFO | WARNING | ERROR
    log_json: bool = False  # True → one JSON object per line (for log aggregation)

    admin_auth_mode: str = "forward_auth"
    admin_forward_auth_header: str = "X-Authentik-Username"
    admin_password_hash: str = ""
    cookie_secure: bool = True  # Secure flag on CSRF cookie; set False for local HTTP dev

    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_starttls: bool = True  # set False for local servers without TLS (e.g. MailHog)
    mail_from: str = ""
    mail_reply_to: str = ""
    # Testing: if set, ALL outgoing mail goes to this address instead of the
    # real student (original recipient shown in the subject). Leave empty in prod.
    mail_override_to: str = ""

    stumgmt_api_url: str = ""
    sparky_auth_url: str = ""
    sparky_user: str = ""
    sparky_password: str = ""
    stumgmt_course_id: str = ""
    stumgmt_presentation_assignment_id: str = ""
    stumgmt_presentation_assignment_name: str = ""
    presentation_pass_points: int = 1

    @field_validator("secret_key")
    @classmethod
    def secret_key_must_be_long(cls, v: str) -> str:
        if len(v) < 32:
            raise ValueError("SECRET_KEY must be at least 32 characters")
        return v


@lru_cache
def get_settings() -> Settings:
    return Settings()
