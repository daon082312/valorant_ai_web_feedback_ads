from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
USAGE_FILE = DATA_DIR / "usage.json"

# The user is in Korea; daily allowance resets at Korean midnight.
LOCAL_TZ = ZoneInfo("Asia/Seoul")

_lock = threading.Lock()


def _today() -> str:
    return datetime.now(LOCAL_TZ).date().isoformat()


def _load() -> dict:
    if not USAGE_FILE.exists():
        return {}

    try:
        return json.loads(
            USAGE_FILE.read_text(
                encoding="utf-8"
            )
        )
    except Exception:
        return {}


def _save(data: dict) -> None:
    DATA_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    temp_file = USAGE_FILE.with_suffix(".tmp")
    temp_file.write_text(
        json.dumps(
            data,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    temp_file.replace(USAGE_FILE)


def get_used(ip: str) -> int:
    today = _today()

    with _lock:
        data = _load()
        record = data.get(ip, {})

        if record.get("date") != today:
            return 0

        return int(
            record.get("successful_analyses", 0)
        )


def get_remaining(
    ip: str,
    daily_limit: int,
) -> int:
    return max(
        daily_limit - get_used(ip),
        0,
    )


def record_success(ip: str) -> int:
    """
    Increment only after a completed successful analysis.

    Returns today's new successful-analysis count.
    """
    today = _today()

    with _lock:
        data = _load()
        record = data.get(ip, {})

        if record.get("date") != today:
            count = 0
        else:
            count = int(
                record.get(
                    "successful_analyses",
                    0,
                )
            )

        count += 1

        data[ip] = {
            "date": today,
            "successful_analyses": count,
        }

        _save(data)
        return count
