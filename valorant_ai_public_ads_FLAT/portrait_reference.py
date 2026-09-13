from __future__ import annotations

import io
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import httpx
import numpy as np
from PIL import Image, ImageDraw

CATALOG_URL = "https://valorant-api.com/v1/agents?isPlayableCharacter=true"
HTTP_TIMEOUT_SECONDS = 8.0
CACHE_TTL_SECONDS = 6 * 60 * 60

_lock = threading.RLock()
_cached_at = 0.0
_cached_agents: list[dict[str, Any]] = []
_cached_killfeed: dict[str, Image.Image] = {}
_cached_killfeed_features: dict[str, np.ndarray] = {}
_cached_killfeed_aspects: dict[str, float] = {}
_cached_ability_icons: dict[tuple[str, str], Image.Image] = {}
_cached_reference_sheets: list[bytes] = []


def _name_key(value: str) -> str:
    return " ".join(str(value or "").strip().split()).casefold()


def _fetch_agents() -> list[dict[str, Any]]:
    with httpx.Client(timeout=HTTP_TIMEOUT_SECONDS, follow_redirects=True) as client:
        response = client.get(CATALOG_URL)
        response.raise_for_status()
        payload = response.json()

    agents: list[dict[str, Any]] = []
    for raw in payload.get("data") or []:
        if not raw.get("isPlayableCharacter"):
            continue
        name = str(raw.get("displayName") or "").strip()
        if not name:
            continue
        abilities = []
        for ability in raw.get("abilities") or []:
            ability_name = str(ability.get("displayName") or "").strip()
            icon_url = str(ability.get("displayIcon") or "").strip()
            slot = str(ability.get("slot") or "").strip()
            if ability_name and icon_url:
                abilities.append({
                    "name": ability_name,
                    "slot": slot,
                    "icon_url": icon_url,
                })
        agents.append({
            "name": name,
            # This is the actual killfeed-specific portrait asset. Do not use
            # displayIcon here: the in-game killfeed uses a different rendering.
            "killfeed_url": str(raw.get("killfeedPortrait") or raw.get("displayIcon") or "").strip(),
            "abilities": abilities,
        })
    agents.sort(key=lambda item: item["name"])
    return agents


def _download_image(url: str) -> Image.Image | None:
    if not url:
        return None
    try:
        with httpx.Client(timeout=HTTP_TIMEOUT_SECONDS, follow_redirects=True) as client:
            response = client.get(url)
            response.raise_for_status()
        with Image.open(io.BytesIO(response.content)) as source:
            return source.convert("RGBA").copy()
    except Exception as exc:
        print(f"[UIReference] asset skipped: {type(exc).__name__}: {exc}")
        return None


def _portrait_feature(image: Image.Image) -> np.ndarray:
    # Team tint and compression alter colour. Match luminance + edges instead.
    rgba = image.convert("RGBA")
    bg = Image.new("RGBA", rgba.size, (16, 20, 28, 255))
    bg.alpha_composite(rgba)
    gray = np.asarray(
        bg.convert("L").resize((48, 32), Image.Resampling.BILINEAR),
        dtype=np.float32,
    ) / 255.0
    low = float(np.percentile(gray, 10))
    high = float(np.percentile(gray, 90))
    if high - low > 0.04:
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


def _ensure_assets(force: bool = False) -> list[dict[str, Any]]:
    global _cached_at, _cached_agents, _cached_killfeed
    global _cached_killfeed_features, _cached_killfeed_aspects
    global _cached_ability_icons, _cached_reference_sheets

    now = time.monotonic()
    with _lock:
        if _cached_agents and not force and now - _cached_at < CACHE_TTL_SECONDS:
            return _cached_agents

    try:
        agents = _fetch_agents()
    except Exception as exc:
        print(f"[UIReference] catalog fetch failed: {type(exc).__name__}: {exc}")
        with _lock:
            return _cached_agents

    tasks: list[tuple[str, str, str, str]] = []
    for agent in agents:
        tasks.append(("killfeed", agent["name"], "", agent["killfeed_url"]))
        for ability in agent["abilities"]:
            tasks.append(("ability", agent["name"], ability["name"], ability["icon_url"]))

    killfeed: dict[str, Image.Image] = {}
    ability_icons: dict[tuple[str, str], Image.Image] = {}

    with ThreadPoolExecutor(max_workers=16) as executor:
        future_map = {
            executor.submit(_download_image, url): (kind, agent, ability)
            for kind, agent, ability, url in tasks
            if url
        }
        for future in as_completed(future_map):
            kind, agent, ability = future_map[future]
            image = future.result()
            if image is None:
                continue
            if kind == "killfeed":
                killfeed[agent] = image
            else:
                ability_icons[(agent, ability)] = image

    features = {name: _portrait_feature(image) for name, image in killfeed.items()}
    aspects = {
        name: max(0.55, min(2.6, image.width / max(1, image.height)))
        for name, image in killfeed.items()
    }

    with _lock:
        _cached_agents = agents
        _cached_killfeed = killfeed
        _cached_killfeed_features = features
        _cached_killfeed_aspects = aspects
        _cached_ability_icons = ability_icons
        _cached_reference_sheets = []
        _cached_at = now
    return agents


def ability_names_for_agent(agent_name: str) -> list[str]:
    wanted = _name_key(agent_name)
    for agent in _ensure_assets():
        if _name_key(agent["name"]) == wanted:
            return [str(a["name"]) for a in agent["abilities"]]
    return []


def _fit_rgba(image: Image.Image, width: int, height: int) -> Image.Image:
    src = image.convert("RGBA")
    ratio = min(width / max(1, src.width), height / max(1, src.height))
    size = (max(1, int(src.width * ratio)), max(1, int(src.height * ratio)))
    src = src.resize(size, Image.Resampling.LANCZOS)
    out = Image.new("RGBA", (width, height), (18, 23, 32, 255))
    out.alpha_composite(src, ((width - size[0]) // 2, (height - size[1]) // 2))
    return out


def build_ui_reference_sheets(force: bool = False) -> list[bytes]:
    """Build two readable sheets using exact killfeed portraits + ability icons.

    Each agent cell contains the in-game killfeed portrait and four official
    ability glyphs with names. Gemini uses these as a visual dictionary instead
    of relying on memorized agent/skill appearance.
    """
    global _cached_reference_sheets
    agents = _ensure_assets(force=force)
    if not agents:
        return []

    with _lock:
        if _cached_reference_sheets and not force:
            return list(_cached_reference_sheets)
        killfeed = dict(_cached_killfeed)
        ability_icons = dict(_cached_ability_icons)

    # 2 columns x 7 rows = 14 agents/sheet, keeping text/icons large enough.
    per_sheet = 14
    sheets: list[bytes] = []
    for start in range(0, len(agents), per_sheet):
        group = agents[start:start + per_sheet]
        columns = 2
        rows = (len(group) + columns - 1) // columns
        cell_w, cell_h, gap = 590, 158, 10
        canvas = Image.new(
            "RGB",
            (columns * cell_w + (columns + 1) * gap, rows * cell_h + (rows + 1) * gap),
            (7, 10, 15),
        )
        draw = ImageDraw.Draw(canvas)

        for index, agent in enumerate(group):
            col, row = index % columns, index // columns
            x = gap + col * (cell_w + gap)
            y = gap + row * (cell_h + gap)
            draw.rounded_rectangle(
                (x, y, x + cell_w, y + cell_h), radius=8,
                fill=(17, 23, 32), outline=(55, 68, 84), width=1,
            )
            draw.text((x + 12, y + 8), agent["name"], fill=(250, 252, 255))

            portrait = killfeed.get(agent["name"])
            if portrait is not None:
                panel = _fit_rgba(portrait, 132, 74).convert("RGB")
                canvas.paste(panel, (x + 12, y + 34))
            draw.text((x + 14, y + 114), "KILLFEED", fill=(170, 184, 200))

            abilities = agent["abilities"][:4]
            for ai, ability in enumerate(abilities):
                ax = x + 158 + ai * 104
                icon = ability_icons.get((agent["name"], ability["name"]))
                if icon is not None:
                    panel = _fit_rgba(icon, 58, 58).convert("RGB")
                    canvas.paste(panel, (ax + 18, y + 34))
                label = ability["name"][:15]
                draw.text((ax + 2, y + 98), label, fill=(235, 240, 246))
                draw.text((ax + 2, y + 118), ability["slot"][:9], fill=(135, 151, 170))

        output = io.BytesIO()
        canvas.save(output, format="JPEG", quality=90, optimize=True)
        sheets.append(output.getvalue())

    with _lock:
        _cached_reference_sheets = list(sheets)
    return sheets


def build_agent_portrait_reference_sheet(force: bool = False) -> bytes:
    # Backward-compatible helper. The first combined UI sheet now contains the
    # exact killfeed portrait, not the menu/display portrait.
    sheets = build_ui_reference_sheets(force=force)
    return sheets[0] if sheets else b""


def match_agent_portrait_in_killfeed(image_bytes: bytes, agent_name: str) -> dict:
    """Find the exact killfeed-specific portrait inside an enlarged killfeed crop."""
    _ensure_assets()
    wanted = _name_key(agent_name)
    with _lock:
        target_name = next((name for name in _cached_killfeed_features if _name_key(name) == wanted), "")
        target = _cached_killfeed_features.get(target_name)
        aspect = float(_cached_killfeed_aspects.get(target_name) or 1.0)
    if target is None:
        return {"ready": False, "reason": "killfeed_reference_missing", "agent": agent_name}

    try:
        with Image.open(io.BytesIO(image_bytes)) as source:
            image = source.convert("RGB")
    except Exception:
        return {"ready": False, "reason": "invalid_image", "agent": target_name}

    # Normalize large browser crops for predictable CPU usage.
    if image.width > 800:
        scale = 800.0 / image.width
        image = image.resize(
            (800, max(1, int(round(image.height * scale)))),
            Image.Resampling.BILINEAR,
        )

    width, height = image.size
    if width < 100 or height < 60:
        return {"ready": False, "reason": "image_too_small", "agent": target_name}

    best_score = -1.0
    best_box = None
    # Killfeed portraits are not guaranteed to be square. Preserve the actual
    # reference aspect ratio while scanning multiple UI scales and all rows.
    heights = sorted({
        max(24, int(height * 0.055)),
        max(32, int(height * 0.075)),
        max(42, int(height * 0.10)),
        max(52, int(height * 0.125)),
    })
    for h in heights:
        w = max(22, min(int(round(h * aspect)), int(width * 0.28)))
        step_y = max(8, h // 3)
        step_x = max(10, w // 3)
        for top in range(0, max(1, height - h + 1), step_y):
            for left in range(0, max(1, width - w + 1), step_x):
                crop = image.crop((left, top, left + w, top + h))
                score = float(np.dot(_portrait_feature(crop), target))
                if score > best_score:
                    best_score = score
                    best_box = (left, top, w, h)

    # Keep the local matcher conservative; Gemini receives the raw similarity
    # plus enlarged killfeed and makes the final attacker/victim decision.
    ready = best_score >= 0.58
    return {
        "ready": bool(ready),
        "agent": target_name,
        "similarity": round(max(-1.0, min(1.0, best_score)), 4),
        "box": list(best_box) if best_box else None,
        "source": "exact_killfeed_portrait_match",
    }
