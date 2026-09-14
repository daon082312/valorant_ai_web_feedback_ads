from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable

from pynput import keyboard, mouse


MOVE_KEYS = {"w", "a", "s", "d"}


@dataclass(slots=True)
class ShotEvent:
    timestamp: float
    moving: bool
    keys: tuple[str, ...]


class InputTracker:
    """Global keyboard/mouse tracker for local coaching features only."""

    def __init__(self, on_shot: Callable[[ShotEvent], None] | None = None):
        self.on_shot = on_shot
        self._lock = threading.Lock()
        self._pressed: set[str] = set()
        self._keyboard_listener: keyboard.Listener | None = None
        self._mouse_listener: mouse.Listener | None = None
        self._running = False

    @property
    def running(self) -> bool:
        return self._running

    def _key_name(self, key) -> str | None:
        try:
            if key.char:
                return str(key.char).lower()
        except AttributeError:
            return None
        return None

    def _on_press(self, key) -> None:
        name = self._key_name(key)
        if name:
            with self._lock:
                self._pressed.add(name)

    def _on_release(self, key) -> None:
        name = self._key_name(key)
        if name:
            with self._lock:
                self._pressed.discard(name)

    def _on_click(self, _x, _y, button, pressed) -> None:
        if not pressed or button != mouse.Button.left:
            return
        with self._lock:
            keys = tuple(sorted(self._pressed))
        moving = any(key in MOVE_KEYS for key in keys)
        if self.on_shot:
            self.on_shot(ShotEvent(timestamp=time.time(), moving=moving, keys=keys))

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
        self._keyboard_listener = keyboard.Listener(
            on_press=self._on_press,
            on_release=self._on_release,
        )
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
