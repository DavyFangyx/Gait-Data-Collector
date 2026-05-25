from __future__ import annotations

import threading
from typing import List

from PyQt5 import QtCore

try:
    import serial
    from serial.tools import list_ports
except ImportError:  # pragma: no cover
    serial = None
    list_ports = None

from .config import SerialSyncSettings


def available_serial_ports() -> List[str]:
    if list_ports is None:
        return []
    return [item.device for item in list_ports.comports()]


class SerialSyncController(QtCore.QObject):
    connection_changed = QtCore.pyqtSignal(bool, str)
    line_received = QtCore.pyqtSignal(str)
    start_requested = QtCore.pyqtSignal(str)
    stop_requested = QtCore.pyqtSignal(str)
    line_sent = QtCore.pyqtSignal(str)

    def __init__(self) -> None:
        super().__init__()
        self.settings = SerialSyncSettings()
        self._serial = None
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._write_lock = threading.Lock()

    def update_settings(self, settings: SerialSyncSettings) -> None:
        self.settings = settings
        self.disconnect()
        if settings.port:
            self.connect()

    def connect(self) -> None:
        if serial is None:
            self.connection_changed.emit(False, "缺少 pyserial，无法连接同步串口。")
            return
        if not self.settings.port:
            self.connection_changed.emit(False, "未配置同步串口。")
            return
        try:
            self._serial = serial.Serial(
                port=self.settings.port,
                baudrate=self.settings.baud_rate,
                timeout=0.2,
                write_timeout=1.0,
            )
        except Exception as exc:
            self._serial = None
            self.connection_changed.emit(False, f"串口连接失败：{exc}")
            return

        self._stop_event.clear()
        self._thread = threading.Thread(target=self._read_loop, daemon=True)
        self._thread.start()
        self.connection_changed.emit(True, f"已连接 {self.settings.port}")

    def disconnect(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        self._thread = None
        if self._serial is not None:
            try:
                self._serial.close()
            except Exception:
                pass
        self._serial = None
        self.connection_changed.emit(False, "同步串口已断开")

    def send_line(self, line: str) -> bool:
        if not line or self._serial is None:
            return False
        with self._write_lock:
            try:
                payload = (line.strip() + "\r\n").encode("ascii", errors="ignore")
                self._serial.write(payload)
                self.line_sent.emit(line.strip())
                return True
            except Exception:
                self.connection_changed.emit(False, "串口写入失败")
                return False

    def _read_loop(self) -> None:
        assert self._serial is not None
        while not self._stop_event.is_set():
            try:
                raw = self._serial.readline()
            except Exception as exc:
                self.connection_changed.emit(False, f"串口读取失败：{exc}")
                break
            if not raw:
                continue
            line = raw.decode("ascii", errors="ignore").strip()
            if not line:
                continue
            self.line_received.emit(line)
            normalized = line.upper()
            if normalized == self.settings.start_token.upper():
                self.start_requested.emit(line)
            elif normalized == self.settings.stop_token.upper():
                self.stop_requested.emit(line)
