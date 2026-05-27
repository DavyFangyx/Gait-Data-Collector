from __future__ import annotations

import threading
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from PyQt5 import QtCore, QtWidgets

from ...config import AppSettings, PressureSensorSettings
from ...constants import PRESSURE_SENSORS
from ...pressure_sensor import (
    BAUD_RATE,
    FOOT_ID_LEFT,
    FOOT_ID_RIGHT,
    FRAME_HEADER,
    FRAME_LEN,
    PACKED_DATA_LEN,
    available_serial_ports,
    sum_checksum,
)
from ..widgets import TechPanel, repolish

try:
    import serial
except ImportError:  # pragma: no cover
    serial = None


@dataclass
class PacketStats:
    valid_frames: int = 0
    checksum_failures: int = 0
    foot_id_mismatches: int = 0
    header_misses: int = 0
    serial_errors: int = 0
    bytes_read: int = 0
    connected: int = 0
    last_error: str = ""
    checksum_with_embedded_prefix: int = 0
    embedded_prefix_offsets: Counter[int] = field(default_factory=Counter)
    failure_samples: list[dict[str, Any]] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {
                "valid_frames": self.valid_frames,
                "checksum_failures": self.checksum_failures,
                "foot_id_mismatches": self.foot_id_mismatches,
                "header_misses": self.header_misses,
                "serial_errors": self.serial_errors,
                "bytes_read": self.bytes_read,
                "connected": self.connected,
                "last_error": self.last_error,
                "checksum_with_embedded_prefix": self.checksum_with_embedded_prefix,
                "embedded_prefix_offsets": dict(self.embedded_prefix_offsets),
                "failure_samples": list(self.failure_samples),
            }

    def increment(self, field_name: str, value: int = 1) -> None:
        with self.lock:
            setattr(self, field_name, getattr(self, field_name) + value)

    def set_state(self, *, connected: int | None = None, last_error: str | None = None) -> None:
        with self.lock:
            if connected is not None:
                self.connected = connected
            if last_error is not None:
                self.last_error = last_error

    def record_checksum_failure(
        self,
        *,
        expected_checksum: int,
        actual_checksum: int,
        embedded_prefix_offset: int,
        frame_bytes: bytes,
        sample_limit: int,
    ) -> None:
        with self.lock:
            self.checksum_failures += 1
            if embedded_prefix_offset >= 0:
                self.checksum_with_embedded_prefix += 1
                self.embedded_prefix_offsets[embedded_prefix_offset] += 1
            if len(self.failure_samples) < sample_limit:
                self.failure_samples.append(
                    {
                        "expected_checksum": expected_checksum,
                        "actual_checksum": actual_checksum,
                        "embedded_prefix_offset": embedded_prefix_offset,
                        "head_hex": frame_bytes[:16].hex(" "),
                        "tail_hex": frame_bytes[-16:].hex(" "),
                    }
                )


def consume_frames(buffer: bytearray, prefix: bytes, stats: PacketStats) -> list[bytes]:
    frames: list[bytes] = []
    prefix_len = len(prefix)
    while True:
        index = buffer.find(prefix)
        if index < 0:
            keep = min(len(buffer), max(0, prefix_len - 1))
            if len(buffer) > keep:
                stats.increment("header_misses", len(buffer) - keep)
            if keep:
                del buffer[:-keep]
            else:
                buffer.clear()
            break
        if index > 0:
            stats.increment("header_misses", index)
            del buffer[:index]
        if len(buffer) < FRAME_LEN:
            break
        frames.append(bytes(buffer[:FRAME_LEN]))
        del buffer[:FRAME_LEN]
    return frames


class PacketReader(threading.Thread):
    def __init__(
        self,
        port: str,
        expected_foot_id: int,
        stats: PacketStats,
        sample_limit: int = 8,
    ) -> None:
        super().__init__(daemon=True)
        self.port = port
        self.expected_foot_id = expected_foot_id
        self.stats = stats
        self.sample_limit = sample_limit
        self.stop_event = threading.Event()
        self.prefix = bytes([FRAME_HEADER[0], expected_foot_id])
        self._serial = None

    def stop(self) -> None:
        self.stop_event.set()

    def run(self) -> None:
        if serial is None:
            self.stats.increment("serial_errors")
            self.stats.set_state(connected=0, last_error="缺少 pyserial")
            return
        try:
            self._serial = serial.Serial(
                port=self.port,
                baudrate=BAUD_RATE,
                timeout=0.2,
                write_timeout=0.5,
            )
            self.stats.set_state(connected=1, last_error="")
        except Exception as exc:
            self.stats.increment("serial_errors")
            self.stats.set_state(connected=0, last_error=str(exc))
            return

        buffer = bytearray()
        while not self.stop_event.is_set():
            try:
                chunk = self._serial.read(max(FRAME_LEN * 2, 128))
            except Exception as exc:
                self.stats.increment("serial_errors")
                self.stats.set_state(connected=0, last_error=str(exc))
                break
            if not chunk:
                continue
            self.stats.increment("bytes_read", len(chunk))
            buffer.extend(chunk)
            for frame_bytes in consume_frames(buffer, self.prefix, self.stats):
                self._handle_frame(frame_bytes)

        if self._serial is not None:
            try:
                self._serial.close()
            except Exception:
                pass
        self.stats.set_state(connected=0)

    def _handle_frame(self, frame_bytes: bytes) -> None:
        if len(frame_bytes) != FRAME_LEN:
            self.stats.increment("header_misses")
            return
        if frame_bytes[0] != FRAME_HEADER[0]:
            self.stats.increment("header_misses")
            return
        if frame_bytes[1] != self.expected_foot_id:
            self.stats.increment("foot_id_mismatches")
            return

        expected_checksum = sum_checksum(frame_bytes[: FRAME_LEN - 1])
        actual_checksum = frame_bytes[FRAME_LEN - 1]
        if expected_checksum != actual_checksum:
            embedded_prefix_offset = frame_bytes.find(self.prefix, 2)
            self.stats.record_checksum_failure(
                expected_checksum=expected_checksum,
                actual_checksum=actual_checksum,
                embedded_prefix_offset=embedded_prefix_offset,
                frame_bytes=frame_bytes,
                sample_limit=self.sample_limit,
            )
            return

        payload = frame_bytes[2:98]
        if len(payload) != PACKED_DATA_LEN:
            self.stats.increment("header_misses")
            return
        self.stats.increment("valid_frames")


class PacketMonitorPage(QtWidgets.QWidget):
    settings_saved = QtCore.pyqtSignal(object)
    ports_scanned = QtCore.pyqtSignal(list, dict, str)

    def __init__(self, settings: AppSettings, parent=None) -> None:
        super().__init__(parent)
        self.settings = settings
        self._rows: dict[str, dict[str, QtWidgets.QWidget]] = {}
        self._stats: dict[str, PacketStats] = {}
        self._readers: dict[str, PacketReader] = {}
        self._previous: dict[str, dict[str, Any]] = {}
        self._elapsed_seconds = 0
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._refresh_stats)
        self.ports_scanned.connect(self._apply_scanned_ports)
        self._build_ui()
        self.update_settings(settings)
        self.scan_ports()

    def _build_ui(self) -> None:
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        panel = TechPanel()
        layout = QtWidgets.QVBoxLayout(panel)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(12)

        header = QtWidgets.QHBoxLayout()
        title = QtWidgets.QLabel("压力包监控")
        title.setProperty("panelTitle", True)
        self.state_badge = QtWidgets.QLabel("未运行")
        self.state_badge.setAlignment(QtCore.Qt.AlignCenter)
        self.state_badge.setProperty("chipRole", "status")
        self.state_badge.setProperty("level", "warning")
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(self.state_badge)
        layout.addLayout(header)

        controls = QtWidgets.QGridLayout()
        controls.setHorizontalSpacing(10)
        controls.setVerticalSpacing(8)
        self.scan_button = QtWidgets.QPushButton("串口扫描")
        self.scan_button.clicked.connect(self.scan_ports)
        self.start_button = QtWidgets.QPushButton("开始监控")
        self.start_button.setProperty("accent", True)
        self.start_button.clicked.connect(self._toggle_monitoring)
        self.save_button = QtWidgets.QPushButton("保存到设置")
        self.save_button.clicked.connect(self._save_ports_to_settings)

        for row, sensor in enumerate(PRESSURE_SENSORS):
            label = QtWidgets.QLabel(f"{sensor.label}串口")
            combo = QtWidgets.QComboBox()
            combo.setEditable(True)
            combo.setMinimumWidth(180)
            foot_id = QtWidgets.QLabel("0x31" if sensor.key == "left_foot" else "0x32")
            foot_id.setProperty("muted", True)
            controls.addWidget(label, row, 0)
            controls.addWidget(combo, row, 1)
            controls.addWidget(QtWidgets.QLabel("脚编号"), row, 2)
            controls.addWidget(foot_id, row, 3)
            self._rows[sensor.key] = {"combo": combo}

        controls.addWidget(self.scan_button, 0, 4)
        controls.addWidget(self.start_button, 1, 4)
        controls.addWidget(self.save_button, 0, 5, 2, 1)
        controls.setColumnStretch(1, 1)
        layout.addLayout(controls)

        self.table = QtWidgets.QTableWidget(len(PRESSURE_SENSORS), 10)
        self.table.setHorizontalHeaderLabels(
            ["足部", "状态", "本秒有效", "有效总数", "校验失败", "丢头字节", "脚编号错", "串口错误", "读取字节", "最近错误"]
        )
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.NoSelection)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        header_view = self.table.horizontalHeader()
        header_view.setStretchLastSection(True)
        for column in range(9):
            mode = QtWidgets.QHeaderView.ResizeToContents if column != 9 else QtWidgets.QHeaderView.Stretch
            header_view.setSectionResizeMode(column, mode)
        layout.addWidget(self.table)

        for row, sensor in enumerate(PRESSURE_SENSORS):
            self.table.setItem(row, 0, QtWidgets.QTableWidgetItem(sensor.label))
            for column in range(1, 10):
                self.table.setItem(row, column, QtWidgets.QTableWidgetItem("-"))

        bottom = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        bottom.setChildrenCollapsible(False)
        bottom.setHandleWidth(8)
        self.offset_view = QtWidgets.QPlainTextEdit()
        self.offset_view.setReadOnly(True)
        self.offset_view.setProperty("console", True)
        self.sample_view = QtWidgets.QPlainTextEdit()
        self.sample_view.setReadOnly(True)
        self.sample_view.setProperty("console", True)
        bottom.addWidget(self.offset_view)
        bottom.addWidget(self.sample_view)
        bottom.setStretchFactor(0, 1)
        bottom.setStretchFactor(1, 2)
        layout.addWidget(bottom, 1)

        root.addWidget(panel, 1)

    def update_settings(self, settings: AppSettings) -> None:
        self.settings = settings
        sensor_map = {item.sensor_id: item for item in settings.pressure_sensors}
        for sensor in PRESSURE_SENSORS:
            combo = self._rows[sensor.key]["combo"]
            current = sensor_map.get(sensor.key, PressureSensorSettings(sensor.key)).port
            if isinstance(combo, QtWidgets.QComboBox):
                combo.setCurrentText(current)

    def scan_ports(self) -> None:
        if self._readers:
            return
        ports = available_serial_ports()
        self.scan_button.setEnabled(False)
        self.scan_button.setText("扫描中...")
        threading.Thread(target=self._scan_port_roles, args=(ports,), daemon=True).start()

    def _scan_port_roles(self, ports: list[str]) -> None:
        detected: dict[str, str] = {}
        messages: list[str] = []
        if serial is None:
            self.ports_scanned.emit(ports, detected, "缺少 pyserial，无法识别左右脚串口。")
            return

        for port in ports:
            if len(detected) >= 2:
                break
            try:
                role = self._detect_port_role(port)
            except Exception as exc:
                messages.append(f"{port}: {exc}")
                continue
            if role and role not in detected:
                detected[role] = port

        if detected:
            summary = " | ".join(
                f"{'左脚' if key == 'left_foot' else '右脚'} {value}"
                for key, value in detected.items()
            )
            message = f"串口扫描完成：{summary}"
        elif ports:
            message = "串口扫描完成：未识别到左右脚数据包。"
        else:
            message = "串口扫描完成：未发现串口。"
        if messages:
            message = f"{message} {'; '.join(messages[:3])}"
        self.ports_scanned.emit(ports, detected, message)

    def _detect_port_role(self, port: str) -> str:
        assert serial is not None
        probes = {
            "left_foot": bytes([FRAME_HEADER[0], FOOT_ID_LEFT]),
            "right_foot": bytes([FRAME_HEADER[0], FOOT_ID_RIGHT]),
        }
        buffer = bytearray()
        with serial.Serial(port=port, baudrate=BAUD_RATE, timeout=0.12, write_timeout=0.2) as handle:
            deadline = time.monotonic() + 0.9
            while time.monotonic() < deadline:
                chunk = handle.read(max(FRAME_LEN * 2, 128))
                if not chunk:
                    continue
                buffer.extend(chunk)
                if len(buffer) > FRAME_LEN * 6:
                    del buffer[: len(buffer) - FRAME_LEN * 6]
                for sensor_id, prefix in probes.items():
                    index = buffer.find(prefix)
                    while index >= 0 and len(buffer) >= index + FRAME_LEN:
                        frame = bytes(buffer[index:index + FRAME_LEN])
                        if sum_checksum(frame[: FRAME_LEN - 1]) == frame[FRAME_LEN - 1]:
                            return sensor_id
                        index = buffer.find(prefix, index + 1)
        return ""

    def _apply_scanned_ports(self, ports: list[str], detected: dict[str, str], message: str) -> None:
        configured = {
            item.sensor_id: item.port
            for item in self.settings.pressure_sensors
            if item.port
        }
        for sensor in PRESSURE_SENSORS:
            combo = self._rows[sensor.key]["combo"]
            if not isinstance(combo, QtWidgets.QComboBox):
                continue
            current = detected.get(sensor.key) or combo.currentText().strip() or configured.get(sensor.key, "")
            combo.blockSignals(True)
            combo.clear()
            combo.addItem("")
            for port in ports:
                combo.addItem(port)
            if current and combo.findText(current) < 0:
                combo.addItem(current)
            combo.setCurrentText(current)
            combo.blockSignals(False)
        self.scan_button.setText("串口扫描")
        self.scan_button.setEnabled(True)
        self._append_log(message)

    def _toggle_monitoring(self) -> None:
        if self._readers:
            self.stop_monitoring()
            return
        self.start_monitoring()

    def start_monitoring(self) -> None:
        left_port = self._port_for("left_foot")
        right_port = self._port_for("right_foot")
        if not left_port or not right_port:
            QtWidgets.QMessageBox.warning(self, "无法开始监控", "请先选择左脚和右脚串口。")
            return
        if left_port == right_port:
            QtWidgets.QMessageBox.warning(self, "无法开始监控", "左脚和右脚不能使用同一个串口。")
            return

        self._stats = {"left_foot": PacketStats(), "right_foot": PacketStats()}
        self._previous = {key: stats.snapshot() for key, stats in self._stats.items()}
        self._elapsed_seconds = 0
        self._readers = {
            "left_foot": PacketReader(left_port, FOOT_ID_LEFT, self._stats["left_foot"]),
            "right_foot": PacketReader(right_port, FOOT_ID_RIGHT, self._stats["right_foot"]),
        }
        for reader in self._readers.values():
            reader.start()
        self._timer.start()
        self._set_running_state(True)
        self._append_log(f"开始监控：左脚 {left_port} | 右脚 {right_port} | 波特率 {BAUD_RATE}")
        self._refresh_stats()

    def stop_monitoring(self) -> None:
        for reader in self._readers.values():
            reader.stop()
        for reader in self._readers.values():
            reader.join(timeout=1.0)
        self._readers.clear()
        self._timer.stop()
        self._set_running_state(False)
        self._refresh_stats()
        self._append_log("监控已停止。")

    def _set_running_state(self, running: bool) -> None:
        self.state_badge.setText("监控中" if running else "未运行")
        self.state_badge.setProperty("level", "normal" if running else "warning")
        repolish(self.state_badge)
        self.start_button.setText("停止监控" if running else "开始监控")
        self.start_button.setProperty("accent", not running)
        self.start_button.setProperty("danger", running)
        repolish(self.start_button)
        self.scan_button.setEnabled(not running)
        self.save_button.setEnabled(not running)
        for sensor in PRESSURE_SENSORS:
            widget = self._rows[sensor.key]["combo"]
            if isinstance(widget, QtWidgets.QComboBox):
                widget.setEnabled(not running)

    def _port_for(self, sensor_id: str) -> str:
        combo = self._rows[sensor_id]["combo"]
        if isinstance(combo, QtWidgets.QComboBox):
            return combo.currentText().strip()
        return ""

    def _refresh_stats(self) -> None:
        self._elapsed_seconds += 1 if self._readers else 0
        offset_lines = []
        sample_lines = []
        for row, sensor in enumerate(PRESSURE_SENSORS):
            stats = self._stats.get(sensor.key)
            snapshot = stats.snapshot() if stats is not None else {}
            previous = self._previous.get(sensor.key, snapshot)
            valid_delta = self._delta(snapshot, previous, "valid_frames")
            checksum_delta = self._delta(snapshot, previous, "checksum_failures")
            self._previous[sensor.key] = snapshot

            connected = bool(snapshot.get("connected", 0))
            status = "在线" if connected else ("离线" if self._readers else "未运行")
            values = [
                status,
                str(valid_delta),
                str(snapshot.get("valid_frames", 0)),
                f"{snapshot.get('checksum_failures', 0)} (+{checksum_delta})",
                str(snapshot.get("header_misses", 0)),
                str(snapshot.get("foot_id_mismatches", 0)),
                str(snapshot.get("serial_errors", 0)),
                str(snapshot.get("bytes_read", 0)),
                str(snapshot.get("last_error", "") or "-"),
            ]
            for column, value in enumerate(values, start=1):
                item = self.table.item(row, column)
                if item is not None:
                    item.setText(value)

            offset_lines.append(
                f"{sensor.label} embedded_prefix_offsets: "
                f"{self._format_top_offsets(snapshot.get('embedded_prefix_offsets', {}))}"
            )
            sample_lines.append(self._format_failure_samples(sensor.label, snapshot))
        self.offset_view.setPlainText("\n".join(offset_lines))
        self.sample_view.setPlainText("\n\n".join(sample_lines))

    def _save_ports_to_settings(self) -> None:
        sensors = []
        for sensor in PRESSURE_SENSORS:
            port = self._port_for(sensor.key)
            sensors.append(
                PressureSensorSettings(
                    sensor_id=sensor.key,
                    enabled=bool(port),
                    port=port,
                )
            )
        self.settings.pressure_sensors = sensors
        self.settings_saved.emit(self.settings)
        self._append_log("左右脚串口已保存到系统设置。")

    def _append_log(self, line: str) -> None:
        if not line:
            return
        self.sample_view.appendPlainText(line)

    @staticmethod
    def _delta(current: dict[str, Any], previous: dict[str, Any], field_name: str) -> int:
        return int(current.get(field_name, 0)) - int(previous.get(field_name, 0))

    @staticmethod
    def _format_top_offsets(offset_map: dict[int, int], top_n: int = 5) -> str:
        if not offset_map:
            return "-"
        items = sorted(offset_map.items(), key=lambda item: (-item[1], item[0]))[:top_n]
        return ", ".join(f"{offset}:{count}" for offset, count in items)

    @staticmethod
    def _format_failure_samples(label: str, stats: dict[str, Any]) -> str:
        samples = stats.get("failure_samples", [])
        if not samples:
            return f"{label} 失败样本：无"
        lines = [f"{label} 失败样本："]
        for index, sample in enumerate(samples, start=1):
            offset = sample["embedded_prefix_offset"]
            offset_text = offset if offset >= 0 else "-"
            lines.append(
                f"{index}. expected=0x{sample['expected_checksum']:02X} "
                f"actual=0x{sample['actual_checksum']:02X} next_prefix_offset={offset_text}"
            )
            lines.append(f"   head={sample['head_hex']}")
            lines.append(f"   tail={sample['tail_hex']}")
        return "\n".join(lines)

    def close(self) -> bool:
        self.stop_monitoring()
        return super().close()

    def closeEvent(self, event) -> None:  # noqa: N802
        self.stop_monitoring()
        super().closeEvent(event)
