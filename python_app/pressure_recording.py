from __future__ import annotations

import csv
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, TextIO

from .config import AppSettings, build_session_name
from .pressure_sensor import PressureFrame


class PressureOnlyRecorder:
    """Record pressure frames using the serial receive timestamp as the time base."""

    def __init__(self, settings: AppSettings) -> None:
        self.settings = settings
        self.is_recording = False
        self.output_dir: Path | None = None
        self.started_at: datetime | None = None
        self._epoch_monotonic_us: int | None = None
        self._files: Dict[str, TextIO] = {}
        self._writers: Dict[str, csv.writer] = {}
        self._counts: Dict[str, int] = {}

    def update_settings(self, settings: AppSettings) -> None:
        self.settings = settings

    def begin_recording(self, subject: str, counter: int) -> tuple[bool, str]:
        if self.is_recording:
            return False, "触觉记录正在进行中。"
        enabled = [item for item in self.settings.pressure_sensors if item.enabled]
        if not enabled:
            return False, "至少需要启用一个触觉传感器。"

        self.started_at = datetime.now()
        self._epoch_monotonic_us = time.monotonic_ns() // 1000
        self.output_dir = Path(self.settings.save_dir) / build_session_name(
            self.started_at,
            f"{subject}_仅触觉",
            counter,
        )
        try:
            self.output_dir.mkdir(parents=True, exist_ok=True)
            self._files = {}
            self._writers = {}
            self._counts = {}
            self._write_meta(subject, counter)
            self.is_recording = True
            return True, str(self.output_dir)
        except Exception as exc:
            self._close_files()
            return False, str(exc)

    def append_frame(self, frame: PressureFrame) -> None:
        if not self.is_recording or self.output_dir is None or self.started_at is None:
            return
        writer = self._writer_for(frame)
        local_index = self._counts.get(frame.sensor_id, 0)
        time_us = max(0, int(frame.monotonic_us) - int(self._epoch_monotonic_us))
        raw_values = [int(value) for value in frame.raw_values[:48]]
        if len(raw_values) < 48:
            raw_values.extend([""] * (48 - len(raw_values)))
        writer.writerow([
            local_index,
            time_us,
            *raw_values,
        ])
        self._counts[frame.sensor_id] = local_index + 1
        handle = self._files.get(frame.sensor_id)
        if handle is not None and local_index % 50 == 0:
            handle.flush()

    def stop_recording(self) -> tuple[int, str]:
        if not self.is_recording:
            return 0, ""
        total = sum(self._counts.values())
        output_dir = str(self.output_dir or "")
        self.is_recording = False
        self._close_files()
        self.output_dir = None
        self.started_at = None
        self._epoch_monotonic_us = None
        return total, output_dir

    def _writer_for(self, frame: PressureFrame) -> csv.writer:
        writer = self._writers.get(frame.sensor_id)
        if writer is not None:
            return writer
        if self.output_dir is None or self.started_at is None:
            raise RuntimeError("触觉记录未开始。")
        name_map = {"left_foot": "left", "right_foot": "right"}
        output_name = name_map.get(frame.sensor_id, frame.sensor_id)
        path = self.output_dir / f"pressure_{output_name}.csv"
        handle = path.open("w", encoding="utf-8", newline="")
        writer = csv.writer(handle)
        writer.writerow(["frame_idx", "t_us", *[str(index) for index in range(1, 49)]])
        self._files[frame.sensor_id] = handle
        self._writers[frame.sensor_id] = writer
        self._counts[frame.sensor_id] = 0
        return writer

    def _write_meta(self, subject: str, counter: int) -> None:
        if self.output_dir is None or self.started_at is None or self._epoch_monotonic_us is None:
            return
        payload = {
            "schema_version": 2,
            "mode": "pressure_only",
            "started_at_iso": self.started_at.isoformat(timespec="microseconds"),
            "epoch_monotonic_us": int(self._epoch_monotonic_us),
            "subject": subject,
            "counter": counter,
            "timebase": "time.monotonic_ns() // 1000 minus epoch_monotonic_us",
            "pressure": [
                {
                    "sensor_id": item.sensor_id,
                    "file": f"pressure_{'left' if item.sensor_id == 'left_foot' else 'right' if item.sensor_id == 'right_foot' else item.sensor_id}.csv",
                    "enabled": item.enabled,
                    "port": item.port,
                }
                for item in self.settings.pressure_sensors
            ],
        }
        with (self.output_dir / "meta.json").open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)

    def _close_files(self) -> None:
        for handle in self._files.values():
            try:
                handle.flush()
                handle.close()
            except Exception:
                pass
        self._files = {}
        self._writers = {}
