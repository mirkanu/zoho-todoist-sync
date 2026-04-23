---
phase: 01-foundation
plan: 01
subsystem: scaffold
tags:
  - scaffold
  - config
  - python
  - pydantic-settings
dependency_graph:
  requires: []
  provides:
    - app.core.config.Settings
    - app.core.config.settings (module-level singleton)
    - pytest infrastructure
    - pinned runtime dependencies
  affects: []
tech_stack:
  added:
    - pydantic-settings==2.14.0
    - pytest==9.0.2
    - pytest-asyncio
  patterns:
    - Fail-fast config: module-level Settings() instantiation raises ValidationError on missing vars
    - TDD RED/GREEN: tests written before implementation
key_files:
  created:
    - pyproject.toml
    - requirements.txt
    - requirements-dev.txt
    - .python-version
    - .gitignore (extended)
    - .env.example
    - app/__init__.py
    - app/core/__init__.py
    - app/core/config.py
    - tests/__init__.py
    - tests/unit/__init__.py
    - tests/conftest.py
    - tests/unit/test_config.py
  modified:
    - .gitignore
decisions:
  - "pydantic-settings==2.14.0 chosen as config layer; module-level settings=Settings() is the fail-fast trigger (INFRA-5)"
  - "todoist_project_id has default '6gCPcWwM392GhXQh' per CLAUDE.md but is still declared as required (no default) to force explicit configuration"
metrics:
  duration: 7 minutes
  completed_date: "2026-04-23T17:01:00Z"
  tasks_completed: 2
  files_created: 13
---

# Phase 1 Plan 1: Project Scaffold and Fail-Fast Config Summary

**One-liner:** Python project scaffold with pydantic-settings fail-fast config that raises ValidationError at import when any required env var is missing.

## What Was Built

Greenfield Python project scaffold for the zoho-todoist-sync service. Every subsequent plan in Phase 1 can now import from `app.core.config` and run under pytest.

### Files Created

| File | Purpose |
|------|---------|
| `pyproject.toml` | Project metadata + pytest configuration (testpaths, asyncio_mode=auto) |
| `requirements.txt` | Pinned runtime dependencies (13 packages) |
| `requirements-dev.txt` | Pinned dev dependencies (pytest==9.0.2, pytest-asyncio) |
| `.python-version` | Railway runtime pin: 3.12 |
| `.gitignore` | Extended with Python-specific patterns (.venv, __pycache__, .pytest_cache, etc.) |
| `.env.example` | Documented env-var contract with placeholder values only |
| `app/__init__.py` | App package root |
| `app/core/__init__.py` | Core subpackage |
| `app/core/config.py` | Settings class with module-level fail-fast instantiation |
| `tests/__init__.py` | Tests package root |
| `tests/unit/__init__.py` | Unit tests subpackage |
| `tests/conftest.py` | Shared fixtures including `complete_env` for Settings tests |
| `tests/unit/test_config.py` | 9 tests covering INFRA-5, INFRA-6, INFRA-7, INFRA-4, EDGE-4 |

### Settings Class

`app/core/config.py` implements `class Settings(BaseSettings)` with:
- All required fields: zoho_client_id, zoho_client_secret, zoho_refresh_token, zoho_user_id, todoist_api_token, todoist_project_id, todoist_client_secret, resend_api_key, database_url, redis_url
- Defaulted fields: zoho_region="eu", zoho_todoist_task_id_field="", zoho_terminal_statuses="Completed", zoho_job_defer_secs=2, log_level="INFO"
- Property `zoho_terminal_statuses_list` that splits and strips the comma-separated string
- Module-level `settings = Settings()` — triggers ValidationError before any other module loads

## Task Commits

| Task | Commit | Description |
|------|--------|-------------|
| Task 1 (scaffold) | 7962903 | chore(01-01): create project scaffold |
| Task 2 RED (tests) | f38fbdb | test(01-01): add failing tests for Settings fail-fast config |
| Task 2 GREEN (impl) | ed4a7d5 | feat(01-01): implement Settings fail-fast config |

## TDD Gate Compliance

- RED gate: commit `f38fbdb` — 9 tests written, all failing (ModuleNotFoundError on missing config.py)
- GREEN gate: commit `ed4a7d5` — all 9 tests pass after implementing Settings class
- REFACTOR gate: not needed (implementation was clean as written)

## Deviations from Plan

None — plan executed exactly as written.

The existing `.gitignore` already contained `.env` and Python-related entries; it was extended with additional Python patterns (`.venv/`, `*.egg-info/`, etc.) rather than replaced, which preserved the existing GSD workflow entries.

## Known Stubs

None. The Settings class is fully wired to env vars via pydantic-settings. No hardcoded empty values flow to consumers.

## Threat Flags

None. All threat mitigations from the plan's threat model were applied:
- T-1-01: `.env` is in `.gitignore`; `.env.example` contains only placeholder values
- T-1-02: `settings` object is never passed to any logger in `config.py`
- T-1-03: `settings = Settings()` at module top-level — fail-fast on missing required var
- T-1-04: Accepted (crash = correct behavior)
- T-1-05: `.env.example` values verified as placeholders only

## Self-Check: PASSED

All created files confirmed present. All commits confirmed in git log.
