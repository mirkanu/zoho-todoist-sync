# app/zoho/state.py
# Shared in-process state for the Zoho client lifecycle.
# The FastAPI lifespan populates these dicts on startup and the proactive
# refresh loop mutates token_state in place. No locks: single-writer
# (refresh task), many-readers (sync jobs, reconciler). Dict assignment is
# atomic in CPython.
from datetime import datetime
from typing import TypedDict


class TokenState(TypedDict, total=False):
    access_token: str
    expires_at: datetime


class ZohoFieldCache(TypedDict, total=False):
    todoist_task_id_api_name: str | None
    status_picklist_values: list[str]


# Module-level mutable singletons. Imported and mutated by token_manager
# and main lifespan. Tests reset these by calling .clear().
token_state: TokenState = {}
zoho_field_cache: ZohoFieldCache = {}
