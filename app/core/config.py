from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    zoho_client_id: str
    zoho_client_secret: str
    zoho_refresh_token: str
    zoho_user_id: str
    zoho_region: str = "eu"
    zoho_org_id: str = ""
    zoho_todoist_task_id_field: str = ""
    zoho_terminal_statuses: str = "Completed"
    zoho_job_defer_secs: int = 2
    todoist_api_token: str
    todoist_project_id: str
    todoist_client_secret: str
    task_provider: str = "todoist"
    nirvana_pat: str
    resend_api_key: str
    resend_sender_email: str = "sync-alerts@resend.dev"
    database_url: str
    redis_url: str
    log_level: str = "INFO"

    @property
    def zoho_terminal_statuses_list(self) -> list[str]:
        return [s.strip() for s in self.zoho_terminal_statuses.split(",") if s.strip()]

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


# Module-level alias for backwards compatibility.
# Callers should prefer get_settings() so tests can patch it via
# get_settings.cache_clear() + monkeypatch.setenv().
settings = get_settings()
