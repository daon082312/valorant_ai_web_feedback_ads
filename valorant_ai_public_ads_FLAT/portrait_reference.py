from __future__ import annotations

import io
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import httpx
from PIL import Image, ImageDraw

from valorant_reference import get_catalog

HTTP_TIMEOUT_SECONDS = 8.0
CACHE_TTL_SECONDS = 6 * 60 * 60

_lock = threading.RLock()
_cached_sheet: bytes | None = None
_cached_at = 0.0
_cached_signature = ""


def _signature() -> str:
    catalog = get_catalog()
    return "|".join(
        f"{agent.get('name','')}:{agent.get('icon_url','')}"
        for agent in sorted(catalog.values(), key=lambda item: item.get("name", ""))
    )


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


def build_agent_portrait_reference_sheet(force: bool = False) -> bytes:
    """Return a cached JPEG contact sheet of current playable agent portraits.

    The sheet is intentionally labelled. Gemini receives it once per combat-
    verification request and uses it as visual reference for the tiny portraits
    shown in VALORANT's killfeed. No user image is stored here.
    """
    global _cached_sheet, _cached_at, _cached_signature

    catalog = get_catalog()
    if not catalog:
        return b""

    signature = _signature()
    now = time.monotonic()
    with _lock:
        if (
            _cached_sheet
            and not force
            and signature == _cached_signature
            and now - _cached_at < CACHE_TTL_SECONDS
        ):
            return _cached_sheet

    agents = sorted(catalog.values(), key=lambda item: item.get("name", ""))
    portraits: list[tuple[str, Image.Image]] = []
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(_download_portrait, agent) for agent in agents]
        for future in as_completed(futures):
            item = future.result()
            if item is not None:
                portraits.append(item)

    portraits.sort(key=lambda item: item[0])
    if not portraits:
        return b""

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

        # Keep the transparent portrait clean against a dark neutral panel.
        panel = Image.new("RGBA", (128, 128), (19, 25, 34, 255))
        panel.alpha_composite(portrait, (0, 0))
        sheet.paste(panel.convert("RGB"), (x + 14, y + 4))

        # PIL's built-in font is enough for short English agent names and avoids
        # shipping font assets in the repository.
        label_y = y + 136
        draw.rectangle((x + 4, label_y - 2, x + cell_w - 4, y + cell_h - 4), fill=(14, 19, 27))
        draw.text((x + 10, label_y + 3), name, fill=(245, 247, 250))

    output = io.BytesIO()
    sheet.save(output, format="JPEG", quality=88, optimize=True)
    data = output.getvalue()

    with _lock:
        _cached_sheet = data
        _cached_at = now
        _cached_signature = signature
    return data
