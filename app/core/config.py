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
    # Operational kill-switch for orphan_sweep. Set ZTS_DISABLE_ORPHAN_SWEEP=1
    # in the env to short-circuit orphan_sweep() — added 2026-10-20 after a
    # broken NirvanaClient silently flagged 76 Nirvana rows as missing and
    # orphan_sweep's two-cycle confirmation turned lies into real Zoho
    # deletions. Keep ON until NirvanaClient is fixed AND a dry-run confirms
    # at least one Nirvana row survives a full cycle (memory
    # `2026-10-20-orphan-sweep-incident`).
    zts_disable_orphan_sweep: bool = False
    # Threshold: if orphan_sweep first-cycles more than this many rows in a
    # single sweep, log a warning at the end of the sweep. Catches anomalies
    # like the 2026-10-20 incident (76 first-cycle rows in one sweep because
    # the NirvanaClient parser was always returning []) before they cascade
    # into a real wave of deletions. Set to 0 to disable.
    zts_orphan_first_cycle_alert_threshold: int = 5
    # How many consecutive missing cycles are required before orphan_sweep
    # triggers deletion for a row whose provider doesn't match the active
    # provider. Mirrors the rule that caught a mixed-provider state out on
    # 2026-07-28: using the wrong client for a row always 404s, so we want
    # extra confirmation for rows that the currently-active client wouldn't
    # normally be looking at.
    zts_orphan_mixed_provider_confirm_cycles: int = 3

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
