from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Dict, List, Optional

import numpy as np
from PyQt5 import QtCore, QtGui

from .camera_discovery import CameraDescriptor, enumerate_camera_devices
from .config import CameraBinding
from .constants import CAMERA_SLOTS, PREVIEW_INTERVAL_MS, SLOT_LABELS
from .mvs import (
    CameraParameterProfile,
    MvsCameraDevice,
    MvsError,
    finalize_sdk,
    probe_camera_parameter_profile,
    runtime_diagnostics,
)


@dataclass
class FramePacket:
    slot: str
    frame: np.ndarray
    captured_at: datetime
    monotonic_at: float
    monotonic_us: int = 0
    frame_number: int | None = None
    device_timestamp: int | None = None
    backend: str = "mvs"

    def clone(self) -> "FramePacket":
        return FramePacket(
            slot=self.slot,
            frame=self.frame.copy(),
            captured_at=self.captured_at,
            monotonic_at=self.monotonic_at,
            monotonic_us=self.monotonic_us,
            frame_number=self.frame_number,
            device_timestamp=self.device_timestamp,
            backend=self.backend,
        )


@dataclass
class CameraRuntimeStatus:
    slot: str
    label: str
    device_name: str = ""
    serial_number: str = ""
    ip_address: str = ""
    model_name: str = ""
    connected: bool = False
    accessible: bool = False
    fps: float = 0.0
    resolution: str = "-"
    last_error: str = ""
    stream_state: str = "idle"


def frame_to_qimage(frame: np.ndarray) -> QtGui.QImage:
    rgb = frame[:, :, ::-1].copy()
    height, width, channel = rgb.shape
    bytes_per_line = channel * width
    return QtGui.QImage(
        rgb.data,
        width,
        height,
        bytes_per_line,
        QtGui.QImage.Format_RGB888,
    ).copy()


class MvsCameraStream(threading.Thread):
    def __init__(
        self,
        descriptor: CameraDescriptor,
        binding: CameraBinding,
        preview_callback: Callable[[str, QtGui.QImage], None],
        frame_callback: Callable[[str, FramePacket], None],
        status_callback: Callable[[CameraRuntimeStatus], None],
    ) -> None:
        super().__init__(daemon=True)
        self.descriptor = descriptor
        self.binding = binding
        self.preview_callback = preview_callback
        self.frame_callback = frame_callback
        self.status_callback = status_callback
        self.stop_event = threading.Event()
        self.lock = threading.Lock()
        self.mode_lock = threading.Lock()
        self.latest_packet: Optional[FramePacket] = None
        self.device: MvsCameraDevice | None = None
        self.parameter_profile: CameraParameterProfile | None = None
        self.capture_mode = "continuous"
        self.trigger_lock = threading.Lock()
        self.trigger_event: threading.Event | None = None
        self.trigger_packet: FramePacket | None = None
        self.status = CameraRuntimeStatus(
            slot=binding.slot,
            label=SLOT_LABELS[binding.slot],
            device_name=descriptor.display_name,
            serial_number=descriptor.serial_number,
            ip_address=descriptor.ip_address,
            model_name=descriptor.model_name,
            accessible=descriptor.accessible,
        )

    def get_latest_packet(self) -> Optional[FramePacket]:
        with self.lock:
            return self.latest_packet.clone() if self.latest_packet is not None else None

    def get_parameter_profile(self) -> CameraParameterProfile | None:
        with self.lock:
            return self.parameter_profile

    def stop(self) -> None:
        self.stop_event.set()

    def set_capture_mode(self, software_trigger: bool) -> None:
        with self.mode_lock:
            target_mode = "software" if software_trigger else "continuous"
            if target_mode == self.capture_mode:
                return
            if self.device is None:
                self.capture_mode = target_mode
                return
            self.device.set_trigger_mode(software_trigger)
            self.capture_mode = target_mode
            self.status.stream_state = target_mode
            self.status.last_error = ""
            self.status_callback(self.status)

    def issue_software_trigger(self, timeout_s: float = 1.2) -> Optional[FramePacket]:
        if self.device is None:
            return None
        with self.trigger_lock:
            if self.capture_mode != "software":
                self.set_capture_mode(True)
            self.trigger_packet = None
            self.trigger_event = threading.Event()
            try:
                self.device.trigger_once()
            except Exception as exc:
                self.status.last_error = str(exc)
                self.status_callback(self.status)
                self.trigger_event = None
                return None
            event = self.trigger_event

        if event is None or not event.wait(timeout_s):
            self.status.last_error = "软触发超时"
            self.status.stream_state = "software"
            self.status_callback(self.status)
            return None

        with self.trigger_lock:
            packet = self.trigger_packet.clone() if self.trigger_packet is not None else None
            self.trigger_packet = None
            self.trigger_event = None
            return packet

    def run(self) -> None:
        try:
            self.device = MvsCameraDevice(self.descriptor)
            self.device.open(
                exposure_time_us=self.binding.exposure_time_us,
                acquisition_frame_rate_fps=self.binding.acquisition_frame_rate_fps,
                gain_db=self.binding.gain_db,
                gamma_value=self.binding.gamma_value,
                black_level=self.binding.black_level,
            )
            self.parameter_profile = self.device.parameter_profile()
        except Exception as exc:
            self.status.last_error = str(exc)
            self.status.connected = False
            self.status.stream_state = "error"
            self.status_callback(self.status)
            return

        frame_times: list[float] = []
        last_preview_time = 0.0
        self.status.connected = True
        self.status.accessible = True
        self.status.last_error = ""
        self.status.stream_state = "continuous"
        self.status_callback(self.status)

        while not self.stop_event.is_set():
            try:
                result = self.device.read_frame(timeout_ms=200)
                if result is None:
                    continue
                frame, meta = result
            except MvsError as exc:
                if "0x80000007" in str(exc) and self.capture_mode == "software":
                    continue
                self.status.connected = False
                self.status.last_error = str(exc)
                self.status.stream_state = "reconnecting"
                self.status_callback(self.status)
                if not self._attempt_restart():
                    break
                frame_times.clear()
                continue
            except Exception as exc:  # pragma: no cover - 依赖设备现场
                self.status.connected = False
                self.status.last_error = str(exc)
                self.status.stream_state = "reconnecting"
                self.status_callback(self.status)
                if not self._attempt_restart():
                    break
                frame_times.clear()
                continue

            monotonic_us = time.monotonic_ns() // 1000
            monotonic_at = monotonic_us / 1_000_000.0
            captured_at = datetime.now()
            packet = FramePacket(
                slot=self.binding.slot,
                frame=frame,
                captured_at=captured_at,
                monotonic_at=monotonic_at,
                monotonic_us=monotonic_us,
                frame_number=meta.get("frame_number"),
                device_timestamp=meta.get("device_timestamp"),
            )
            with self.lock:
                self.latest_packet = packet

            frame_times.append(monotonic_at)
            if len(frame_times) > 30:
                frame_times.pop(0)
            if len(frame_times) >= 2:
                elapsed = frame_times[-1] - frame_times[0]
                if elapsed > 0:
                    self.status.fps = (len(frame_times) - 1) / elapsed
            self.status.resolution = f"{meta.get('width', frame.shape[1])}x{meta.get('height', frame.shape[0])}"
            self.status.connected = True
            self.status.last_error = ""
            self.status.stream_state = self.capture_mode
            self.status_callback(self.status)

            self.frame_callback(self.binding.slot, packet)
            if (monotonic_at - last_preview_time) * 1000 >= PREVIEW_INTERVAL_MS:
                self.preview_callback(self.binding.slot, frame_to_qimage(frame))
                last_preview_time = monotonic_at

            with self.trigger_lock:
                if self.trigger_event is not None:
                    self.trigger_packet = packet.clone()
                    self.trigger_event.set()
                    self.trigger_event = None

        if self.device is not None:
            self.device.close()
        self.status.connected = False
        self.status.stream_state = "stopped"
        self.status_callback(self.status)

    def _attempt_restart(self) -> bool:
        if self.device is None:
            return False
        for _ in range(5):
            if self.stop_event.wait(0.4):
                return False
            try:
                self.device.restart()
                self.device.set_trigger_mode(self.capture_mode == "software")
                self.parameter_profile = self.device.parameter_profile()
                self.status.connected = True
                self.status.last_error = ""
                self.status.stream_state = self.capture_mode
                self.status_callback(self.status)
                return True
            except Exception as exc:
                self.status.last_error = f"重连失败：{exc}"
                self.status_callback(self.status)
        return False


class CameraController(QtCore.QObject):
    preview_updated = QtCore.pyqtSignal(str, object)
    status_updated = QtCore.pyqtSignal(str, object)
    devices_changed = QtCore.pyqtSignal(object)

    def __init__(self) -> None:
        super().__init__()
        self.descriptors: List[CameraDescriptor] = []
        self.streams: Dict[str, MvsCameraStream] = {}
        self.status_map: Dict[str, CameraRuntimeStatus] = {
            slot.key: CameraRuntimeStatus(slot=slot.key, label=slot.label)
            for slot in CAMERA_SLOTS
        }
        self.frame_consumer: Optional[Callable[[str, FramePacket], None]] = None
        self._backend_message = ""

    def set_frame_consumer(self, consumer: Callable[[str, FramePacket], None]) -> None:
        self.frame_consumer = consumer

    def backend_warning(self) -> str:
        return self._backend_message

    def set_binding_hints(self, bindings: List[CameraBinding]) -> list[str]:
        migrated: list[str] = []
        if not self.descriptors:
            return migrated
        name_map = {item.display_name: item for item in self.descriptors}
        legacy_map = {item.user_defined_name: item for item in self.descriptors if item.user_defined_name}
        for binding in bindings:
            if binding.device_serial or not binding.device_name:
                continue
            descriptor = name_map.get(binding.device_name) or legacy_map.get(binding.device_name)
            if descriptor is None:
                continue
            binding.device_serial = descriptor.serial_number
            binding.device_name = ""
            migrated.append(f"{SLOT_LABELS[binding.slot]} 号机位已根据旧设备名迁移到序列号 {descriptor.serial_number}")
        return migrated

    def refresh_devices(self) -> List[CameraDescriptor]:
        diagnostics = runtime_diagnostics()
        if not diagnostics.available:
            self._backend_message = diagnostics.message
            self.descriptors = []
            for slot in CAMERA_SLOTS:
                status = self.status_map[slot.key]
                status.connected = False
                status.last_error = diagnostics.message
            self.devices_changed.emit(self.descriptors)
            return self.descriptors

        try:
            self.descriptors = enumerate_camera_devices()
            self._backend_message = "" if self.descriptors or diagnostics.available else diagnostics.message
        except Exception as exc:
            self.descriptors = []
            self._backend_message = str(exc)
        self.devices_changed.emit(self.descriptors)
        return self.descriptors

    def start(self, bindings: List[CameraBinding]) -> None:
        self.stop()
        if self._backend_message:
            for binding in bindings:
                status = self.status_map[binding.slot]
                status.device_name = ""
                status.connected = False
                status.last_error = self._backend_message
                self.status_updated.emit(binding.slot, status)
            return

        descriptor_map = {item.serial_number: item for item in self.descriptors}
        for binding in bindings:
            status = self.status_map[binding.slot]
            status.device_name = ""
            status.serial_number = binding.device_serial
            status.last_error = ""

            if not binding.device_serial:
                status.connected = False
                status.last_error = "未绑定设备"
                self.status_updated.emit(binding.slot, status)
                continue

            descriptor = descriptor_map.get(binding.device_serial)
            if descriptor is None:
                status.connected = False
                status.last_error = "未找到对应序列号设备"
                self.status_updated.emit(binding.slot, status)
                continue
            if not descriptor.accessible:
                status.connected = False
                status.device_name = descriptor.display_name
                status.model_name = descriptor.model_name
                status.ip_address = descriptor.ip_address
                status.serial_number = descriptor.serial_number
                status.accessible = False
                status.last_error = "设备被占用"
                self.status_updated.emit(binding.slot, status)
                continue

            stream = MvsCameraStream(
                descriptor=descriptor,
                binding=binding,
                preview_callback=self._emit_preview,
                frame_callback=self._consume_frame,
                status_callback=self._update_status,
            )
            self.streams[binding.slot] = stream
            stream.start()

    def stop(self) -> None:
        for stream in self.streams.values():
            stream.stop()
        for stream in self.streams.values():
            stream.join(timeout=2.0)
        self.streams.clear()

    def shutdown(self) -> None:
        self.stop()
        finalize_sdk()

    def latest_packet(self, slot: str) -> Optional[FramePacket]:
        stream = self.streams.get(slot)
        return stream.get_latest_packet() if stream else None

    def camera_profiles(self) -> Dict[str, CameraParameterProfile]:
        profiles: Dict[str, CameraParameterProfile] = {}
        for stream in self.streams.values():
            profile = stream.get_parameter_profile()
            if profile is not None:
                profiles[profile.serial_number] = profile
        return profiles

    def probe_camera_profile(self, serial_number: str) -> CameraParameterProfile | None:
        if not serial_number:
            return None
        live_profile = self.camera_profiles().get(serial_number)
        if live_profile is not None:
            return live_profile
        descriptor = next((item for item in self.descriptors if item.serial_number == serial_number), None)
        if descriptor is None:
            return None
        return probe_camera_parameter_profile(descriptor)

    def snapshot_packets(self, slots: List[str]) -> Dict[str, FramePacket]:
        packets = {}
        for slot in slots:
            packet = self.latest_packet(slot)
            if packet is not None:
                packets[slot] = packet
        return packets

    def trigger_packets(self, slots: List[str]) -> Dict[str, FramePacket]:
        packets: Dict[str, FramePacket] = {}
        for slot in slots:
            stream = self.streams.get(slot)
            if stream is None:
                continue
            packet = stream.issue_software_trigger()
            if packet is not None:
                packets[slot] = packet
        return packets

    def set_recording_mode(self, slots: List[str], use_software_trigger: bool) -> tuple[bool, str]:
        if self._backend_message:
            return False, self._backend_message
        if not use_software_trigger:
            for slot in slots:
                stream = self.streams.get(slot)
                if stream is None:
                    continue
                try:
                    stream.set_capture_mode(False)
                except Exception as exc:
                    return False, str(exc)
            return True, ""

        online_slots = [slot for slot in slots if slot in self.streams]
        if not online_slots:
            return False, "未找到已连接的工业相机，无法进入软触发录制。"
        for slot in online_slots:
            try:
                self.streams[slot].set_capture_mode(True)
            except Exception as exc:
                return False, str(exc)
        return True, ""

    def restore_preview_mode(self) -> None:
        for stream in self.streams.values():
            try:
                stream.set_capture_mode(False)
            except Exception:
                continue

    def statuses(self) -> Dict[str, CameraRuntimeStatus]:
        return dict(self.status_map)

    def _emit_preview(self, slot: str, image: QtGui.QImage) -> None:
        self.preview_updated.emit(slot, image)

    def _consume_frame(self, slot: str, packet: FramePacket) -> None:
        if self.frame_consumer is not None:
            self.frame_consumer(slot, packet)

    def _update_status(self, status: CameraRuntimeStatus) -> None:
        self.status_map[status.slot] = status
        self.status_updated.emit(status.slot, status)
