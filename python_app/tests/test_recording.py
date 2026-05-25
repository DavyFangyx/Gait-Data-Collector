import csv
import json
import shutil
import sys
import tempfile
import types
import unittest
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

if "PyQt5" not in sys.modules:
    qtcore_module = types.ModuleType("PyQt5.QtCore")
    qtgui_module = types.ModuleType("PyQt5.QtGui")
    pyqt5_module = types.ModuleType("PyQt5")

    class _DummyQObject:
        pass

    class _DummySignal:
        def __init__(self, *args, **kwargs):
            pass

        def emit(self, *args, **kwargs):
            pass

    class _DummyQImage:
        Format_RGB888 = 0

        def __init__(self, *args, **kwargs):
            pass

        def copy(self):
            return self

    qtcore_module.QObject = _DummyQObject
    qtcore_module.pyqtSignal = lambda *args, **kwargs: _DummySignal()
    qtgui_module.QImage = _DummyQImage
    pyqt5_module.QtCore = qtcore_module
    pyqt5_module.QtGui = qtgui_module
    sys.modules["PyQt5"] = pyqt5_module
    sys.modules["PyQt5.QtCore"] = qtcore_module
    sys.modules["PyQt5.QtGui"] = qtgui_module

from python_app.camera_service import FramePacket
from python_app.config import AppSettings, CameraBinding
from python_app.pressure_sensor import PressureFrame
from python_app.recording import (
    ActiveRecordingSession,
    _save_camera_times,
    _save_meta,
    _save_pressure_output,
)

_MONOTONIC_BASE = datetime(2026, 4, 13, 12, 0, 0)


def _make_camera_packet(slot: str, captured_at: datetime, frame_number: int) -> FramePacket:
    monotonic_us = int((captured_at - _MONOTONIC_BASE).total_seconds() * 1_000_000)
    return FramePacket(
        slot=slot,
        frame=np.zeros((4, 4, 3), dtype=np.uint8),
        captured_at=captured_at,
        monotonic_at=monotonic_us / 1_000_000.0,
        monotonic_us=monotonic_us,
        frame_number=frame_number,
    )


def _make_pressure_frame(sensor_id: str, captured_at: datetime, frame_index: int) -> PressureFrame:
    raw_values = np.arange(1, 49, dtype=np.uint16)
    monotonic_us = int((captured_at - _MONOTONIC_BASE).total_seconds() * 1_000_000)
    return PressureFrame(
        sensor_id=sensor_id,
        label=sensor_id,
        frame_index=frame_index,
        captured_at=captured_at,
        monotonic_us=monotonic_us,
        raw_values=raw_values,
        display_values=raw_values.astype(np.float32),
    )


class RecordingTests(unittest.TestCase):
    def test_save_camera_times_writes_one_file_per_camera(self):
        base_time = datetime(2026, 4, 13, 12, 0, 0)
        settings = AppSettings(
            camera_bindings=[
                CameraBinding(slot="top", device_serial="MV-0001", output_id="1", acquisition_frame_rate_fps=30.0),
                CameraBinding(slot="left", device_serial="MV-0002", output_id="2", acquisition_frame_rate_fps=30.0),
            ],
        )
        temp_dir = Path(__file__).resolve().parent / ".tmp" / "camera_times_case"
        if temp_dir.exists():
            shutil.rmtree(temp_dir)
        temp_dir.mkdir(parents=True, exist_ok=True)
        session = ActiveRecordingSession(
            started_at=base_time,
            epoch_monotonic_us=0,
            epoch_wall_utc=base_time,
            subject="测试",
            counter=1,
            output_dir=temp_dir,
            camera_buffers={
                "top": [
                    _make_camera_packet("top", base_time + timedelta(milliseconds=10), 0),
                    _make_camera_packet("top", base_time + timedelta(milliseconds=20), 1),
                ],
                "left": [
                    _make_camera_packet("left", base_time + timedelta(milliseconds=11), 0),
                ],
            },
            pressure_buffers={},
        )

        _save_camera_times(settings.camera_bindings[0], session.camera_buffers["top"], session)

        with (temp_dir / "cam1_times.csv").open("r", encoding="utf-8", newline="") as handle:
            rows = list(csv.reader(handle))

        self.assertEqual(rows[0], ["frame_idx", "t_us"])
        self.assertEqual(rows[1], ["0", "10000"])
        self.assertEqual(rows[2], ["1", "20000"])

        shutil.rmtree(temp_dir)

    def test_save_pressure_output_writes_unified_schema(self):
        base_time = datetime(2026, 4, 13, 12, 0, 0)
        settings = AppSettings()

        temp_dir = Path(__file__).resolve().parent / ".tmp" / "pressure_output_case"
        if temp_dir.exists():
            shutil.rmtree(temp_dir)
        temp_dir.mkdir(parents=True, exist_ok=True)
        session = ActiveRecordingSession(
            started_at=base_time,
            epoch_monotonic_us=0,
            epoch_wall_utc=base_time,
            subject="测试",
            counter=1,
            output_dir=temp_dir,
            camera_buffers={},
            pressure_buffers={
                "left_foot": [
                    _make_pressure_frame("left_foot", base_time + timedelta(milliseconds=50), 0),
                    _make_pressure_frame("left_foot", base_time + timedelta(milliseconds=310), 1),
                ],
                "right_foot": [
                    _make_pressure_frame("right_foot", base_time - timedelta(milliseconds=1), 0),
                ],
            },
        )

        _save_pressure_output(session, settings)

        left_path = temp_dir / "pressure_left.csv"
        right_path = temp_dir / "pressure_right.csv"

        with left_path.open("r", encoding="utf-8", newline="") as handle:
            left_rows = list(csv.reader(handle))
        with right_path.open("r", encoding="utf-8", newline="") as handle:
            right_rows = list(csv.reader(handle))

        self.assertEqual(left_rows[0][:3], ["frame_idx", "t_us", "1"])
        self.assertEqual(left_rows[1][:2], ["0", "50000"])
        self.assertEqual(left_rows[2][:2], ["1", "310000"])
        self.assertEqual(right_rows[1][:2], ["0", "0"])

        shutil.rmtree(temp_dir)

    def test_save_meta_writes_recording_manifest(self):
        base_time = datetime(2026, 4, 13, 12, 0, 0)
        settings = AppSettings(
            camera_bindings=[
                CameraBinding(slot="top", device_serial="MV-0001", output_id="1", acquisition_frame_rate_fps=30.0),
            ],
        )
        temp_dir = Path(__file__).resolve().parent / ".tmp" / "meta_case"
        if temp_dir.exists():
            shutil.rmtree(temp_dir)
        temp_dir.mkdir(parents=True, exist_ok=True)
        session = ActiveRecordingSession(
            started_at=base_time,
            epoch_monotonic_us=0,
            epoch_wall_utc=base_time,
            subject="subject1",
            counter=1,
            output_dir=temp_dir,
            camera_buffers={
                "top": [
                    _make_camera_packet("top", base_time + timedelta(milliseconds=10), 0),
                ],
            },
            pressure_buffers={"left_foot": []},
        )

        _save_meta(session, settings)

        with (temp_dir / "meta.json").open("r", encoding="utf-8") as handle:
            payload = json.load(handle)

        self.assertEqual(payload["schema_version"], 2)
        self.assertEqual(payload["cameras"][0]["file"], "cam1.mp4")
        self.assertEqual(payload["cameras"][0]["times_file"], "cam1_times.csv")
        self.assertEqual(payload["timebase"], "time.monotonic_ns() // 1000 minus epoch_monotonic_us")

        shutil.rmtree(temp_dir)


if __name__ == "__main__":
    unittest.main()
