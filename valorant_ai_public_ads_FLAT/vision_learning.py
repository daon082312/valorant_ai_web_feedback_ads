from __future__ import annotations

import base64
import io
import math
import os
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

import numpy as np
from PIL import Image
from dotenv import load_dotenv
from supabase import create_client

from valorant_reference import (
    abilities_for_agent,
    canonical_ability,
    canonical_agent,
    merge_agent_predictions,
    recognize_agent_from_hud,
)

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip()
SUPABASE_SECRET_KEY = (
    os.getenv("SUPABASE_SECRET_KEY", "").strip()
    or os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
)

SAMPLES_TABLE = "vision_training_samples"
CENTROIDS_TABLE = "vision_model_centroids"
MIN_SAMPLES_PER_LABEL = int(os.getenv("VISION_MIN_SAMPLES_PER_LABEL", "2"))
MAX_IMAGE_BYTES = int(os.getenv("VISION_MAX_IMAGE_BYTES", str(2 * 1024 * 1024)))

_client = None


def _db_client():
    global _client
    if not (SUPABASE_URL and SUPABASE_SECRET_KEY):
        return None
    if _client is None:
        _client = create_client(SUPABASE_URL, SUPABASE_SECRET_KEY)
    return _client


def vision_learning_is_configured() -> bool:
    return bool(SUPABASE_URL and SUPABASE_SECRET_KEY)


def decode_data_url(image_data: str) -> bytes:
    text = str(image_data or "").strip()
    if not text:
        raise ValueError("이미지 데이터가 없습니다.")
    if text.startswith("data:"):
        try:
            _, text = text.split(",", 1)
        except ValueError as exc:
            raise ValueError("잘못된 이미지 데이터입니다.") from exc
    raw = base64.b64decode(text, validate=False)
    if not raw or len(raw) > MAX_IMAGE_BYTES:
        raise ValueError("학습 이미지 크기가 너무 큽니다.")
    return raw


def _crop_for_target(image: Image.Image, target_type: str) -> Image.Image:
    w, h = image.size
    if target_type == "agent":
        # Agent learning focuses on the bottom HUD, where all four ability icons
        # form an agent-specific visual signature.
        return image.crop((int(w * 0.16), int(h * 0.72), int(w * 0.84), h))

    # Skill correction still keeps the cast/effect area together with the HUD.
    return image.crop((int(w * 0.10), int(h * 0.12), int(w * 0.90), h))


def extract_feature(image_bytes: bytes, target_type: str) -> list[float]:
    if target_type not in {"agent", "skill"}:
        raise ValueError("target_type은 agent 또는 skill이어야 합니다.")

    with Image.open(io.BytesIO(image_bytes)) as source:
        image = source.convert("RGB")
        image = _crop_for_target(image, target_type)
        image = image.resize((20, 12), Image.Resampling.BILINEAR)
        arr = np.asarray(image, dtype=np.float32) / 255.0

    gray = arr.mean(axis=2)
    gx = np.diff(gray, axis=1, prepend=gray[:, :1])
    gy = np.diff(gray, axis=0, prepend=gray[:1, :])
    feature = np.concatenate([
        arr.reshape(-1),
        gx.reshape(-1),
        gy.reshape(-1),
    ]).astype(np.float32)

    norm = float(np.linalg.norm(feature))
    if norm > 1e-8:
        feature /= norm
    return [round(float(x), 7) for x in feature]


def _normalize_label(label: str) -> str:
    value = " ".join(str(label or "").strip().split())
    if not 1 <= len(value) <= 60:
        raise ValueError("라벨은 1~60자로 입력해 주세요.")
    return value


def _normalize_vector(values: list[float]) -> list[float]:
    arr = np.asarray(values, dtype=np.float32)
    norm = float(np.linalg.norm(arr))
    if norm > 1e-8:
        arr /= norm
    return [round(float(x), 7) for x in arr]


def retrain(target_type: str) -> dict[str, Any]:
    client = _db_client()
    if client is None:
        raise RuntimeError("VISION_LEARNING_NOT_CONFIGURED")

    rows = list(
        client.table(SAMPLES_TABLE)
        .select("label,feature")
        .eq("target_type", target_type)
        .execute()
        .data
        or []
    )

    grouped: dict[str, list[list[float]]] = defaultdict(list)
    for row in rows:
        label = str(row.get("label") or "").strip()
        feature = row.get("feature")
        if label and isinstance(feature, list) and feature:
            grouped[label].append([float(x) for x in feature])

    client.table(CENTROIDS_TABLE).delete().eq("target_type", target_type).execute()

    upserts = []
    now = datetime.now(timezone.utc).isoformat()
    for label, vectors in grouped.items():
        matrix = np.asarray(vectors, dtype=np.float32)
        centroid = _normalize_vector(matrix.mean(axis=0).tolist())
        upserts.append({
            "target_type": target_type,
            "label": label,
            "sample_count": len(vectors),
            "centroid": centroid,
            "updated_at": now,
        })

    if upserts:
        client.table(CENTROIDS_TABLE).upsert(
            upserts,
            on_conflict="target_type,label",
        ).execute()

    return {
        "target_type": target_type,
        "sample_count": len(rows),
        "label_count": len(grouped),
        "labels": {label: len(vectors) for label, vectors in grouped.items()},
    }


def train_sample(
    *,
    user_id: str,
    analysis_id: str,
    target_type: str,
    event_index: int,
    label: str,
    image_bytes: bytes,
    predicted_label: str = "",
    agent_label: str = "",
) -> dict[str, Any]:
    client = _db_client()
    if client is None:
        raise RuntimeError("VISION_LEARNING_NOT_CONFIGURED")

    if target_type not in {"agent", "skill"}:
        raise ValueError("잘못된 학습 대상입니다.")

    clean_label = _normalize_label(label)
    if target_type == "agent":
        canonical = canonical_agent(clean_label)
        if canonical:
            clean_label = canonical
    elif agent_label:
        canonical = canonical_ability(agent_label, clean_label)
        if canonical:
            clean_label = canonical
        elif abilities_for_agent(agent_label):
            raise ValueError(f"{agent_label}의 실제 스킬 이름을 선택해 주세요.")

    feature = extract_feature(image_bytes, target_type)
    now = datetime.now(timezone.utc).isoformat()

    row = {
        "user_id": user_id,
        "analysis_id": str(analysis_id or "")[:100],
        "target_type": target_type,
        "event_index": int(event_index),
        "label": clean_label,
        "predicted_label": str(predicted_label or "")[:60],
        "feature": feature,
        "updated_at": now,
    }

    client.table(SAMPLES_TABLE).upsert(
        row,
        on_conflict="user_id,analysis_id,target_type,event_index",
    ).execute()

    status = retrain(target_type)
    return {"ok": True, "label": clean_label, "model": status}


def predict_feature(
    target_type: str,
    feature: list[float],
    allowed_labels: list[str] | None = None,
) -> dict[str, Any]:
    client = _db_client()
    if client is None:
        return {"ready": False, "reason": "not_configured"}

    rows = list(
        client.table(CENTROIDS_TABLE)
        .select("label,sample_count,centroid")
        .eq("target_type", target_type)
        .execute()
        .data
        or []
    )

    allowed = {str(item).casefold() for item in (allowed_labels or [])}
    candidates = []
    vector = np.asarray(feature, dtype=np.float32)
    vector_norm = float(np.linalg.norm(vector))
    if vector_norm > 1e-8:
        vector /= vector_norm

    for row in rows:
        label = str(row.get("label") or "")
        if allowed and label.casefold() not in allowed:
            continue
        count = int(row.get("sample_count") or 0)
        centroid = row.get("centroid")
        if count < MIN_SAMPLES_PER_LABEL or not isinstance(centroid, list):
            continue
        c = np.asarray(centroid, dtype=np.float32)
        if c.shape != vector.shape:
            continue
        c_norm = float(np.linalg.norm(c))
        if c_norm > 1e-8:
            c /= c_norm
        similarity = float(np.dot(vector, c))
        candidates.append({
            "label": label,
            "sample_count": count,
            "similarity": similarity,
        })

    candidates.sort(key=lambda item: item["similarity"], reverse=True)
    if len(candidates) < 2:
        return {
            "ready": False,
            "reason": "needs_training",
            "candidates": [
                {
                    "label": item["label"],
                    "similarity": round(item["similarity"], 4),
                    "sample_count": item["sample_count"],
                }
                for item in candidates[:5]
            ],
        }

    best = candidates[0]
    second = candidates[1]["similarity"]
    margin = max(0.0, best["similarity"] - second)
    support = min(1.0, math.log2(best["sample_count"] + 1) / 4.0)
    confidence = max(0.0, min(1.0, margin * 2.5 + support * 0.25))

    return {
        "ready": True,
        "label": best["label"],
        "confidence": round(confidence, 3),
        "similarity": round(best["similarity"], 4),
        "sample_count": best["sample_count"],
        "source": "user_training",
        "candidates": [
            {
                "label": item["label"],
                "similarity": round(item["similarity"], 4),
                "sample_count": item["sample_count"],
            }
            for item in candidates[:5]
        ],
    }


def predict_image(
    target_type: str,
    image_bytes: bytes,
    agent_label: str = "",
) -> dict[str, Any]:
    feature = extract_feature(image_bytes, target_type)

    if target_type == "agent":
        learned = predict_feature(target_type, feature)
        try:
            reference = recognize_agent_from_hud(image_bytes)
        except Exception as exc:
            print(f"[ValorantReference] HUD recognition skipped: {type(exc).__name__}: {exc}")
            reference = {"ready": False, "reason": "reference_error"}
        return merge_agent_predictions(learned, reference)

    allowed = abilities_for_agent(agent_label) if agent_label else []
    result = predict_feature(target_type, feature, allowed_labels=allowed or None)
    if allowed:
        result["allowed_labels"] = allowed
        result["agent"] = canonical_agent(agent_label) or agent_label
    return result


def model_status() -> dict[str, Any]:
    client = _db_client()
    if client is None:
        return {"configured": False, "agent": {}, "skill": {}}

    rows = list(
        client.table(CENTROIDS_TABLE)
        .select("target_type,label,sample_count,updated_at")
        .execute()
        .data
        or []
    )
    result: dict[str, Any] = {"configured": True, "agent": {}, "skill": {}}
    for row in rows:
        target = str(row.get("target_type") or "")
        if target not in {"agent", "skill"}:
            continue
        result[target][str(row.get("label") or "")] = {
            "sample_count": int(row.get("sample_count") or 0),
            "updated_at": row.get("updated_at"),
        }
    return result
