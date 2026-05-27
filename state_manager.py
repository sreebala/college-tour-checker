"""
Persistent state stored in state.json (committed back to the repo after each CI run).

Schema
------
{
  "last_check":        "<ISO-8601 UTC>",
  "last_daily_report": "<ISO-8601 UTC>",
  "alerted_slots": {
    "<slot-key>": "<ISO-8601 UTC when first alerted>"
  },
  "last_uci_status":  { connected, morning_slots, all_slots, error, checked_at },
  "last_ucla_status": { connected, morning_slots, all_slots, error, checked_at }
}
"""

import json
import os
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

STATE_FILE = os.environ.get("STATE_FILE", "state.json")

_DEFAULTS: dict = {
    "last_check": None,
    "last_daily_report": None,
    "alerted_slots": {},
    "last_uci_status": None,
    "last_ucla_status": None,
}


def load() -> dict:
    if not os.path.exists(STATE_FILE):
        return dict(_DEFAULTS)
    try:
        with open(STATE_FILE) as f:
            data = json.load(f)
        # Merge in any missing keys from defaults
        for k, v in _DEFAULTS.items():
            data.setdefault(k, v)
        return data
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Could not read state file (%s) — using defaults", exc)
        return dict(_DEFAULTS)


def save(state: dict) -> None:
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f, indent=2)
    except OSError as exc:
        logger.error("Could not write state file: %s", exc)


def slot_key(university: str, time_str: str, target_date: str) -> str:
    raw = f"{university}_{target_date}_{time_str}"
    return raw.replace(" ", "_").replace(",", "").replace(":", "")


def is_new_slot(state: dict, key: str) -> bool:
    return key not in state.get("alerted_slots", {})


def mark_alerted(state: dict, key: str) -> None:
    state.setdefault("alerted_slots", {})[key] = datetime.now(timezone.utc).isoformat()


def daily_report_due(state: dict) -> bool:
    last = state.get("last_daily_report")
    if last is None:
        return True
    try:
        dt = datetime.fromisoformat(last)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) - dt >= timedelta(hours=24)
    except (ValueError, TypeError):
        return True


def record_check(state: dict, uci_result, ucla_result) -> None:
    """Persist connection and slot snapshot from the latest scrape run."""
    now = datetime.now(timezone.utc).isoformat()
    state["last_check"] = now

    for key, result in (("last_uci_status", uci_result), ("last_ucla_status", ucla_result)):
        state[key] = {
            "connected":     result.connected,
            "morning_slots": [s.time_str for s in result.morning_slots],
            "all_slots":     [s.time_str for s in result.slots],
            "error":         result.error,
            "checked_at":    now,
        }
