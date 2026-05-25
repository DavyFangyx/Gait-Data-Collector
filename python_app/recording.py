from __future__ import annotations

import csv
import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path
from typing import Dict, List, Optional

import av

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None

try:
    import psutil
except ImportError:  # pragma: no cover
    psutil = None

from PyQt5 import QtCore

from .camera_service import CameraController, FramePacket
from .config import AppSettings, CameraBinding, build_session_name
from .constants import CAMERA_SLOTS, DEFAULT_OUTPUT_IDS, SLOT_LABELS, STATE_IDLE, STATE_RECORDING, STATE_SAVING
from .pressure_sensor import PressureFrame

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class BufferUsageByCamera:
    slot: str
    label: str
    output_id: str
    frame_count: int
    estimated_bytes: int
    resolution: str


@dataclass(frozen=True)
class MemorySnapshot:
    process_rss_bytes: int
    system_total_bytes: int
    system_used_bytes: int
    system_available_bytes: int
    system_used_percent: float
    process_percent: float
    buffer_total_bytes: int
    buffer_percent: float
    pressure_buffer_bytes: int
    by_camera: List[BufferUsageByCamera]
    level: str
    note: str


@dataclass
class ActiveRecordingSession:
    started_at: datetime
    epoch_monotonic_us: int
    epoch_wall_utc: datetime
    subject: str
    counter: int
    output_dir: Path
    camera_buffers: Dict[str, List[FramePacket]]
    pressure_buffers: Dict[str, List[PressureFrame]]


def _save_frame_image(path: Path, frame, image_type: str) -> None:
    if cv2 is None:
        raise RuntimeError("缺少 opencv-python，无法保存图片。")
    suffix = image_type.lower().lstrip(".")
    extension = f".{suffix}"
    params = []
    if suffix == "jpg":
        params = [int(cv2.IMWRITE_JPEG_QUALITY), 90]
    ok, encoded = cv2.imencode(extension, frame, params)
    if not ok:
        raise RuntimeError(f"OpenCV 无法编码 {extension} 图片。")
    encoded.tofile(str(path))


def _fractional_rate(value: float) -> Fraction:
    base = max(1, int(round(value * 1000)))
    return Fraction(base, 1000)


def _estimate_fps(frames: List[FramePacket], fallback: float) -> float:
    if len(frames) < 2:
        return max(1.0, fallback)
    elapsed = (frames[-1].captured_at - frames[0].captured_at).total_seconds()
    if elapsed <= 0:
        return max(1.0, fallback)
    return max(1.0, (len(frames) - 1) / elapsed)


def _camera_output_name(binding: CameraBinding) -> str:
    output_id = (binding.output_id or DEFAULT_OUTPUT_IDS.get(binding.slot, binding.slot)).strip()
    return f"cam{output_id}"


def _save_camera_movie(
    binding: CameraBinding,
    frames: List[FramePacket],
    session: ActiveRecordingSession,
    settings: AppSettings,
) -> None:
    if not frames:
        return
    movie_path = session.output_dir / f"{_camera_output_name(binding)}.mp4"
    fps = settings.software_trigger_fps if settings.software_trigger_enabled else _estimate_fps(frames, 30.0)
    container = av.open(str(movie_path), mode="w")
    stream = None
    for codec in ("libx264", "h264"):
        try:
            stream = container.add_stream(codec, rate=_fractional_rate(fps))
            break
        except av.AVError:
            continue
    if stream is None:
        raise RuntimeError("当前环境不支持 H.264 编码。")
    first = frames[0].frame
    stream.width = first.shape[1]
    stream.height = first.shape[0]
    stream.bit_rate = int(settings.movie_rate_kbps) * 1000
    stream.pix_fmt = "yuv420p"
    for packet in frames:
        video_frame = av.VideoFrame.from_ndarray(packet.frame, format="bgr24")
        for encoded in stream.encode(video_frame):
            container.mux(encoded)
    for encoded in stream.encode():
        container.mux(encoded)
    container.close()


def _save_camera_times(
    binding: CameraBinding,
    frames: List[FramePacket],
    session: ActiveRecordingSession,
) -> None:
    output_path = session.output_dir / f"{_camera_output_name(binding)}_times.csv"
    frames = sorted(frames, key=lambda item: (item.monotonic_us, item.captured_at))
    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["frame_idx", "t_us"])
        for frame_idx, packet in enumerate(frames):
            writer.writerow([
                frame_idx,
                max(0, int(packet.monotonic_us) - int(session.epoch_monotonic_us)),
            ])


def _save_pressure_output(session: ActiveRecordingSession, settings: AppSettings) -> None:
    sensor_name_map = {
        "left_foot": "left",
        "right_foot": "right",
    }
    point_headers = [str(index) for index in range(1, 49)]
    for sensor_id, sensor_frames in session.pressure_buffers.items():
        if not sensor_frames:
            continue
        sensor_frames = sorted(sensor_frames, key=lambda item: (item.monotonic_us, item.frame_index))
        output_name = sensor_name_map.get(sensor_id, sensor_id)
        output_path = session.output_dir / f"pressure_{output_name}.csv"
        with output_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["frame_idx", "t_us", *point_headers])
            for local_frame_index, frame in enumerate(sensor_frames):
                raw_values = [int(value) for value in frame.raw_values[:48]]
                if len(raw_values) < 48:
                    raw_values.extend([""] * (48 - len(raw_values)))
                time_us = max(0, int(frame.monotonic_us) - int(session.epoch_monotonic_us))
                writer.writerow([local_frame_index, time_us, *raw_values])


def _save_meta(session: ActiveRecordingSession, settings: AppSettings) -> None:
    bindings = {item.slot: item for item in settings.camera_bindings}
    cameras = []
    for slot, frames in session.camera_buffers.items():
        binding = bindings.get(slot)
        if binding is None:
            continue
        fps = settings.software_trigger_fps if settings.software_trigger_enabled else _estimate_fps(frames, binding.acquisition_frame_rate_fps)
        resolution = ""
        if frames:
            sample = frames[0].frame
            resolution = f"{sample.shape[1]}x{sample.shape[0]}"
        cameras.append({
            "slot": slot,
            "file": f"{_camera_output_name(binding)}.mp4",
            "times_file": f"{_camera_output_name(binding)}_times.csv",
            "output_id": binding.output_id,
            "device_serial": binding.device_serial,
            "fps": fps,
            "frame_count": len(frames),
            "resolution": resolution,
            "exposure_time_us": binding.exposure_time_us,
            "gain_db": binding.gain_db,
            "gamma_value": binding.gamma_value,
            "black_level": binding.black_level,
        })
    pressure = []
    for sensor_id, frames in session.pressure_buffers.items():
        pressure.append({
            "sensor_id": sensor_id,
            "file": f"pressure_{'left' if sensor_id == 'left_foot' else 'right' if sensor_id == 'right_foot' else sensor_id}.csv",
            "frame_count": len(frames),
        })
    payload = {
        "schema_version": 2,
        "started_at_iso": session.started_at.isoformat(timespec="microseconds"),
        "epoch_wall_utc_iso": session.epoch_wall_utc.isoformat(timespec="microseconds"),
        "epoch_monotonic_us": int(session.epoch_monotonic_us),
        "subject": session.subject,
        "counter": session.counter,
        "software_trigger_enabled": settings.software_trigger_enabled,
        "software_trigger_fps": settings.software_trigger_fps,
        "movie_rate_kbps": settings.movie_rate_kbps,
        "timebase": "time.monotonic_ns() // 1000 minus epoch_monotonic_us",
        "cameras": cameras,
        "pressure": pressure,
    }
    with (session.output_dir / "meta.json").open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)


def _estimate_camera_buffer(slot: str, binding: CameraBinding, frames: List[FramePacket]) -> BufferUsageByCamera:
    frame_count = len(frames)
    resolution = "-"
    estimated_bytes = 0
    if frame_count:
        sample = frames[-1].frame
        estimated_bytes = int(sample.nbytes) * frame_count
        resolution = f"{sample.shape[1]}x{sample.shape[0]}"
    return BufferUsageByCamera(
        slot=slot,
        label=SLOT_LABELS.get(slot, slot),
        output_id=binding.output_id or DEFAULT_OUTPUT_IDS.get(slot, slot),
        frame_count=frame_count,
        estimated_bytes=estimated_bytes,
        resolution=resolution,
    )


def _estimate_pressure_buffer(buffers: Dict[str, List[PressureFrame]]) -> int:
    total = 0
    for frames in buffers.values():
        if frames:
            total += len(frames) * frames[-1].estimated_bytes()
    return total


def _resolve_memory_level(total_bytes: int, available_bytes: int, used_percent: float, buffer_total: int) -> str:
    if total_bytes <= 0:
        return "normal"
    available_ratio = available_bytes / total_bytes if total_bytes else 1.0
    if available_bytes <= int(1.5 * 1024**3) or available_ratio <= 0.10:
        return "danger"
    if used_percent >= 82.0 or available_bytes <= int(3 * 1024**3):
        return "warning"
    if available_bytes > 0 and buffer_total >= int(available_bytes * 0.45):
        return "warning"
    return "normal"


class RecordingCoordinator(QtCore.QObject):
    state_changed = QtCore.pyqtSignal(str)
    save_completed = QtCore.pyqtSignal(str)
    save_failed = QtCore.pyqtSignal(str)

    def __init__(self, settings: AppSettings, camera_controller: CameraController) -> None:
        super().__init__()
        self.settings = settings
        self.camera_controller = camera_controller
        self.state = STATE_IDLE
        self._lock = threading.Lock()
        self._session: Optional[ActiveRecordingSession] = None
        self._sampler_thread: Optional[threading.Thread] = None
        self._sampler_stop = threading.Event()
        self._save_thread: Optional[threading.Thread] = None

    def update_settings(self, settings: AppSettings) -> None:
        self.settings = settings

    def is_recording(self) -> bool:
        return self.state == STATE_RECORDING

    def is_busy(self) -> bool:
        return self.state in {STATE_RECORDING, STATE_SAVING}

    def elapsed_seconds(self) -> float:
        session = self._session
        if session is None:
            return 0.0
        return max(0.0, (datetime.now() - session.started_at).total_seconds())

    def begin_recording(self, subject: str, counter: int) -> tuple[bool, str]:
        with self._lock:
            if self.state != STATE_IDLE:
                return False, "当前状态下无法开始录制。"
            bindings = [item for item in self.settings.camera_bindings if item.record_enabled and item.device_serial]
            if not bindings:
                return False, "至少需要选择一个参与录制的机位。"
            if not any(self.camera_controller.statuses().get(item.slot).connected for item in bindings if self.camera_controller.statuses().get(item.slot) is not None):
                return False, "当前没有已连接的工业相机，无法开始录制。"
            ok, message = self.camera_controller.set_recording_mode(
                [item.slot for item in bindings],
                self.settings.software_trigger_enabled,
            )
            if not ok:
                return False, message
            epoch_monotonic_us = time.monotonic_ns() // 1000
            started_at = datetime.now()
            epoch_wall_utc = datetime.now(timezone.utc)
            try:
                output_dir = Path(self.settings.save_dir) / build_session_name(started_at, subject, counter)
                output_dir.mkdir(parents=True, exist_ok=True)
                buffers = {item.slot: [] for item in bindings}
                pressure_buffers = {item.sensor_id: [] for item in self.settings.pressure_sensors if item.enabled}
                self._session = ActiveRecordingSession(
                    started_at=started_at,
                    epoch_monotonic_us=epoch_monotonic_us,
                    epoch_wall_utc=epoch_wall_utc,
                    subject=subject,
                    counter=counter,
                    output_dir=output_dir,
                    camera_buffers=buffers,
                    pressure_buffers=pressure_buffers,
                )
                self.state = STATE_RECORDING
                self.state_changed.emit(self.state)
                if self.settings.software_trigger_enabled:
                    self._start_sampler()
                return True, str(output_dir)
            except Exception as exc:
                self.camera_controller.restore_preview_mode()
                return False, str(exc)

    def append_live_frame(self, slot: str, packet: FramePacket) -> None:
        session = self._session
        if session is None or self.state != STATE_RECORDING or self.settings.software_trigger_enabled:
            return
        if slot not in session.camera_buffers:
            return
        session.camera_buffers[slot].append(packet.clone())

    def append_live_pressure_frame(self, frame: PressureFrame) -> None:
        session = self._session
        if session is None or self.state != STATE_RECORDING:
            return
        if frame.sensor_id not in session.pressure_buffers:
            return
        session.pressure_buffers[frame.sensor_id].append(frame.clone())

    def take_snapshots(self) -> List[Path]:
        targets = [item for item in self.settings.camera_bindings if item.snapshot_enabled]
        packets = self.camera_controller.snapshot_packets([item.slot for item in targets])
        output_dir = Path(self.settings.save_dir) / "image"
        output_dir.mkdir(parents=True, exist_ok=True)
        saved_files = []
        for binding in targets:
            packet = packets.get(binding.slot)
            if packet is None:
                continue
            filename = output_dir / f"{binding.output_id}_{datetime.now():%Y%m%d_%H%M%S}.{self.settings.image_type}"
            _save_frame_image(filename, packet.frame, self.settings.image_type)
            saved_files.append(filename)
        return saved_files

    def append_sync_event(self, direction: str, event: str, payload: str) -> None:
        return

    def memory_snapshot(self) -> MemorySnapshot:
        total_bytes = 0
        used_bytes = 0
        available_bytes = 0
        used_percent = 0.0
        process_rss = 0
        if psutil is not None:
            vm = psutil.virtual_memory()
            total_bytes = int(vm.total)
            used_bytes = int(vm.used)
            available_bytes = int(vm.available)
            used_percent = float(vm.percent)
            process_rss = int(psutil.Process().memory_info().rss)

        binding_map = {item.slot: item for item in self.settings.camera_bindings}
        session = self._session
        camera_items: List[BufferUsageByCamera] = []
        for slot in CAMERA_SLOTS:
            binding = binding_map.get(slot.key, CameraBinding(slot=slot.key, output_id=DEFAULT_OUTPUT_IDS[slot.key]))
            frames = session.camera_buffers.get(slot.key, []) if session is not None else []
            camera_items.append(_estimate_camera_buffer(slot.key, binding, frames))

        camera_total = sum(item.estimated_bytes for item in camera_items)
        pressure_total = _estimate_pressure_buffer(session.pressure_buffers) if session is not None else 0
        buffer_total = camera_total + pressure_total
        level = _resolve_memory_level(total_bytes, available_bytes, used_percent, buffer_total)
        process_percent = (process_rss / total_bytes * 100.0) if total_bytes else 0.0
        buffer_percent = (buffer_total / total_bytes * 100.0) if total_bytes else 0.0
        note = "录制缓存估算包含相机帧与脚压矩阵。" if session is not None else "当前未录制，缓存占用为 0。"
        if psutil is None:
            note = "未安装 psutil，无法获取系统内存数据。"
        return MemorySnapshot(
            process_rss_bytes=process_rss,
            system_total_bytes=total_bytes,
            system_used_bytes=used_bytes,
            system_available_bytes=available_bytes,
            system_used_percent=used_percent,
            process_percent=process_percent,
            buffer_total_bytes=buffer_total,
            buffer_percent=buffer_percent,
            pressure_buffer_bytes=pressure_total,
            by_camera=camera_items,
            level=level,
            note=note,
        )

    def stop_recording(self) -> None:
        with self._lock:
            if self.state != STATE_RECORDING or self._session is None:
                return
            self.state = STATE_SAVING
            self.state_changed.emit(self.state)
        self._stop_sampler()
        self.camera_controller.restore_preview_mode()
        self._save_thread = threading.Thread(target=self._save_worker, daemon=True)
        self._save_thread.start()

    def wait_for_background_tasks(self, timeout: float = 3.0) -> None:
        if self._save_thread is not None:
            self._save_thread.join(timeout=timeout)
        self._stop_sampler()

    def _start_sampler(self) -> None:
        self._sampler_stop.clear()
        self._sampler_thread = threading.Thread(target=self._sampler_loop, daemon=True)
        self._sampler_thread.start()

    def _stop_sampler(self) -> None:
        self._sampler_stop.set()
        if self._sampler_thread is not None:
            self._sampler_thread.join(timeout=1.0)
        self._sampler_thread = None

    def _sampler_loop(self) -> None:
        interval = 1.0 / max(self.settings.software_trigger_fps, 1.0)
        next_tick = time.perf_counter()
        while not self._sampler_stop.is_set():
            session = self._session
            if session is None or self.state != STATE_RECORDING:
                return
            packets = self.camera_controller.trigger_packets(list(session.camera_buffers.keys()))
            for slot, packet in packets.items():
                session.camera_buffers[slot].append(packet)
            next_tick += interval
            time.sleep(max(0.0, next_tick - time.perf_counter()))

    def _save_worker(self) -> None:
        session = self._session
        if session is None:
            return
        try:
            bindings = {item.slot: item for item in self.settings.camera_bindings}
            _save_meta(session, self.settings)
            with ThreadPoolExecutor(max_workers=4) as executor:
                futures = []
                for slot, frames in session.camera_buffers.items():
                    binding = bindings[slot]
                    _save_camera_times(binding, frames, session)
                    futures.append(executor.submit(_save_camera_movie, binding, frames, session, self.settings))
                for future in futures:
                    future.result()
            _save_pressure_output(session, self.settings)
            output_dir = str(session.output_dir)
            self._session = None
            self.state = STATE_IDLE
            self.state_changed.emit(self.state)
            self.save_completed.emit(output_dir)
        except Exception as exc:
            self._session = None
            self.state = STATE_IDLE
            self.state_changed.emit(self.state)
            self.save_failed.emit(str(exc))
