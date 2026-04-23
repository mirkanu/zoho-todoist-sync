# app/core/hash.py
import hashlib
import json
from .normalise import NormalisedTask


def canonical_hash(task: NormalisedTask) -> str:
    """
    Deterministic SHA-256 hex digest of the 4 canonical sync fields.
    JSON-serialises with sorted keys for stability.
    """
    payload = {
        "title": task.title,
        "due_date": task.due_date,   # "YYYY-MM-DD" or None (serialises as null)
        "priority": task.priority,   # int 1–4
        "is_completed": task.is_completed,
    }
    serialised = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(serialised.encode("utf-8")).hexdigest()
