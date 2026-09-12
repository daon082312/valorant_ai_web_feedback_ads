from __future__ import annotations

import io
import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import httpx
import numpy as np
from PIL import Image

CATALOG_URL = "https://valorant-api.com/v1/agents?isPlayableCharacter=true"
CATALOG_TTL_SECONDS = 6 * 60 * 60
HTTP_TIMEOUT_SECONDS = 8.0

_catalog_lock = threading.RLock()
_catalog_cache: dict[str, dict[str, Any]] = {}
_catalog_at = 0.0
_reference_lock = threading.RLock()
_reference_matrix: np.ndarray | None = None
_reference_rows: list[dict[str, Any]] = []
_reference_catalog_signature = ""

SLOT_ORDER = {
    "Grenade": 0,
    "Ability1": 1,
    "Ability2": 2,
    "Ultimate": 3,
}


def _clean_name(value: str) -> str:
    return " ".join(str(value or "").strip().split())


def _name_key(value: str) -> str:
    return _clean_name(value).casefold().replace("’", "'")


def _fetch_catalog() -> dict[str, dict[str, Any]]:
    with httpx.Client(timeout=HTTP_TIMEOUT_SECONDS, follow_redirects=True) as client:
        response = client.get(CATALOG_URL)
        response.raise_for_status()
        payload = response.json()

    agents: dict[str, dict[str, Any]] = {}
    for raw_agent in payload.get("data") or []:
        if not raw_agent.get("isPlayableCharacter"):
            continue
        name = _clean_name(raw_agent.get("displayName"))
        if not name:
            continue

        abilities = []
        for raw_ability in raw_agent.get("abilities") or []:
            ability_name = _clean_name(raw_ability.get("displayName"))
            slot = _clean_name(raw_ability.get("slot"))
            icon_url = _clean_name(raw_ability.get("displayIcon"))
            if not ability_name or not slot or not icon_url:
                continue
            abilities.append({
                "slot": slot,
                "name": ability_name,
                "icon_url": icon_url,
                "order": SLOT_ORDER.get(slot, 9),
            })

        abilities.sort(key=lambda item: (item["order"], item["name"]))
        agents[_name_key(name)] = {
            "name": name,
            "uuid": str(raw_agent.get("uuid") or ""),
            "icon_url": _clean_name(raw_agent.get("displayIcon")),
            "abilities": abilities,
        }
    return agents


def get_catalog(force: bool = False) -> dict[str, dict[str, Any]]:
    global _catalog_cache, _catalog_at
    now = time.monotonic()
    with _catalog_lock:
        if _catalog_cache and not force and now - _catalog_at < CATALOG_TTL_SECONDS:
            return _catalog_cache

    try:
        fresh = _fetch_catalog()
    except Exception as exc:
        print(f"[ValorantReference] catalog fetch skipped: {type(exc).__name__}: {exc}")
        with _catalog_lock:
            return _catalog_cache

    with _catalog_lock:
        _catalog_cache = fresh
        _catalog_at = now
        return _catalog_cache


def canonical_agent(name: str) -> str | None:
    key = _name_key(name)
    if not key:
        return None
    agent = get_catalog().get(key)
    return str(agent.get("name")) if agent else None


def abilities_for_agent(agent_name: str) -> list[str]:
    agent = get_catalog().get(_name_key(agent_name))
    if not agent:
        return []
    return [str(item["name"]) for item in agent.get("abilities") or []]


def canonical_ability(agent_name: str, ability_name: str) -> str | None:
    wanted = _name_key(ability_name)
    if not wanted:
        return None
    for ability in abilities_for_agent(agent_name):
        if _name_key(ability) == wanted:
            return ability
    return None


def compact_kit_prompt(max_chars: int = 3800) -> str:
    catalog = get_catalog()
    if not catalog:
        return ""

    parts = ["[현재 VALORANT 요원별 공식 스킬 후보표]"]
    for agent in sorted(catalog.values(), key=lambda item: item["name"]):
        abilities = [item["name"] for item in agent.get("abilities") or []]
        if not abilities:
            continue
        parts.append(f"{agent['name']}: " + ", ".join(abilities))
    text = "\n".join(parts)
    return text[:max_chars]


def catalog_payload(agent_name: str = "") -> dict[str, Any]:
    catalog = get_catalog()
    if agent_name:
        agent = catalog.get(_name_key(agent_name))
        if not agent:
            return {"ready": bool(catalog), "agent": None, "abilities": []}
        return {
            "ready": True,
            "agent": agent["name"],
            "abilities": [item["name"] for item in agent.get("abilities") or []],
        }

    return {
        "ready": bool(catalog),
        "agents": [
            {
                "name": agent["name"],
                "abilities": [item["name"] for item in agent.get("abilities") or []],
            }
            for agent in sorted(catalog.values(), key=lambda item: item["name"])
        ],
    }


def _reference_signal(image: Image.Image) -> np.ndarray:
    rgba = image.convert("RGBA").resize((24, 24), Image.Resampling.LANCZOS)
    arr = np.asarray(rgba, dtype=np.float32) / 255.0
    alpha = arr[:, :, 3]
    rgb = arr[:, :, :3].mean(axis=2)

    if float(alpha.std()) > 0.03:
        signal = alpha * (0.70 + 0.30 * rgb)
    else:
        baseline = float(np.percentile(rgb, 45))
        signal = np.clip(rgb - baseline, 0.0, 1.0)

    gx = np.diff(signal, axis=1, prepend=signal[:, :1])
    gy = np.diff(signal, axis=0, prepend=signal[:1, :])
    feature = np.concatenate([
        signal.reshape(-1),
        np.abs(gx).reshape(-1),
        np.abs(gy).reshape(-1),
    ]).astype(np.float32)
    feature -= float(feature.mean())
    norm = float(np.linalg.norm(feature))
    if norm > 1e-8:
        feature /= norm
    return feature


def _hud_window_signal(image: Image.Image) -> np.ndarray:
    gray = np.asarray(
        image.convert("L").resize((24, 24), Image.Resampling.BILINEAR),
        dtype=np.float32,
    ) / 255.0

    # Ability glyphs are mostly bright, high-contrast shapes. Suppress the
    # gameplay background and retain the bright glyph/edge structure.
    baseline = float(np.percentile(gray, 62))
    signal = np.clip(gray - baseline, 0.0, 1.0)
    gx = np.diff(signal, axis=1, prepend=signal[:, :1])
    gy = np.diff(signal, axis=0, prepend=signal[:1, :])
    feature = np.concatenate([
        signal.reshape(-1),
        np.abs(gx).reshape(-1),
        np.abs(gy).reshape(-1),
    ]).astype(np.float32)
    feature -= float(feature.mean())
    norm = float(np.linalg.norm(feature))
    if norm > 1e-8:
        feature /= norm
    return feature


def _download_reference(row: dict[str, Any]) -> tuple[dict[str, Any], np.ndarray] | None:
    try:
        with httpx.Client(timeout=HTTP_TIMEOUT_SECONDS, follow_redirects=True) as client:
            response = client.get(row["icon_url"])
            response.raise_for_status()
        with Image.open(io.BytesIO(response.content)) as image:
            return row, _reference_signal(image)
    except Exception:
        return None


def _reference_signature(catalog: dict[str, dict[str, Any]]) -> str:
    return "|".join(
        f"{agent['name']}:{','.join(a['name'] for a in agent.get('abilities') or [])}"
        for agent in sorted(catalog.values(), key=lambda item: item["name"])
    )


def _ensure_reference_matrix() -> tuple[np.ndarray | None, list[dict[str, Any]]]:
    global _reference_matrix, _reference_rows, _reference_catalog_signature
    catalog = get_catalog()
    if not catalog:
        return None, []

    signature = _reference_signature(catalog)
    with _reference_lock:
        if (
            _reference_matrix is not None
            and _reference_rows
            and _reference_catalog_signature == signature
        ):
            return _reference_matrix, _reference_rows

    rows = []
    for agent in catalog.values():
        for ability in agent.get("abilities") or []:
            rows.append({
                "agent": agent["name"],
                "ability": ability["name"],
                "slot": ability["slot"],
                "order": ability["order"],
                "icon_url": ability["icon_url"],
            })

    features: list[np.ndarray] = []
    kept_rows: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=12) as executor:
        futures = [executor.submit(_download_reference, row) for row in rows]
        for future in as_completed(futures):
            item = future.result()
            if item is None:
                continue
            row, feature = item
            kept_rows.append(row)
            features.append(feature)

    if not features:
        return None, []

    matrix = np.stack(features).astype(np.float32)
    with _reference_lock:
        _reference_matrix = matrix
        _reference_rows = kept_rows
        _reference_catalog_signature = signature
        return _reference_matrix, _reference_rows


def _candidate_hud_windows(source: Image.Image) -> tuple[np.ndarray, list[tuple[float, float]]]:
    image = source.convert("RGB")
    width, height = image.size
    if width <= 0 or height <= 0:
        return np.empty((0, 1728), dtype=np.float32), []

    # Keep compute predictable by normalizing wide captures to <= 720 px.
    if width > 720:
        scale = 720.0 / width
        image = image.resize(
            (720, max(1, int(round(height * scale)))),
            Image.Resampling.BILINEAR,
        )
        width, height = image.size

    x_min = int(width * 0.16)
    x_max = int(width * 0.84)
    y_min = int(height * 0.78)
    y_max = int(height * 0.985)
    sizes = sorted({
        max(18, int(height * 0.055)),
        max(22, int(height * 0.070)),
        max(26, int(height * 0.085)),
    })

    features: list[np.ndarray] = []
    centers: list[tuple[float, float]] = []
    for size in sizes:
        step = max(7, size // 3)
        half = size // 2
        for cy in range(y_min + half, y_max - half + 1, step):
            for cx in range(x_min + half, x_max - half + 1, step):
                crop = image.crop((cx - half, cy - half, cx + half, cy + half))
                features.append(_hud_window_signal(crop))
                centers.append((cx / width, cy / height))

    if not features:
        return np.empty((0, 1728), dtype=np.float32), []
    return np.stack(features).astype(np.float32), centers


def recognize_agent_from_hud(image_bytes: bytes) -> dict[str, Any]:
    reference_matrix, reference_rows = _ensure_reference_matrix()
    if reference_matrix is None or not reference_rows:
        return {"ready": False, "reason": "reference_catalog_unavailable", "candidates": []}

    try:
        with Image.open(io.BytesIO(image_bytes)) as source:
            candidate_matrix, centers = _candidate_hud_windows(source)
    except Exception:
        return {"ready": False, "reason": "invalid_image", "candidates": []}

    if candidate_matrix.size == 0:
        return {"ready": False, "reason": "hud_not_found", "candidates": []}

    scores = candidate_matrix @ reference_matrix.T
    ref_best_scores = scores.max(axis=0)
    ref_best_indices = scores.argmax(axis=0)

    by_agent: dict[str, list[tuple[dict[str, Any], float, float]]] = {}
    for idx, row in enumerate(reference_rows):
        center_x = centers[int(ref_best_indices[idx])][0]
        by_agent.setdefault(row["agent"], []).append(
            (row, float(ref_best_scores[idx]), center_x)
        )

    agent_scores = []
    for agent, matches in by_agent.items():
        matches.sort(key=lambda item: item[0].get("order", 9))
        useful = [item for item in matches if item[0].get("order", 9) <= 3]
        if len(useful) < 3:
            continue

        raw_scores = sorted((item[1] for item in useful), reverse=True)
        core = raw_scores[: min(4, len(raw_scores))]
        score = float(sum(core) / len(core))

        ordered_x = [item[2] for item in useful]
        ordered_pairs = sum(
            1 for left, right in zip(ordered_x, ordered_x[1:]) if left < right
        )
        order_ratio = ordered_pairs / max(1, len(ordered_x) - 1)
        if order_ratio >= 2 / 3:
            score += 0.045
        elif order_ratio <= 1 / 3:
            score -= 0.025

        agent_scores.append({
            "label": agent,
            "score": score,
            "matched_icons": [
                {
                    "ability": row["ability"],
                    "slot": row["slot"],
                    "similarity": round(similarity, 4),
                    "x": round(center_x, 4),
                }
                for row, similarity, center_x in useful
            ],
        })

    agent_scores.sort(key=lambda item: item["score"], reverse=True)
    if not agent_scores:
        return {"ready": False, "reason": "hud_not_found", "candidates": []}

    best = agent_scores[0]
    second_score = agent_scores[1]["score"] if len(agent_scores) > 1 else 0.0
    margin = max(0.0, best["score"] - second_score)
    confidence = max(
        0.0,
        min(1.0, (best["score"] - 0.34) * 1.55 + margin * 3.0),
    )

    ready = best["score"] >= 0.44 and (margin >= 0.012 or confidence >= 0.72)
    return {
        "ready": bool(ready),
        "label": best["label"] if ready else "",
        "confidence": round(confidence, 3),
        "similarity": round(float(best["score"]), 4),
        "sample_count": 1000,
        "source": "valorant_ui_reference",
        "abilities": abilities_for_agent(best["label"]) if ready else [],
        "candidates": [
            {
                "label": item["label"],
                "similarity": round(float(item["score"]), 4),
                "sample_count": 1000,
            }
            for item in agent_scores[:5]
        ],
    }


def merge_agent_predictions(
    learned: dict[str, Any],
    reference: dict[str, Any],
) -> dict[str, Any]:
    learned_ready = bool(learned.get("ready"))
    reference_ready = bool(reference.get("ready"))

    if reference_ready and not learned_ready:
        return reference
    if learned_ready and not reference_ready:
        return learned
    if not learned_ready and not reference_ready:
        return learned if learned else reference

    learned_label = canonical_agent(str(learned.get("label") or "")) or str(learned.get("label") or "")
    reference_label = str(reference.get("label") or "")
    learned_conf = float(learned.get("confidence") or 0)
    reference_conf = float(reference.get("confidence") or 0)

    if _name_key(learned_label) == _name_key(reference_label):
        merged = dict(reference)
        merged["label"] = reference_label
        merged["confidence"] = round(min(1.0, max(learned_conf, reference_conf) + 0.08), 3)
        merged["sample_count"] = int(learned.get("sample_count") or 0)
        merged["source"] = "valorant_ui_reference+user_training"
        merged["learned_prediction"] = learned
        return merged

    # Reference HUD matching gets priority only when it is meaningfully strong.
    if reference_conf >= 0.72 and reference_conf >= learned_conf + 0.05:
        merged = dict(reference)
        merged["learned_prediction"] = learned
        return merged

    merged = dict(learned)
    merged["label"] = learned_label
    merged["reference_prediction"] = reference
    merged["source"] = "user_training"
    return merged
