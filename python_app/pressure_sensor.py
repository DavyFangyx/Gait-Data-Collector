"""
pressure_sensor.py  （重构版）

帧格式（99 字节）：
    B1      : 帧头 0x40
    B2      : 脚编号 0x31（左脚）/ 0x32（右脚）
    B3-B98  : 48 个测量点，每点 2 字节大端序 uint16（高字节在前）
    B99     : 校验位 = sum(B1..B98) & 0xFF
"""

from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Dict, List, Optional

import numpy as np
from PyQt5 import QtCore

try:
    import serial
    from serial.tools import list_ports
except ImportError:  # pragma: no cover
    serial = None
    list_ports = None

from .config import PressureSensorSettings
from .constants import PRESSURE_SENSOR_LABELS, PRESSURE_SENSORS

# ---------------------------------------------------------------------------
# 协议常量
# ---------------------------------------------------------------------------
FRAME_HEADER: bytes = bytes([0x40])
FRAME_HEADER_LEN: int = 1
FOOT_ID_LEN: int = 1
FOOT_ID_LEFT: int = 0x31          # 左脚编号
FOOT_ID_RIGHT: int = 0x32         # 右脚编号
BAUD_RATE: int = 115200           # 固定波特率（协议常量，不可配置）
FOOT_ID_SENSOR_MAP: dict = {FOOT_ID_LEFT: "left_foot", FOOT_ID_RIGHT: "right_foot"}
SENSOR_ID_FOOT_MAP: dict = {"left_foot": FOOT_ID_LEFT, "right_foot": FOOT_ID_RIGHT}
PACKED_DATA_LEN: int = 96       # 48 × 2 字节
SENSOR_COUNT: int = 48          # 每帧测量点数
FRAME_LEN: int = 99             # 1 + 1 + 96 + 1


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

def available_serial_ports() -> List[str]:
    if list_ports is None:
        return []
    return [item.device for item in list_ports.comports()]


def sum_checksum(data: bytes | bytearray) -> int:
    """对 data 中所有字节求和，返回低 8 位（0–255）。"""
    return sum(data) & 0xFF


def verify_checksum(frame_bytes: bytes) -> bool:
    """
    验证完整帧的校验位。
    帧结构：[header(1)] [foot_id(1)] [data(96)] [checksum(1)]
    校验字节 = sum(B1..B98) & 0xFF（前 98 字节之和取低 8 位）
    """
    if len(frame_bytes) < FRAME_LEN:
        return False
    expected = sum_checksum(frame_bytes[:FRAME_LEN - 1])
    actual = frame_bytes[FRAME_LEN - 1]
    return expected == actual


def unpack_16bit(data: bytes | memoryview) -> np.ndarray:
    """
    将 96 字节数据解包为 48 个 uint16 值（大端序）。
    每个测量点占 2 字节：高字节在前，低字节在后。

    返回：shape=(48,) 的 np.ndarray, dtype=uint16
    """
    data = bytes(data)
    if len(data) != PACKED_DATA_LEN:
        raise ValueError(f"数据长度应为 {PACKED_DATA_LEN}，实际为 {len(data)}")
    return np.frombuffer(data, dtype='>u2').astype(np.uint16)


# ---------------------------------------------------------------------------
# 显示值转换（预留钩子）
# ---------------------------------------------------------------------------

def raw_to_display_value(raw: np.ndarray) -> np.ndarray:
    """
    将原始 ADC 值转换为最终显示值。
    当前为直通（display == raw），后续在此处添加标定/映射逻辑。

    参数：
        raw  — shape 任意, dtype uint16 的原始值数组
    返回：
        与输入形状相同的 float32 数组
    """
    # ── 在此添加转换逻辑，例如：
    #   return raw.astype(np.float32) / 4095.0 * MAX_PRESSURE_KPA
    return raw.astype(np.float32)


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PressureFrame:
    sensor_id: str
    label: str
    frame_index: int
    captured_at: datetime
    monotonic_us: int

    # 原始 ADC 值，shape=(48,), dtype=uint16
    raw_values: np.ndarray

    # 经 raw_to_display_value() 转换后的显示值，shape=(48,), dtype=float32
    display_values: np.ndarray

    def clone(self) -> "PressureFrame":
        return PressureFrame(
            sensor_id=self.sensor_id,
            label=self.label,
            frame_index=self.frame_index,
            captured_at=self.captured_at,
            monotonic_us=self.monotonic_us,
            raw_values=self.raw_values.copy(),
            display_values=self.display_values.copy(),
        )

    def estimated_bytes(self) -> int:
        return int(self.raw_values.nbytes + self.display_values.nbytes)


@dataclass(frozen=True)
class PressureSnapshot:
    sensor_id: str
    label: str
    connected: bool = False
    port: str = ""
    last_frame_index: int = -1
    last_received_at: datetime | None = None
    message: str = "未连接"

    # 最新一帧的原始值（可为 None）
    raw_values: np.ndarray | None = None
    # 最新一帧的显示值（可为 None）
    display_values: np.ndarray | None = None


@dataclass(frozen=True)
class PressureParseStats:
    sensor_id: str
    label: str
    received_frames: int = 0
    parsed_frames: int = 0
    failed_frames: int = 0
    last_error: str = ""


# ---------------------------------------------------------------------------
# 帧解析
# ---------------------------------------------------------------------------

def parse_pressure_frame(
    settings: PressureSensorSettings,
    frame_bytes: bytes,
    frame_index: int,
    arrival_monotonic_us: int,
) -> PressureFrame:
    """
    解析一帧完整数据。

    帧结构（99 字节）：
        B1      : 帧头 0x40
        B2      : 脚编号 0x31（左） / 0x32（右）
        B3-B98  : 48 个测量点，每点 2 字节大端序 uint16
        B99     : 校验位 = sum(B1..B98) & 0xFF

    参数：
        settings    — 传感器配置
        frame_bytes — 完整的 99 字节帧
        frame_index — 帧序号
    """
    if len(frame_bytes) < FRAME_LEN:
        raise ValueError(f"帧长度 {len(frame_bytes)} < {FRAME_LEN}，无法解析。")

    # 1. 校验帧头
    if frame_bytes[0:1] != FRAME_HEADER:
        raise ValueError(f"帧头错误：0x{frame_bytes[0]:02X}")

    # 2. 脚编号验证
    foot_id = frame_bytes[1]
    expected_foot_id = SENSOR_ID_FOOT_MAP.get(settings.sensor_id)
    if expected_foot_id is not None and foot_id != expected_foot_id:
        raise ValueError(
            f"脚编号不匹配：期望 0x{expected_foot_id:02X}，实际 0x{foot_id:02X}"
        )

    # 3. 校验和验证
    if not verify_checksum(frame_bytes):
        raise ValueError("校验和验证失败。")

    # 4. 解包 16-bit 数据
    data_start = FRAME_HEADER_LEN + FOOT_ID_LEN
    raw_values = unpack_16bit(frame_bytes[data_start: data_start + PACKED_DATA_LEN])  # shape=(48,), uint16

    # 5. 转换为显示值
    display_values = raw_to_display_value(raw_values)

    return PressureFrame(
        sensor_id=settings.sensor_id,
        label=settings.label,
        frame_index=frame_index,
        captured_at=datetime.now(),
        monotonic_us=arrival_monotonic_us,
        raw_values=raw_values,
        display_values=display_values,
    )


# ---------------------------------------------------------------------------
# 帧缓冲消费
# ---------------------------------------------------------------------------

def _frame_prefix_for_sensor(sensor_id: str) -> bytes:
    """
    返回该传感器对应的帧同步前缀。

    当前协议里每个串口只连接一只脚，因此用 `帧头 + 脚编号`
    作为同步前缀比只匹配单字节帧头更稳健，能避免数据区出现
    `0x40` 时误切帧。
    """
    foot_id = SENSOR_ID_FOOT_MAP.get(sensor_id)
    if foot_id is None:
        return FRAME_HEADER
    return FRAME_HEADER + bytes([foot_id])


def _consume_frames(buffer: bytearray, frame_size: int, prefix: bytes) -> List[bytes]:
    """从 buffer 中提取完整帧，按指定同步前缀对齐。"""
    frames: List[bytes] = []
    prefix_len = len(prefix)

    while True:
        index = buffer.find(prefix)
        if index < 0:
            keep = min(len(buffer), max(0, prefix_len - 1))
            if keep:
                del buffer[:-keep]
            else:
                buffer.clear()
            break
        if index > 0:
            del buffer[:index]
        if len(buffer) < frame_size:
            break
        frames.append(bytes(buffer[:frame_size]))
        del buffer[:frame_size]
    return frames


# ---------------------------------------------------------------------------
# 后台串口工作线程
# ---------------------------------------------------------------------------

class PressureSensorWorker(threading.Thread):
    def __init__(
        self,
        settings: PressureSensorSettings,
        raw_frame_callback: Callable[[PressureSensorSettings, bytes, int], None],
        status_callback: Callable[[str, bool, str], None],
        log_callback: Callable[[str], None],
    ) -> None:
        super().__init__(daemon=True)
        self.settings = settings
        self.raw_frame_callback = raw_frame_callback
        self.status_callback = status_callback
        self.log_callback = log_callback
        self.stop_event = threading.Event()
        self._serial = None

    def stop(self) -> None:
        self.stop_event.set()

    def run(self) -> None:
        label = self.settings.label
        frame_prefix = _frame_prefix_for_sensor(self.settings.sensor_id)
        if serial is None:
            self.status_callback(self.settings.sensor_id, False, f"{label} 缺少 pyserial")
            return
        if not self.settings.enabled:
            self.status_callback(self.settings.sensor_id, False, f"{label} 未启用")
            return
        if not self.settings.port:
            self.status_callback(self.settings.sensor_id, False, f"{label} 未配置端口")
            return
        try:
            self._serial = serial.Serial(
                port=self.settings.port,
                baudrate=BAUD_RATE,
                timeout=0.2,
                write_timeout=0.5,
            )
        except Exception as exc:
            self.status_callback(self.settings.sensor_id, False, f"{label} 连接失败：{exc}")
            return

        self.status_callback(
            self.settings.sensor_id, True,
            f"{label} 已连接 {self.settings.port}"
        )
        buffer = bytearray()
        while not self.stop_event.is_set():
            try:
                chunk = self._serial.read(max(FRAME_LEN * 2, 128))
                arrival_monotonic_us = time.monotonic_ns() // 1000
            except Exception as exc:
                self.status_callback(self.settings.sensor_id, False, f"{label} 读取失败：{exc}")
                break
            if not chunk:
                continue
            buffer.extend(chunk)
            frames = _consume_frames(buffer, FRAME_LEN, frame_prefix)
            for raw_frame in frames:
                self.raw_frame_callback(self.settings, raw_frame, arrival_monotonic_us)

        if self._serial is not None:
            try:
                self._serial.close()
            except Exception:
                pass
        self.status_callback(self.settings.sensor_id, False, f"{label} 已断开")


# ---------------------------------------------------------------------------
# 传感器管理器
# ---------------------------------------------------------------------------

class PressureSensorManager(QtCore.QObject):
    connection_changed = QtCore.pyqtSignal(str, bool, str)
    frame_received = QtCore.pyqtSignal(str, object)
    log_message = QtCore.pyqtSignal(str)

    def __init__(self) -> None:
        super().__init__()
        self.settings_map: Dict[str, PressureSensorSettings] = {
            sensor.key: PressureSensorSettings(sensor_id=sensor.key)
            for sensor in PRESSURE_SENSORS
        }
        self.snapshot_map: Dict[str, PressureSnapshot] = {
            sensor.key: PressureSnapshot(sensor_id=sensor.key, label=sensor.label)
            for sensor in PRESSURE_SENSORS
        }
        self.stats_map: Dict[str, PressureParseStats] = {
            sensor.key: PressureParseStats(sensor_id=sensor.key, label=sensor.label)
            for sensor in PRESSURE_SENSORS
        }
        self.workers: Dict[str, PressureSensorWorker] = {}
        self.frame_consumer: Optional[Callable[[PressureFrame], None]] = None
        self.visualization_enabled = True
        self._raw_queue: queue.SimpleQueue[tuple[PressureSensorSettings, bytes, int]] = queue.SimpleQueue()
        self._processor_stop = threading.Event()
        self._processor_thread = threading.Thread(target=self._process_raw_frames, daemon=True)
        self._frame_index_map: Dict[str, int] = {sensor.key: 0 for sensor in PRESSURE_SENSORS}
        self._state_lock = threading.Lock()
        self._processor_thread.start()

    def set_frame_consumer(self, consumer: Callable[[PressureFrame], None]) -> None:
        self.frame_consumer = consumer

    def set_visualization_enabled(self, enabled: bool) -> None:
        self.visualization_enabled = enabled

    def update_settings(self, settings_list: List[PressureSensorSettings]) -> None:
        self._stop_workers()
        with self._state_lock:
            self.settings_map = {item.sensor_id: item for item in settings_list}
            self._frame_index_map = {sensor.key: 0 for sensor in PRESSURE_SENSORS}
            self.stats_map = {
                sensor.key: PressureParseStats(sensor_id=sensor.key, label=sensor.label)
                for sensor in PRESSURE_SENSORS
            }

        for sensor in PRESSURE_SENSORS:
            with self._state_lock:
                settings = self.settings_map.get(
                    sensor.key, PressureSensorSettings(sensor_id=sensor.key)
                )

                self.snapshot_map[sensor.key] = PressureSnapshot(
                    sensor_id=sensor.key,
                    label=PRESSURE_SENSOR_LABELS[sensor.key],
                    connected=False,
                    port=settings.port,
                    message=(
                        "未启用" if not settings.enabled
                        else ("未配置端口" if not settings.port else "连接中")
                    ),
                )
            if not settings.enabled or not settings.port:
                continue

            worker = PressureSensorWorker(
                settings=settings,
                raw_frame_callback=self._enqueue_raw_frame,
                status_callback=self._handle_status,
                log_callback=self._handle_log,
            )
            self.workers[sensor.key] = worker
            worker.start()

    def snapshots(self) -> Dict[str, PressureSnapshot]:
        with self._state_lock:
            return dict(self.snapshot_map)

    def parse_stats(self) -> Dict[str, PressureParseStats]:
        with self._state_lock:
            return dict(self.stats_map)

    def _stop_workers(self) -> None:
        for worker in self.workers.values():
            worker.stop()
        for worker in self.workers.values():
            worker.join(timeout=1.0)
        self.workers.clear()

    def shutdown(self) -> None:
        self._stop_workers()
        self._processor_stop.set()
        self._processor_thread.join(timeout=1.0)

    # ------------------------------------------------------------------
    # 内部回调
    # ------------------------------------------------------------------

    def _handle_status(self, sensor_id: str, connected: bool, message: str) -> None:
        with self._state_lock:
            previous = self.snapshot_map.get(sensor_id)
            self.snapshot_map[sensor_id] = PressureSnapshot(
                sensor_id=sensor_id,
                label=PRESSURE_SENSOR_LABELS.get(sensor_id, sensor_id),
                connected=connected,
                port=self.settings_map.get(
                    sensor_id, PressureSensorSettings(sensor_id=sensor_id)
                ).port,
                last_frame_index=previous.last_frame_index if previous else -1,
                last_received_at=previous.last_received_at if previous else None,
                message=message,
                raw_values=previous.raw_values.copy() if previous and previous.raw_values is not None else None,
                display_values=previous.display_values.copy() if previous and previous.display_values is not None else None,
            )
        self.connection_changed.emit(sensor_id, connected, message)
        self.log_message.emit(message)

    def _enqueue_raw_frame(
        self,
        settings: PressureSensorSettings,
        raw_frame: bytes,
        arrival_monotonic_us: int,
    ) -> None:
        self._raw_queue.put((settings, raw_frame, arrival_monotonic_us))

    def _process_raw_frames(self) -> None:
        while not self._processor_stop.is_set():
            try:
                settings, raw_frame, arrival_monotonic_us = self._raw_queue.get(timeout=0.1)
            except queue.Empty:
                continue
            with self._state_lock:
                frame_index = self._frame_index_map.get(settings.sensor_id, 0)
                previous = self.stats_map.get(settings.sensor_id)
                self.stats_map[settings.sensor_id] = PressureParseStats(
                    sensor_id=settings.sensor_id,
                    label=settings.label,
                    received_frames=(previous.received_frames if previous else 0) + 1,
                    parsed_frames=previous.parsed_frames if previous else 0,
                    failed_frames=previous.failed_frames if previous else 0,
                    last_error=previous.last_error if previous else "",
                )
            try:
                frame = parse_pressure_frame(settings, raw_frame, frame_index, arrival_monotonic_us)
            except Exception as exc:
                with self._state_lock:
                    previous = self.stats_map.get(settings.sensor_id)
                    self.stats_map[settings.sensor_id] = PressureParseStats(
                        sensor_id=settings.sensor_id,
                        label=settings.label,
                        received_frames=previous.received_frames if previous else 0,
                        parsed_frames=previous.parsed_frames if previous else 0,
                        failed_frames=(previous.failed_frames if previous else 0) + 1,
                        last_error=str(exc),
                    )
                self._handle_log(f"{settings.label} 解析失败：{exc}")
                continue
            with self._state_lock:
                self._frame_index_map[settings.sensor_id] = frame_index + 1
                previous = self.stats_map.get(settings.sensor_id)
                self.stats_map[settings.sensor_id] = PressureParseStats(
                    sensor_id=settings.sensor_id,
                    label=settings.label,
                    received_frames=previous.received_frames if previous else 0,
                    parsed_frames=(previous.parsed_frames if previous else 0) + 1,
                    failed_frames=previous.failed_frames if previous else 0,
                    last_error=previous.last_error if previous else "",
                )
            self._publish_frame(frame)

    def _publish_frame(self, frame: PressureFrame) -> None:
        with self._state_lock:
            self.snapshot_map[frame.sensor_id] = PressureSnapshot(
                sensor_id=frame.sensor_id,
                label=frame.label,
                connected=True,
                port=self.settings_map.get(
                    frame.sensor_id, PressureSensorSettings(sensor_id=frame.sensor_id)
                ).port,
                last_frame_index=frame.frame_index,
                last_received_at=frame.captured_at,
                message="48点 压力帧",
                raw_values=frame.raw_values.copy(),
                display_values=frame.display_values.copy(),
            )
        if self.frame_consumer is not None:
            self.frame_consumer(frame.clone())
        if self.visualization_enabled:
            self.frame_received.emit(frame.sensor_id, frame.clone())

    def _handle_log(self, message: str) -> None:
        self.log_message.emit(message)
