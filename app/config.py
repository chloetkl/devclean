from pydantic_settings import BaseSettings


class ApplicationSettings(BaseSettings):
    devin_api_token: str = ""
    devin_organization_id: str = ""
    devin_api_base_url: str = "https://api.devin.ai"

    github_webhook_secret: str = ""

    database_url: str = "sqlite+aiosqlite:///./data/autoquality.db"

    session_poll_interval_seconds: int = 30
    session_poll_timeout_seconds: int = 1800

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


application_settings = ApplicationSettings()
