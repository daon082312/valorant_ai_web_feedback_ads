from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable

from pynput import keyboard, mouse


MOVE_KEYS = {"w", "a", "s", "d"}
OPPOSITE_KEYS = {"a": "d", "d": "a", "w": "s", "s": "w"}

_KEY_ALIASES = {
    "control": "ctrl",
    "ctrl_l": "ctrl",
    "ctrl_r": "ctrl",
    "shift_l": "shift",
    "shift_r": "shift",
    "alt_l": "alt",
    "alt_r": "alt",
    "mouse_x1": "mouse4",
    "x1": "mouse4",
    "mouse_x2": "mouse5",
    "x2": "mouse5",
}


def normalize_binding(value: str) -> str:
    value = str(value or "").strip().lower().replace("key.", "")
    value = value.replace(" ", "_")
    return _KEY_ALIASES.get(value, value)


@dataclass(slots=True)
class KeyEvent:
    timestamp: float
    key: str
    pressed: bool
    keys: tuple[str, ...]


@dataclass(slots=True)
class SkillEvent:
    timestamp: float
    slot_id: str
    label: str
    binding: str
    moving: bool
    walking: bool
    crouching: bool
    keys: tuple[str, ...]
    source: str = "key_candidate"
    confidence: float | None = None


@dataclass(slots=True)
class ShotEvent:
    timestamp: float
    moving: bool
    walking: bool
    crouching: bool
    keys: tuple[str, ...]
    move_keys: tuple[str, ...]
    stop_to_shot_ms: float | None
    direction_change_to_shot_ms: float | None
    opposite_tap_recent: bool


class InputTracker:
    """Global keyboard/mouse tracker for local coaching features only."""

    def __init__(self, on_shot: Callable[[ShotEvent], None] | None = None, on_key_event: Callable[[KeyEvent], None] | None = None, on_skill: Callable[[SkillEvent], None] | None = None, skill_bindings: dict[str, dict] | None = None):
        self.on_shot = on_shot
        self.on_key_event = on_key_event
        self.on_skill = on_skill
        self._lock = threading.Lock()
        self._pressed: set[str] = set()
        self._keyboard_listener: keyboard.Listener | None = None
        self._mouse_listener: mouse.Listener | None = None
        self._running = False
        self._last_move_release_ts: float | None = None
        self._last_direction_change_ts: float | None = None
        self._last_direction_key: str | None = None
        self._last_opposite_tap_ts: float | None = None
        self._left_mouse_down = False
        self._last_left_press_ts: float | None = None
        self._last_left_release_ts: float | None = None
        self._skill_bindings: dict[str, dict] = {}
        self.set_skill_bindings(skill_bindings or {})

    @property
    def running(self) -> bool:
        return self._running

    def set_skill_bindings(self, bindings: dict[str, dict]) -> None:
        cleaned: dict[str, dict] = {}
        for slot_id, item in dict(bindings or {}).items():
            if not isinstance(item, dict):
                continue
            key = normalize_binding(item.get("key", ""))
            if not key:
                continue
            cleaned[str(slot_id)] = {"label": str(item.get("label") or slot_id).strip() or str(slot_id), "key": key}
        with self._lock:
            self._skill_bindings = cleaned

    def skill_bindings(self) -> dict[str, dict]:
        with self._lock:
            return {key: dict(value) for key, value in self._skill_bindings.items()}

    def _key_name(self, key) -> str | None:
        try:
            char = getattr(key, "char", None)
            if char:
                return normalize_binding(str(char))
        except Exception:
            pass
        special = {keyboard.Key.shift: "shift", keyboard.Key.shift_l: "shift", keyboard.Key.shift_r: "shift", keyboard.Key.ctrl: "ctrl", keyboard.Key.ctrl_l: "ctrl", keyboard.Key.ctrl_r: "ctrl", keyboard.Key.alt: "alt", keyboard.Key.alt_l: "alt", keyboard.Key.alt_r: "alt", keyboard.Key.space: "space", keyboard.Key.tab: "tab", keyboard.Key.caps_lock: "caps_lock", keyboard.Key.enter: "enter", keyboard.Key.esc: "esc"}
        if key in special:
            return special[key]
        name = getattr(key, "name", None)
        if name:
            return normalize_binding(str(name))
        return None

    @staticmethod
    def _mouse_name(button) -> str | None:
        mapping = {mouse.Button.middle: "middle", mouse.Button.right: "right_mouse"}
        x1 = getattr(mouse.Button, "x1", None)
        x2 = getattr(mouse.Button, "x2", None)
        if x1 is not None:
            mapping[x1] = "mouse4"
        if x2 is not None:
            mapping[x2] = "mouse5"
        return mapping.get(button)

    def _emit_key(self, timestamp: float, name: str, pressed: bool, keys: tuple[str, ...]) -> None:
        if self.on_key_event:
            self.on_key_event(KeyEvent(timestamp=timestamp, key=name, pressed=pressed, keys=keys))

    def _skill_for_binding(self, name: str) -> tuple[str, dict] | None:
        for slot_id, item in self._skill_bindings.items():
            if item.get("key") == name:
                return slot_id, item
        return None

    def _emit_skill(self, timestamp: float, name: str, keys: tuple[str, ...]) -> None:
        if not self.on_skill:
            return
        with self._lock:
            matched = self._skill_for_binding(name)
        if not matched:
            return
        slot_id, item = matched
        move_keys = {key for key in keys if key in MOVE_KEYS}
        self.on_skill(SkillEvent(timestamp=timestamp, slot_id=slot_id, label=str(item.get("label") or slot_id), binding=name, moving=bool(move_keys), walking="shift" in keys, crouching="ctrl" in keys, keys=keys))

    def _on_press(self, key) -> None:
        name = self._key_name(key)
        if not name:
            return
        now = time.time()
        with self._lock:
            already_pressed = name in self._pressed
            self._pressed.add(name)
            if name in MOVE_KEYS and not already_pressed:
                if self._last_direction_key and self._last_direction_key != name:
                    self._last_direction_change_ts = now
                    if OPPOSITE_KEYS.get(self._last_direction_key) == name:
                        self._last_opposite_tap_ts = now
                self._last_direction_key = name
            keys = tuple(sorted(self._pressed))
        if not already_pressed:
            self._emit_key(now, name, True, keys)
            self._emit_skill(now, name, keys)

    def _on_release(self, key) -> None:
        name = self._key_name(key)
        if not name:
            return
        now = time.time()
        with self._lock:
            was_pressed = name in self._pressed
            self._pressed.discard(name)
            if name in MOVE_KEYS and was_pressed:
                self._last_move_release_ts = now
            keys = tuple(sorted(self._pressed))
        if was_pressed:
            self._emit_key(now, name, False, keys)

    def _on_click(self, _x, _y, button, pressed) -> None:
        now = time.time()
        if button == mouse.Button.left:
            with self._lock:
                self._left_mouse_down = bool(pressed)
                if pressed:
                    self._last_left_press_ts = now
                else:
                    self._last_left_release_ts = now
            if not pressed:
                return
            with self._lock:
                keys = tuple(sorted(self._pressed))
                move_keys = tuple(sorted(key for key in self._pressed if key in MOVE_KEYS))
                moving = bool(move_keys)
                walking = "shift" in self._pressed
                crouching = "ctrl" in self._pressed
                stop_to_shot_ms = None
                if not moving and self._last_move_release_ts is not None:
                    stop_to_shot_ms = max(0.0, (now - self._last_move_release_ts) * 1000.0)
                direction_change_to_shot_ms = None
                if self._last_direction_change_ts is not None:
                    direction_change_to_shot_ms = max(0.0, (now - self._last_direction_change_ts) * 1000.0)
                opposite_recent = bool(self._last_opposite_tap_ts is not None and (now - self._last_opposite_tap_ts) <= 0.35)
            if self.on_shot:
                self.on_shot(ShotEvent(timestamp=now, moving=moving, walking=walking, crouching=crouching, keys=keys, move_keys=move_keys, stop_to_shot_ms=stop_to_shot_ms, direction_change_to_shot_ms=direction_change_to_shot_ms, opposite_tap_recent=opposite_recent))
            return

        name = self._mouse_name(button)
        if not name:
            return
        with self._lock:
            if pressed:
                already_pressed = name in self._pressed
                self._pressed.add(name)
            else:
                already_pressed = name in self._pressed
                self._pressed.discard(name)
            keys = tuple(sorted(self._pressed))
        if pressed and not already_pressed:
            self._emit_key(now, name, True, keys)
            self._emit_skill(now, name, keys)
        elif not pressed and already_pressed:
            self._emit_key(now, name, False, keys)

    def fire_hint_active(self, timestamp: float | None = None, grace_seconds: float = 0.28) -> bool:
        now = float(timestamp or time.time())
        with self._lock:
            if self._left_mouse_down:
                return True
            if self._last_left_press_ts is None:
                return False
            return (now - self._last_left_press_ts) <= max(0.05, float(grace_seconds))

    def is_moving(self) -> bool:
        with self._lock:
            return any(key in MOVE_KEYS for key in self._pressed)

    def pressed_keys(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(sorted(self._pressed))

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._keyboard_listener = keyboard.Listener(on_press=self._on_press, on_release=self._on_release)
        self._mouse_listener = mouse.Listener(on_click=self._on_click)
        self._keyboard_listener.start()
        self._mouse_listener.start()

    def stop(self) -> None:
        self._running = False
        if self._keyboard_listener:
            self._keyboard_listener.stop()
            self._keyboard_listener = None
        if self._mouse_listener:
            self._mouse_listener.stop()
            self._mouse_listener = None
