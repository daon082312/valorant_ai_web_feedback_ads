from __future__ import annotations

import time
from dataclasses import dataclass

import cv2
import numpy as np


@dataclass(slots=True)
class AmmoShotUpdate:
    timestamp: float
    shots: int
    ammo_before: int
    ammo_after: int
    confidence: float


class AmmoShotDetector:
    """Conservatively count actual bullets from bottom-right ammo HUD decreases."""

    def __init__(self, config: dict):
        self.update_config(config)
        self._last_process_ts = 0.0
        self._last_ammo: int | None = None
        self._digit_templates = self._build_digit_templates()

    def update_config(self, config: dict) -> None:
        self.config = config
        self.enabled = bool(config.get("ammo_hud_detection", True))
        self.fps = max(6.0, min(20.0, float(config.get("ammo_hud_detection_fps", 12))))
        self.min_conf = max(0.35, min(0.95, float(config.get("ammo_hud_min_confidence", 0.50))))
        roi = config.get("ammo_hud_roi") or {"x": 0.875, "y": 0.865, "w": 0.115, "h": 0.120}
        self.roi = {k: float(roi.get(k, d)) for k, d in {"x":0.875,"y":0.865,"w":0.115,"h":0.120}.items()}

    def clear(self) -> None:
        self._last_process_ts = 0.0
        self._last_ammo = None

    @staticmethod
    def _normalize_glyph(mask: np.ndarray, out_w: int = 26, out_h: int = 38) -> np.ndarray | None:
        ys, xs = np.where(mask > 0)
        if len(xs) < 5:
            return None
        crop = mask[int(ys.min()):int(ys.max())+1, int(xs.min()):int(xs.max())+1]
        h, w = crop.shape[:2]
        scale = min((out_w - 4) / max(1, w), (out_h - 4) / max(1, h))
        nw, nh = max(1, int(w * scale)), max(1, int(h * scale))
        resized = cv2.resize(crop, (nw, nh), interpolation=cv2.INTER_NEAREST)
        out = np.zeros((out_h, out_w), np.uint8)
        x, y = (out_w - nw)//2, (out_h - nh)//2
        out[y:y+nh, x:x+nw] = resized
        return out

    @classmethod
    def _build_digit_templates(cls) -> dict[int, list[np.ndarray]]:
        result: dict[int, list[np.ndarray]] = {i: [] for i in range(10)}
        for digit in range(10):
            text = str(digit)
            for font in (cv2.FONT_HERSHEY_DUPLEX, cv2.FONT_HERSHEY_SIMPLEX):
                for scale in (0.9, 1.0, 1.1, 1.2):
                    for thickness in (1, 2):
                        img = np.zeros((52, 42), np.uint8)
                        (tw, th), _ = cv2.getTextSize(text, font, scale, thickness)
                        cv2.putText(img, text, ((42-tw)//2, (52+th)//2-3), font, scale, 255, thickness, cv2.LINE_AA)
                        _, img = cv2.threshold(img, 90, 255, cv2.THRESH_BINARY)
                        norm = cls._normalize_glyph(img)
                        if norm is not None:
                            result[digit].append(norm)
        return result

    def _binary_roi(self, frame: np.ndarray) -> np.ndarray | None:
        h, w = frame.shape[:2]
        x1 = max(0, min(w-1, int(w*self.roi['x'])))
        y1 = max(0, min(h-1, int(h*self.roi['y'])))
        x2 = max(x1+1, min(w, int(w*(self.roi['x']+self.roi['w']))))
        y2 = max(y1+1, min(h, int(h*(self.roi['y']+self.roi['h']))))
        crop = frame[y1:y2, x1:x2]
        if crop.size == 0:
            return None
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        _, bw = cv2.threshold(gray, 170, 255, cv2.THRESH_BINARY)
        return cv2.morphologyEx(bw, cv2.MORPH_OPEN, np.ones((2,2),np.uint8))

    def _segment_digits(self, bw: np.ndarray) -> list[np.ndarray]:
        n, _labels, stats, _ = cv2.connectedComponentsWithStats(bw, 8)
        comps=[]
        H,W=bw.shape[:2]
        for i in range(1,n):
            x,y,w,h,area=[int(v) for v in stats[i]]
            if area < 5 or h < max(7,int(H*0.18)):
                continue
            if h > int(H*0.85) or w > int(W*0.45):
                continue
            if w/max(1,h) > 1.1:
                continue
            comps.append((x,y,w,h,area))
        comps.sort(key=lambda v:v[0])
        if len(comps) > 3:
            comps = comps[-3:]
        glyphs=[]
        for x,y,w,h,_ in comps:
            norm=self._normalize_glyph(bw[y:y+h,x:x+w])
            if norm is not None:
                glyphs.append(norm)
        return glyphs

    @staticmethod
    def _similarity(a: np.ndarray, b: np.ndarray) -> float:
        af=(a>0).astype(np.float32)
        bf=(b>0).astype(np.float32)
        inter=float(np.sum(af*bf))
        denom=float(np.sum(af)+np.sum(bf))+1e-6
        dice=2.0*inter/denom
        ar=af.mean(axis=1); br=bf.mean(axis=1); ac=af.mean(axis=0); bc=bf.mean(axis=0)
        proj=1.0-(float(np.mean(np.abs(ar-br)))+float(np.mean(np.abs(ac-bc))))/2.0
        return max(0.0,min(1.0,0.78*dice+0.22*proj))

    def _classify_digit(self, glyph: np.ndarray) -> tuple[int|None,float]:
        scores=[]
        for digit, variants in self._digit_templates.items():
            best=max((self._similarity(glyph,t) for t in variants),default=0.0)
            scores.append((best,digit))
        scores.sort(reverse=True)
        best,digit=scores[0]
        second=scores[1][0] if len(scores)>1 else 0.0
        conf=max(0.0,min(1.0,best*0.88+max(0.0,best-second)*0.8))
        return digit,conf

    def _ocr(self, frame: np.ndarray) -> tuple[int|None,float]:
        bw=self._binary_roi(frame)
        if bw is None:
            return None,0.0
        glyphs=self._segment_digits(bw)
        if not glyphs or len(glyphs)>3:
            return None,0.0
        digits=[]; confs=[]
        for g in glyphs:
            d,c=self._classify_digit(g)
            if d is None:
                return None,0.0
            digits.append(str(d)); confs.append(c)
        try:
            value=int(''.join(digits))
        except ValueError:
            return None,0.0
        if value<0 or value>100:
            return None,0.0
        return value, min(confs) if confs else 0.0

    def read_ammo(self, frame_bgr: np.ndarray) -> tuple[int|None,float]:
        if frame_bgr is None or frame_bgr.size == 0:
            return None, 0.0
        return self._ocr(frame_bgr)

    def process(self, frame_bgr: np.ndarray, timestamp: float, firing_hint: bool) -> AmmoShotUpdate|None:
        ts=float(timestamp or time.time())
        if not self.enabled or frame_bgr is None or frame_bgr.size==0:
            return None
        if ts-self._last_process_ts < 1.0/self.fps:
            return None
        self._last_process_ts=ts
        ammo,conf=self._ocr(frame_bgr)
        if ammo is None or conf<self.min_conf:
            return None
        previous=self._last_ammo
        self._last_ammo=int(ammo)
        if previous is None:
            return None
        delta=previous-int(ammo)
        if delta<=0:
            return None
        if not firing_hint or delta>15:
            return None
        return AmmoShotUpdate(ts,int(delta),int(previous),int(ammo),round(conf,3))
