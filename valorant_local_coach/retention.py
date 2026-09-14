from __future__ import annotations

import json
import time
from pathlib import Path

VIDEO_SUFFIXES = {'.mp4', '.avi', '.mov', '.mkv', '.webm'}


def cleanup_old_videos(root_dir: Path, retention_hours: float = 24.0) -> dict:
    """Delete only recorded video files older than retention_hours.

    JSON metadata and still images are intentionally preserved for coaching history.
    If a sibling JSON points at a deleted video, its ``video`` field is set to null.
    """
    clip_root = Path(root_dir) / 'data' / 'fight_clips'
    clip_root.mkdir(parents=True, exist_ok=True)
    cutoff = time.time() - max(1.0, float(retention_hours)) * 3600.0
    deleted = 0
    bytes_freed = 0
    metadata_updated = 0

    for path in clip_root.rglob('*'):
        if not path.is_file() or path.suffix.lower() not in VIDEO_SUFFIXES:
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        if stat.st_mtime > cutoff:
            continue
        try:
            size = stat.st_size
            path.unlink()
            deleted += 1
            bytes_freed += size
        except OSError:
            continue

        meta_path = path.with_suffix('.json')
        if meta_path.exists():
            try:
                data = json.loads(meta_path.read_text(encoding='utf-8'))
                if isinstance(data, dict) and data.get('video'):
                    data['video'] = None
                    data['video_deleted_after_hours'] = max(1.0, float(retention_hours))
                    data['video_deleted_at'] = time.time()
                    meta_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
                    metadata_updated += 1
            except Exception:
                pass

    for directory in sorted((p for p in clip_root.rglob('*') if p.is_dir()), reverse=True):
        try:
            if not any(directory.iterdir()):
                directory.rmdir()
        except OSError:
            pass

    return {
        'deleted_videos': deleted,
        'bytes_freed': bytes_freed,
        'metadata_updated': metadata_updated,
        'retention_hours': max(1.0, float(retention_hours)),
    }
