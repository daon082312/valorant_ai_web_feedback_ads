from __future__ import annotations

import json
import statistics
import time
from collections import defaultdict
from pathlib import Path
from typing import Callable

import cv2
import numpy as np

from ammo_shot_detector import AmmoShotDetector
from vision_analyzer import VisionAnalyzer


DEFAULT_SLOT_ROIS = {
    "skill1": {"x": 0.365, "y": 0.875, "w": 0.060, "h": 0.105},
    "skill2": {"x": 0.430, "y": 0.875, "w": 0.060, "h": 0.105},
    "skill3": {"x": 0.495, "y": 0.875, "w": 0.060, "h": 0.105},
    "ultimate": {"x": 0.560, "y": 0.875, "w": 0.065, "h": 0.105},
}

try:
    cv2.setNumThreads(1)
    cv2.ocl.setUseOpenCL(False)
except Exception:
    pass


class VideoFileAnalyzer:
    """Sparse, fully-local analysis for imported VALORANT videos."""

    def __init__(self, root_dir: Path, config: dict):
        self.root_dir = Path(root_dir)
        self.config = dict(config)
        self.sample_fps = max(1.0, min(8.0, float(config.get("video_file_analysis_fps", 3))))
        self.vision = VisionAnalyzer(self.root_dir, self.config)
        self.ammo = AmmoShotDetector(self.config)
        self.skill_threshold = max(3.0, float(config.get("skill_hud_change_threshold", 8.0)))
        self.skill_cooldown = max(0.7, float(config.get("skill_hud_event_cooldown_seconds", 1.0)))
        rois = config.get("skill_hud_rois") or DEFAULT_SLOT_ROIS
        self.skill_rois = {
            slot: {k: float((rois.get(slot) or default).get(k, default[k])) for k in ("x", "y", "w", "h")}
            for slot, default in DEFAULT_SLOT_ROIS.items()
        }

    @staticmethod
    def _gray_small(frame: np.ndarray) -> np.ndarray:
        h, w = frame.shape[:2]
        roi = frame[int(h * 0.18):int(h * 0.82), int(w * 0.20):int(w * 0.80)]
        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        return cv2.resize(gray, (160, 90), interpolation=cv2.INTER_AREA)

    @staticmethod
    def _crop_skill(frame: np.ndarray, roi: dict) -> np.ndarray | None:
        h, w = frame.shape[:2]
        x1 = max(0, min(w - 1, int(w * roi["x"])))
        y1 = max(0, min(h - 1, int(h * roi["y"])))
        x2 = max(x1 + 1, min(w, int(w * (roi["x"] + roi["w"]))))
        y2 = max(y1 + 1, min(h, int(h * (roi["y"] + roi["h"]))))
        crop = frame[y1:y2, x1:x2]
        if crop.size == 0:
            return None
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        gray = cv2.resize(gray, (48, 48), interpolation=cv2.INTER_AREA)
        return cv2.GaussianBlur(gray, (3, 3), 0)

    def _skill_changes(self, frame: np.ndarray, ts: float, previous: dict[str, np.ndarray], evidence: dict[str, int], last_event: dict[str, float]) -> list[dict]:
        events: list[dict] = []
        for slot, roi in self.skill_rois.items():
            crop = self._crop_skill(frame, roi)
            if crop is None:
                continue
            prev = previous.get(slot)
            previous[slot] = crop
            if prev is None or prev.shape != crop.shape:
                continue
            diff = float(np.mean(cv2.absdiff(prev, crop)))
            evidence[slot] = evidence[slot] + 1 if diff >= self.skill_threshold else 0
            if evidence[slot] >= 2 and ts - last_event.get(slot, -999.0) >= self.skill_cooldown:
                confidence = max(0.45, min(0.95, 0.50 + (diff - self.skill_threshold) / max(20.0, self.skill_threshold * 4.0)))
                events.append({"slot_id": slot, "timestamp": round(ts, 2), "confidence": round(confidence, 3), "change": round(diff, 2)})
                last_event[slot] = ts
                evidence[slot] = 0
        return events

    @staticmethod
    def _group_bursts(shot_times: list[float], gap: float = 0.75) -> list[list[float]]:
        if not shot_times:
            return []
        groups = [[shot_times[0]]]
        for ts in shot_times[1:]:
            if ts - groups[-1][-1] > gap:
                groups.append([ts])
            else:
                groups[-1].append(ts)
        return groups

    def analyze(self, video_path: str | Path, progress: Callable[[float, str], None] | None = None) -> dict:
        path = Path(video_path)
        capture = cv2.VideoCapture(str(path))
        if not capture.isOpened():
            raise RuntimeError("영상 파일을 열 수 없습니다. MP4/AVI/MOV/MKV 형식을 확인해 주세요.")

        src_fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
        if src_fps <= 0.1:
            src_fps = 30.0
        total_frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        duration = (total_frames / src_fps) if total_frames > 0 else 0.0
        sample_every = max(1, int(round(src_fps / self.sample_fps)))

        self.vision.clear()
        self.ammo.clear()
        base_wall = time.time()
        sampled = 0
        index = 0
        previous_gray = None
        motion_values: list[float] = []
        firing_motion: list[float] = []
        last_ammo: int | None = None
        shot_times: list[float] = []
        ammo_reads = 0
        ammo_confidences: list[float] = []
        skill_previous: dict[str, np.ndarray] = {}
        skill_evidence: dict[str, int] = defaultdict(int)
        skill_last_event: dict[str, float] = {}
        skill_events: list[dict] = []
        vision_samples = []

        while True:
            ok, frame = capture.read()
            if not ok:
                break
            if index % sample_every != 0:
                index += 1
                continue

            video_ts = index / src_fps
            sampled += 1
            gray = self._gray_small(frame)
            motion = 0.0
            if previous_gray is not None:
                motion = float(np.mean(cv2.absdiff(previous_gray, gray)))
                motion_values.append(motion)
            previous_gray = gray

            ammo, ammo_conf = self.ammo.read_ammo(frame)
            if ammo is not None:
                ammo_reads += 1
                ammo_confidences.append(ammo_conf)
                if last_ammo is not None:
                    delta = last_ammo - ammo
                    if 1 <= delta <= 15:
                        per_bullet_gap = min(0.09, max(0.015, (1.0 / self.sample_fps) / max(1, delta)))
                        for n in range(delta):
                            shot_times.append(video_ts - (delta - 1 - n) * per_bullet_gap)
                        if motion > 0:
                            firing_motion.append(motion)
                last_ammo = ammo

            skill_events.extend(self._skill_changes(frame, video_ts, skill_previous, skill_evidence, skill_last_event))
            sample = self.vision.analyze(frame, base_wall + video_ts)
            if sample is not None:
                vision_samples.append(sample)

            if progress and sampled % 5 == 0:
                frac = (index / max(1, total_frames)) if total_frames > 0 else 0.0
                progress(min(0.99, frac), f"{video_ts:.0f}s / {duration:.0f}s")
            index += 1

        capture.release()
        if progress:
            progress(1.0, "분석 완료")

        headline_scores = [float(s.headline_score) for s in vision_samples if s.headline_score is not None]
        headline_errors = [float(s.headline_error_px) for s in vision_samples if s.headline_error_px is not None]
        bursts = self._group_bursts(sorted(shot_times))
        burst_sizes = [len(x) for x in bursts]
        long_bursts = sum(1 for n in burst_sizes if n >= 7)
        skill_by_slot = defaultdict(int)
        for event in skill_events:
            skill_by_slot[event["slot_id"]] += 1

        headline_score = round(statistics.fmean(headline_scores), 1) if headline_scores else None
        headline_error = round(statistics.fmean(headline_errors), 1) if headline_errors else None
        avg_motion = round(statistics.fmean(motion_values), 2) if motion_values else 0.0
        shot_motion = round(statistics.fmean(firing_motion), 2) if firing_motion else None
        avg_burst = round(statistics.fmean(burst_sizes), 1) if burst_sizes else 0.0

        feedback: list[str] = []
        if headline_score is not None:
            if headline_score < 65:
                feedback.append(f"헤드라인 유지가 낮은 편입니다({headline_score}/100, 평균 높이 오차 {headline_error}px). 코너 진입 전 크로스헤어 높이를 먼저 고정해 보세요.")
            elif headline_score < 80:
                feedback.append(f"헤드라인은 보통 수준입니다({headline_score}/100). 적이 보이기 전부터 예상 머리 높이를 더 일정하게 유지하면 좋습니다.")
            else:
                feedback.append(f"헤드라인은 비교적 안정적입니다({headline_score}/100).")
        else:
            feedback.append("적 후보가 충분히 판독되지 않아 헤드라인 점수는 계산하지 않았습니다.")

        if shot_times:
            if avg_burst >= 7 or long_bursts >= max(2, len(bursts) // 3):
                feedback.append(f"탄약 HUD 기준 약 {len(shot_times)}발, {len(bursts)}개 사격 구간이 감지됐고 평균 {avg_burst}발입니다. 긴 스프레이 비중이 높아 보이므로 중거리에서는 짧은 버스트 후 재이동을 연습하세요.")
            else:
                feedback.append(f"탄약 HUD 기준 약 {len(shot_times)}발, {len(bursts)}개 사격 구간이 감지됐고 평균 {avg_burst}발입니다.")
        else:
            feedback.append("탄약 HUD 숫자 감소를 안정적으로 판독하지 못했습니다. HUD 배율/해상도에 따라 탄약 영역 보정이 필요할 수 있습니다.")

        if shot_motion is not None and motion_values:
            baseline_motion = statistics.fmean(motion_values)
            if shot_motion > max(14.0, baseline_motion * 1.35):
                feedback.append(f"사격이 감지된 프레임의 화면 움직임({shot_motion:.1f})이 전체 평균({avg_motion:.1f})보다 큽니다. 플릭 직후 짧은 안정 구간을 만드는 연습이 도움이 됩니다.")

        if skill_events:
            slots = ", ".join(f"{slot} {count}회" for slot, count in sorted(skill_by_slot.items()))
            feedback.append(f"스킬 HUD 변화 후보가 {len(skill_events)}회 감지됐습니다({slots}). 영상만으로 실제 적중/효과까지는 확정하지 않습니다.")
        else:
            feedback.append("지정된 스킬 HUD 영역에서 확실한 사용 변화가 감지되지 않았습니다. 이는 스킬 미사용을 확정하는 판정은 아닙니다.")

        if vision_samples:
            enemy_frames = sum(1 for s in vision_samples if s.enemies > 0)
            max_enemies = max((s.enemies for s in vision_samples), default=0)
            mm_enemy = max((s.minimap_enemies for s in vision_samples), default=0)
            feedback.append(f"샘플 {len(vision_samples)}개 중 적 후보가 보인 프레임은 {enemy_frames}개였고, 한 프레임 최대 적 후보 {max_enemies}, 미니맵 최대 적 후보 {mm_enemy}였습니다.")

        result = {
            "version": "v6.2",
            "video": str(path),
            "filename": path.name,
            "duration_seconds": round(duration, 2),
            "source_fps": round(src_fps, 2),
            "analysis_fps": self.sample_fps,
            "sampled_frames": sampled,
            "estimated_shots": len(shot_times),
            "shot_bursts": len(bursts),
            "average_burst_size": avg_burst,
            "long_bursts": long_bursts,
            "ammo_read_frames": ammo_reads,
            "ammo_mean_confidence": round(statistics.fmean(ammo_confidences), 3) if ammo_confidences else None,
            "skill_hud_changes": len(skill_events),
            "skill_hud_changes_by_slot": dict(skill_by_slot),
            "skill_events": skill_events[:100],
            "headline_score": headline_score,
            "headline_error_px": headline_error,
            "enemy_detection_frames": sum(1 for s in vision_samples if s.enemies > 0),
            "ally_detection_frames": sum(1 for s in vision_samples if s.allies > 0),
            "max_enemies": max((s.enemies for s in vision_samples), default=0),
            "max_allies": max((s.allies for s in vision_samples), default=0),
            "minimap_max_enemies": max((s.minimap_enemies for s in vision_samples), default=0),
            "minimap_max_allies": max((s.minimap_allies for s in vision_samples), default=0),
            "average_camera_motion": avg_motion,
            "firing_camera_motion": shot_motion,
            "feedback": feedback,
            "limitations": [
                "영상 파일에는 실제 Shift/Ctrl/WASD 입력 기록이 없으므로 해당 입력을 추측하지 않습니다.",
                "탄약 감소는 HUD 기반 추정이며 무기 교체 등 일부 장면에서 오탐 가능성이 있습니다.",
                "스킬은 HUD 변화 후보를 감지하며 실제 적중, 공간 확보, 전술적 가치는 확정하지 않습니다.",
                "적/아군 및 헤드라인은 현재 색상/형태 기반 로컬 CV 휴리스틱입니다.",
            ],
        }

        out_dir = self.root_dir / "data" / "imported_video_analysis"
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        out_path = out_dir / f"{path.stem}_{stamp}.json"
        out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        result["result_json"] = str(out_path)
        return result
