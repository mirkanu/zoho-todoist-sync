# app/core/normalise.py
import unicodedata
from dataclasses import dataclass
from datetime import datetime

@dataclass(frozen=True)
class NormalisedTask:
    title: str          # Unicode NFC, stripped, CRLF→LF
    due_date: str | None  # "YYYY-MM-DD" or None
    priority: int       # Todoist int 1–4
    is_completed: bool


def normalise_due_date(raw: str | None) -> str | None:
    """
    Accepts: None, "YYYY-MM-DD", "YYYY-MM-DDTHH:MM:SS+HH:MM"
    Returns: "YYYY-MM-DD" string or None.
    Uses datetime.fromisoformat() — handles tz-offset correctly on Python 3.11+.
    """
    if not raw:
        return None
    try:
        return str(datetime.fromisoformat(raw).date())
    except ValueError:
        # Unknown format — treat as no due date rather than silently truncating.
        return None


def normalise_title(raw: str | None) -> str:
    """NFC normalise, CRLF→LF, strip leading/trailing whitespace."""
    if not raw:
        return ""
    text = (raw or "").replace("\r\n", "\n").replace("\r", "\n")
    text = unicodedata.normalize("NFC", text)
    return text.strip()


