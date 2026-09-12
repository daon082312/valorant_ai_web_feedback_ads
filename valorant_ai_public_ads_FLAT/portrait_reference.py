from __future__ import annotations

import io
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import httpx
import numpy as np
from PIL import Image, ImageDraw

from valorant_reference import get_catalog

HTTP_TIMEOUT_SECONDS = 8.0
CACHE_TTL_SECONDS = 6 * 60 * 60

_lock = threading.RLock()
_cached_sheet: bytes | None = None
_cached_at = 0.0
_cached_signature = ""
_cached_portraits: dict[str, Image.Image] = {}
_cached_features: dict[str, np.ndarray] = {}


def _signature() -> str:
    catalog = get_catalog()
    return "|".join(
        f"{agent.get('name','')}:{agent.get('icon_url','')}"
        for agent in sorted(catalog.values(), key=lambda item: item.get("name", ""))
    )


def _name_key(value: str) -> str:
    return " ".join(str(value or "").strip().split()).casefold()


def _download_portrait(agent: dict) -> tuple[str, Image.Image] | None:
    url = str(agent.get("icon_url") or "").strip()
    name = str(agent.get("name") or "").strip()
    if not url or not name:
        return None
    try:
        with httpx.Client(timeout=HTTP_TIMEOUT_SECONDS, follow_redirects=True) as client:
            response = client.get(url)
            response.raise_for_status()
        with Image.open(io.BytesIO(response.content)) as source:
            portrait = source.convert("RGBA").resize((128, 128), Image.Resampling.LANCZOS)
            return name, portrait.copy()
    except Exception as exc:
        print(f"[PortraitReference] {name} portrait skipped: {type(exc).__name__}: {exc}")
        return None


def _portrait_feature(image: Image.Image) -> np.ndarray:
    gray = np.asarray(
        image.convert("L").resize((32, 32), Image.Resampling.BILINEAR),
        dtype=np.float32,
    ) / 255.0
    low = float(np.percentile(gray, 12))
    high = float(np.percentile(gray, 88))
    if high - low > 0.05:
        gray = np.clip((gray - low) / (high - low), 0.0, 1.0)
    gx = np.diff(gray, axis=1, prepend=gray[:, :1])
    gy = np.diff(gray, axis=0, prepend=gray[:1, :])
    feature = np.concatenate([
        gray.reshape(-1),
        np.abs(gx).reshape(-1),
        np.abs(gy).reshape(-1),
    ]).astype(np.float32)
    feature -= float(feature.mean())
    norm = float(np.linalg.norm(feature))
    if norm > 1e-8:
        feature /= norm
    return feature


def _ensure_portraits(force: bool = False) -> list[tuple[str, Image.Image]]:
    global _cached_portraits, _cached_features, _cached_at, _cached_signature, _cached_sheet
    catalog = get_catalog()
    if not catalog:
        return []

    signature = _signature()
    now = time.monotonic()
    with _lock:
        if (
            _cached_portraits
            and not force
            and signature == _cached_signature
            and now - _cached_at < CACHE_TTL_SECONDS
        ):
            return sorted(_cached_portraits.items(), key=lambda item: item[0])

    agents = sorted(catalog.values(), key=lambda item: item.get("name", ""))
    portraits: list[tuple[str, Image.Image]] = []
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(_download_portrait, agent) for agent in agents]
        for future in as_completed(futures):
            item = future.result()
            if item is not None:
                portraits.append(item)

    portraits.sort(key=lambda item: item[0])
    with _lock:
        _cached_portraits = {name: portrait for name, portrait in portraits}
        _cached_features = {name: _portrait_feature(portrait) for name, portrait in portraits}
        _cached_at = now
        if signature != _cached_signature or force:
            _cached_sheet = None
        _cached_signature = signature
    return portraits


def build_agent_portrait_reference_sheet(force: bool = False) -> bytes:
    """Return a cached labelled JPEG sheet of current playable agent portraits."""
    global _cached_sheet

    portraits = _ensure_portraits(force=force)
    if not portraits:
        return b""

    with _lock:
        if _cached_sheet and not force:
            return _cached_sheet

    columns = 5
    cell_w = 156
    cell_h = 162
    padding = 10
    rows = (len(portraits) + columns - 1) // columns
    sheet = Image.new(
        "RGB",
        (columns * cell_w + (columns + 1) * padding, rows * cell_h + (rows + 1) * padding),
        (8, 11, 16),
    )
    draw = ImageDraw.Draw(sheet)

    for index, (name, portrait) in enumerate(portraits):
        col = index % columns
        row = index // columns
        x = padding + col * cell_w
        y = padding + row * cell_h
        panel = Image.new("RGBA", (128, 128), (19, 25, 34, 255))
        panel.alpha_composite(portrait, (0, 0))
        sheet.paste(panel.convert("RGB"), (x + 14, y + 4))
        label_y = y + 136
        draw.rectangle((x + 4, label_y - 2, x + cell_w - 4, y + cell_h - 4), fill=(14, 19, 27))
        draw.text((x + 10, label_y + 3), name, fill=(245, 247, 250))

    output = io.BytesIO()
    sheet.save(output, format="JPEG", quality=88, optimize=True)
    data = output.getvalue()
    with _lock:
        _cached_sheet = data
    return data


def match_agent_portrait_in_killfeed(image_bytes: bytes, agent_name: str) -> dict:
    """Look for one agent portrait inside an enlarged killfeed crop.

    This is a cheap reference matcher, not a final kill decision. Side/row and
    assist semantics are still decided by Gemini.
    """
    _ensure_portraits()
    wanted_key = _name_key(agent_name)
    with _lock:
        target_name = next((name for name in _cached_features if _name_key(name) == wanted_key), "")
        target = _cached_features.get(target_name)
    if target is None:
        return {"ready": False, "reason": "agent_reference_missing", "agent": agent_name}

    try:
        with Image.open(io.BytesIO(image_bytes)) as source:
            image = source.convert("RGB")
    except Exception:
        return {"ready": False, "reason": "invalid_image", "agent": target_name}

    if image.width > 720:
        scale = 720.0 / image.width
        image = image.resize(
            (720, max(1, int(round(image.height * scale)))),
            Image.Resampling.BILINEAR,
        )

    width, height = image.size
    if width < 80 or height < 60:
        return {"ready": False, "reason": "image_too_small", "agent": target_name}

    sizes = sorted({
        max(28, int(height * 0.075)),
        max(34, int(height * 0.095)),
        max(42, int(height * 0.12)),
    })
    best_score = -1.0
    best_box = None

    # In the enlarged crop, real killfeed rows are concentrated toward the
    # right. Scanning only that region keeps CPU use low while still allowing
    # assist icons to shift the exact portrait location.
    left_start = int(width * 0.22)
    for size in sizes:
        step = max(10, size // 2)
        for top in range(0, max(1, height - size + 1), step):
            for left in range(left_start, max(left_start + 1, width - size + 1), step):
                crop = image.crop((left, top, left + size, top + size))
                feature = _portrait_feature(crop)
                score = float(np.dot(feature, target))
                if score > best_score:
                    best_score = score
                    best_box = (left, top, size, size)

    ready = best_score >= 0.54
    return {
        "ready": bool(ready),
        "agent": target_name,
        "similarity": round(max(-1.0, min(1.0, best_score)), 4),
        "box": list(best_box) if best_box else None,
        "source": "agent_portrait_reference_match",
    }
