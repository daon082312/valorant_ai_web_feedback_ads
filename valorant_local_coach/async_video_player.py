from __future__ import annotations

import queue
import threading
import time
from pathlib import Path

import cv2


class AsyncVideoDecoder:
    """Owns VideoCapture on a background thread and publishes only recent frames.

    Tk/PIL rendering stays on the UI thread. The decoder never touches Tk widgets.
    """

    def __init__(self, max_display_fps: float = 24.0):
        self.max_display_fps = max(8.0, min(30.0, float(max_display_fps)))
        self.messages: queue.Queue[tuple] = queue.Queue(maxsize=64)
        self.commands: queue.Queue[tuple] = queue.Queue()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def open(self, path: str | Path) -> None:
        self.close()
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._worker,
            args=(str(path),),
            daemon=True,
            name="ValorantVideoDecoder",
        )
        self._thread.start()

    def play(self) -> None:
        self.commands.put(("play",))

    def pause(self) -> None:
        self.commands.put(("pause",))

    def seek(self, seconds: float) -> None:
        kept: list[tuple] = []
        try:
            while True:
                item = self.commands.get_nowait()
                if not item or item[0] != "seek":
                    kept.append(item)
        except queue.Empty:
            pass
        for item in kept:
            self.commands.put(item)
        self.commands.put(("seek", max(0.0, float(seconds))))

    def close(self) -> None:
        self._stop.set()
        try:
            self.commands.put_nowait(("stop",))
        except Exception:
            pass
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=0.8)
        self._thread = None
        self._clear_queue(self.messages)
        self._clear_queue(self.commands)

    @staticmethod
    def _clear_queue(q: queue.Queue) -> None:
        try:
            while True:
                q.get_nowait()
        except queue.Empty:
            return

    def _publish(self, message: tuple) -> None:
        """Publish without allowing disposable frames to evict control metadata."""
        if not message:
            return
        kind = message[0]
        if kind == "frame":
            try:
                self.messages.put_nowait(message)
            except queue.Full:
                pass
            return
        try:
            self.messages.put_nowait(message)
            return
        except queue.Full:
            pass
        kept: list[tuple] = []
        removed_frame = False
        try:
            while True:
                item = self.messages.get_nowait()
                if not removed_frame and item and item[0] == "frame":
                    removed_frame = True
                    continue
                kept.append(item)
        except queue.Empty:
            pass
        for item in kept:
            try:
                self.messages.put_nowait(item)
            except queue.Full:
                break
        try:
            self.messages.put_nowait(message)
        except queue.Full:
            pass

    def _worker(self, path: str) -> None:
        cap = cv2.VideoCapture(path)
        if not cap.isOpened():
            self._publish(("error", "선택한 영상을 열 수 없습니다."))
            return
        fps = max(1.0, float(cap.get(cv2.CAP_PROP_FPS) or 30.0))
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        duration = frame_count / fps if frame_count > 0 else 0.0
        step = max(1, int(round(fps / self.max_display_fps)))
        self._publish(("meta", fps, duration, frame_count, step))

        playing = False
        current = 0.0
        next_deadline = time.monotonic()

        def read_and_publish() -> bool:
            nonlocal current
            ok, frame = cap.read()
            if not ok:
                return False
            for _ in range(max(0, step - 1)):
                if not cap.grab():
                    break
            pos_ms = float(cap.get(cv2.CAP_PROP_POS_MSEC) or 0.0)
            current = pos_ms / 1000.0 if pos_ms > 0 else current + step / fps
            self._publish(("frame", current, frame))
            return True

        cap.set(cv2.CAP_PROP_POS_MSEC, 0.0)
        read_and_publish()
        current = 0.0

        while not self._stop.is_set():
            try:
                while True:
                    cmd = self.commands.get_nowait()
                    kind = cmd[0]
                    if kind == "stop":
                        self._stop.set()
                        break
                    if kind == "pause":
                        playing = False
                    elif kind == "play":
                        playing = True
                        next_deadline = time.monotonic()
                    elif kind == "seek":
                        playing = False
                        target = max(0.0, min(float(cmd[1]), duration if duration > 0 else float(cmd[1])))
                        cap.set(cv2.CAP_PROP_POS_MSEC, target * 1000.0)
                        current = target
                        if read_and_publish():
                            self._publish(("seeked", current))
                        else:
                            self._publish(("seeked", target))
            except queue.Empty:
                pass

            if self._stop.is_set():
                break
            if not playing:
                time.sleep(0.012)
                continue

            now = time.monotonic()
            if now < next_deadline:
                time.sleep(min(0.010, next_deadline - now))
                continue
            if not read_and_publish():
                playing = False
                self._publish(("eof", current))
                continue
            if duration > 0 and current >= duration - 0.02:
                playing = False
                self._publish(("eof", current))
                continue
            frame_period = step / fps
            next_deadline = max(next_deadline + frame_period, time.monotonic() - frame_period)

        cap.release()
