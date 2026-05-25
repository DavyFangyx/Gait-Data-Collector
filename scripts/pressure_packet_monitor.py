#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
import threading
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR if (SCRIPT_DIR / "python_app").is_dir() else SCRIPT_DIR.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from python_app.config import SettingsStore

try:
    import serial
except ImportError as exc:  # pragma: no cover
    raise SystemExit("缺少 pyserial，请先安装 requirements.txt。") from exc


FRAME_HEADER = 0x40
FOOT_ID_LEFT = 0x31
FOOT_ID_RIGHT = 0x32
FRAME_LEN = 99
PACKED_DATA_LEN = 96
BAUD_RATE = 115200


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


def sum_checksum(data: bytes | bytearray) -> int:
    return sum(data) & 0xFF


def verify_checksum(frame_bytes: bytes) -> bool:
    if len(frame_bytes) != FRAME_LEN:
        return False
    return sum_checksum(frame_bytes[: FRAME_LEN - 1]) == frame_bytes[FRAME_LEN - 1]


def consume_frames(
    buffer: bytearray,
    prefix: bytes,
    stats: PacketStats,
) -> list[bytes]:
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
        name: str,
        port: str,
        expected_foot_id: int,
        stats: PacketStats,
        sample_limit: int,
    ) -> None:
        super().__init__(daemon=True)
        self.name = name
        self.port = port
        self.expected_foot_id = expected_foot_id
        self.stats = stats
        self.sample_limit = sample_limit
        self.stop_event = threading.Event()
        self.prefix = bytes([FRAME_HEADER, expected_foot_id])
        self._serial: serial.Serial | None = None

    def stop(self) -> None:
        self.stop_event.set()

    def run(self) -> None:
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

    def _handle_frame(self, frame_bytes: bytes) -> None:
        if len(frame_bytes) != FRAME_LEN:
            self.stats.increment("header_misses")
            return
        if frame_bytes[0] != FRAME_HEADER:
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


def load_default_ports() -> tuple[str, str]:
    settings = SettingsStore().load()
    sensor_map = {item.sensor_id: item for item in settings.pressure_sensors}
    left_port = sensor_map.get("left_foot").port if sensor_map.get("left_foot") else ""
    right_port = sensor_map.get("right_foot").port if sensor_map.get("right_foot") else ""
    return left_port, right_port


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="监控左右脚触觉串口，每秒输出有效包和校验异常统计。",
    )
    default_left, default_right = load_default_ports()
    parser.add_argument("--left-port", default=default_left, help="左脚串口，默认取 settings.json")
    parser.add_argument("--right-port", default=default_right, help="右脚串口，默认取 settings.json")
    parser.add_argument("--duration", type=int, default=0, help="监控秒数，0 表示持续运行直到 Ctrl+C")
    parser.add_argument("--sample-limit", type=int, default=8, help="每只脚最多保留多少个校验失败样本")
    return parser


def delta(current: dict[str, Any], previous: dict[str, Any], field_name: str) -> int:
    return int(current[field_name]) - int(previous[field_name])


def format_top_offsets(offset_map: dict[int, int], top_n: int = 5) -> str:
    if not offset_map:
        return "-"
    items = sorted(offset_map.items(), key=lambda item: (-item[1], item[0]))[:top_n]
    return ", ".join(f"{offset}:{count}" for offset, count in items)


def print_failure_samples(label: str, stats: dict[str, Any]) -> None:
    samples = stats["failure_samples"]
    if not samples:
        print(f"{label} 失败样本: 无")
        return
    print(f"{label} 失败样本:")
    for index, sample in enumerate(samples, start=1):
        offset_text = sample["embedded_prefix_offset"] if sample["embedded_prefix_offset"] >= 0 else "-"
        print(
            f"  {index}. expected=0x{sample['expected_checksum']:02X}"
            f" actual=0x{sample['actual_checksum']:02X}"
            f" next_prefix_offset={offset_text}"
        )
        print(f"     head={sample['head_hex']}")
        print(f"     tail={sample['tail_hex']}")


def print_summary(label: str, stats: dict[str, Any]) -> None:
    print(
        f"{label}"
        f" valid={stats['valid_frames']}"
        f" checksum_failures={stats['checksum_failures']}"
        f" checksum_with_embedded_prefix={stats['checksum_with_embedded_prefix']}"
        f" foot_id_mismatches={stats['foot_id_mismatches']}"
        f" header_misses={stats['header_misses']}"
        f" serial_errors={stats['serial_errors']}"
        f" bytes_read={stats['bytes_read']}"
        f" connected={stats['connected']}"
        f" last_error={stats['last_error'] or '-'}"
    )
    print(f"{label} embedded_prefix_offsets={format_top_offsets(stats['embedded_prefix_offsets'])}")
    print_failure_samples(label, stats)


def main() -> int:
    args = build_parser().parse_args()
    if not args.left_port or not args.right_port:
        print("未找到左右脚串口，请通过 --left-port / --right-port 指定。")
        return 1

    left_stats = PacketStats()
    right_stats = PacketStats()
    readers = [
        PacketReader("left_foot", args.left_port, FOOT_ID_LEFT, left_stats, args.sample_limit),
        PacketReader("right_foot", args.right_port, FOOT_ID_RIGHT, right_stats, args.sample_limit),
    ]

    print(f"左脚串口: {args.left_port} | 右脚串口: {args.right_port} | 波特率: {BAUD_RATE}")
    print(
        "每秒统计: 时间"
        " | 左有效 | 右有效"
        " | 左校验失败 | 右校验失败"
        " | 左丢头字节 | 右丢头字节"
        " | 左串口异常 | 右串口异常"
    )

    for reader in readers:
        reader.start()

    started_at = time.monotonic()
    previous_left = left_stats.snapshot()
    previous_right = right_stats.snapshot()
    seconds_elapsed = 0

    try:
        while True:
            time.sleep(1.0)
            seconds_elapsed += 1

            current_left = left_stats.snapshot()
            current_right = right_stats.snapshot()
            print(
                f"{seconds_elapsed:04d}s"
                f" | L={delta(current_left, previous_left, 'valid_frames'):4d}"
                f" | R={delta(current_right, previous_right, 'valid_frames'):4d}"
                f" | Lchk={delta(current_left, previous_left, 'checksum_failures'):3d}"
                f" | Rchk={delta(current_right, previous_right, 'checksum_failures'):3d}"
                f" | Lhdr={delta(current_left, previous_left, 'header_misses'):4d}"
                f" | Rhdr={delta(current_right, previous_right, 'header_misses'):4d}"
                f" | Lerr={delta(current_left, previous_left, 'serial_errors'):2d}"
                f" | Rerr={delta(current_right, previous_right, 'serial_errors'):2d}"
            )
            previous_left = current_left
            previous_right = current_right

            if args.duration > 0 and (time.monotonic() - started_at) >= args.duration:
                break
    except KeyboardInterrupt:
        print("手动停止监控。")
    finally:
        for reader in readers:
            reader.stop()
        for reader in readers:
            reader.join(timeout=1.0)

    total_left = left_stats.snapshot()
    total_right = right_stats.snapshot()
    print("总计:")
    print_summary("左脚", total_left)
    print_summary("右脚", total_right)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
