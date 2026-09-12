import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
FEEDBACK_FILE = DATA_DIR / "feedback.jsonl"
LOCK = threading.Lock()


def save_feedback(record: dict) -> str:
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    feedback_id = uuid.uuid4().hex
    stored = {
        "feedback_id": feedback_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        **record
    }

    with LOCK:
        with FEEDBACK_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(stored, ensure_ascii=False) + "\n")

    return feedback_id
