from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List
from .constants import CAMERA_SLOTS, DEFAULT_OUTPUT_IDS, DEFAULT_SAVE_DIR, PRESSURE_SENSOR_LABELS, PRESSURE_SENSORS

LEGACY_OUTPUT_IDS = {
    "top": "Cam16",
    "left": "Cam18",
    "front": "Cam21",
    "right": "Cam25",
}


def app_root() -> Path:
    return Path(__file__).resolve().parent.parent


def settings_file() -> Path:
    return app_root() / "settings.json"


def subject_list_file() -> Path:
    return app_root() / "SubjectList.txt"


def legacy_subject_list_file() -> Path:
    return app_root() / "ISCamRecorder" / "media" / "SubjectList.txt"


def sanitize_subject_name(subject: str) -> str:
    text = (subject or "").strip()
    text = re.sub(r"[\\/:*?\"<>|]+", "_", text)
    return text or "未命名对象"


def build_session_name(recording_time: datetime, subject: str, counter: int) -> str:
    safe_subject = sanitize_subject_name(subject)
    return f"rec{recording_time:%Y%m%d_%H%M%S}_{safe_subject}_{counter}"

def _get_labels():
    from .constants import PRESSURE_SENSOR_LABELS
    return PRESSURE_SENSOR_LABELS

def _get_sensors():
    from .constants import PRESSURE_SENSORS
    return PRESSURE_SENSORS

@dataclass
class CameraBinding:
    slot: str
    device_serial: str = ""
    device_name: str = ""
    output_id: str = ""
    record_enabled: bool = True
    snapshot_enabled: bool = True
    exposure_time_us: float = 20000.0
    acquisition_frame_rate_fps: float = 30.0
    gain_db: float = 0.0
    gamma_value: float = 1.0
    black_level: float = 0.0

    @classmethod
    def default_bindings(cls) -> List["CameraBinding"]:
        return [
            cls(
                slot=slot.key,
                output_id=DEFAULT_OUTPUT_IDS[slot.key],
                record_enabled=True,
                snapshot_enabled=True,
            )
            for slot in CAMERA_SLOTS
        ]


@dataclass
class SerialSyncSettings:
    port: str = ""
    baud_rate: int = 115200
    start_token: str = "START"
    stop_token: str = "STOP"
    outbound_start_line: str = "REC_START"
    outbound_stop_line: str = "REC_STOP"


@dataclass
class PressureSensorSettings:
    sensor_id: str

    # 串口基本配置（仅开关与端口可配置，帧头/帧长/波特率为协议固定常量）
    enabled: bool = False
    port: str = ""

    @property
    def label(self) -> str:
        return _get_labels().get(self.sensor_id, self.sensor_id)

    @classmethod
    def default_sensors(cls) -> List["PressureSensorSettings"]:
        return [
            cls(sensor_id=sensor.key)
            for sensor in _get_sensors()
        ]


@dataclass
class AppSettings:
    save_dir: str = DEFAULT_SAVE_DIR
    image_type: str = "tif"
    frame_type: str = "jpg"
    movie_rate_kbps: int = 5000
    record_limit_sec: float = 30.0
    software_trigger_enabled: bool = False
    software_trigger_fps: float = 40.0
    camera_bindings: List[CameraBinding] = field(default_factory=CameraBinding.default_bindings)
    pressure_sensors: List[PressureSensorSettings] = field(default_factory=PressureSensorSettings.default_sensors)
    subjects: List[str] = field(default_factory=lambda: ["测试"])
    legacy_serial_sync: SerialSyncSettings = field(default_factory=SerialSyncSettings, repr=False)
    pressure_display_max: float = 1000.0  # 热力图压力上限（对应 vmax）
    pressure_scale_x: float = 1.0          # 热力图 X 轴缩放倍数
    pressure_scale_y: float = 2.7          # 热力图 Y 轴缩放倍数
    pressure_colormap: str = "viridis"     # 热力图配色方案
    pressure_display_mode: str = "heatmap" # heatmap / outline
    pressure_quality_auto: bool = True
    pressure_quality_output_dir: str = ""
    pressure_processing_method: str = "original"
    pressure_suspect_left: List[int] = field(default_factory=list)
    pressure_suspect_right: List[int] = field(default_factory=list)

    @classmethod
    def from_dict(cls, payload: dict) -> "AppSettings":
        data = dict(payload or {})
        bindings = []
        for item in data.get("camera_bindings", []):
            slot = item.get("slot", "")
            output_id = item.get("output_id", "")
            if not output_id or output_id == LEGACY_OUTPUT_IDS.get(slot):
                output_id = DEFAULT_OUTPUT_IDS.get(slot, output_id)
            bindings.append(
                CameraBinding(
                    slot=slot,
                    device_serial=str(item.get("device_serial", "") or "").strip(),
                    device_name=str(item.get("device_name", "") or "").strip(),
                    output_id=output_id,
                    record_enabled=bool(item.get("record_enabled", True)),
                    snapshot_enabled=bool(item.get("snapshot_enabled", True)),
                    exposure_time_us=float(item.get("exposure_time_us", 20000.0)),
                    acquisition_frame_rate_fps=float(item.get("acquisition_frame_rate_fps", 30.0)),
                    gain_db=float(item.get("gain_db", 0.0)),
                    gamma_value=float(item.get("gamma_value", 1.0)),
                    black_level=float(item.get("black_level", 0.0)),
                )
            )
        if not bindings:
            bindings = CameraBinding.default_bindings()

        sensor_payload = {item.get("sensor_id", ""): item for item in data.get("pressure_sensors", [])}
        sensors = []
        for sensor in PRESSURE_SENSORS:
            item = sensor_payload.get(sensor.key, {})
            sensors.append(
                PressureSensorSettings(
                    sensor_id=sensor.key,
                    enabled=bool(item.get("enabled", False)),
                    port=str(item.get("port", "")),
                )
            )

        serial_payload = data.get("serial_sync", {})
        subjects = data.get("subjects") or load_subjects()
        clean_subjects = [item.strip() for item in subjects if item.strip()]
        return cls(
            save_dir=data.get("save_dir", DEFAULT_SAVE_DIR),
            image_type=str(data.get("image_type", "tif")).lower(),
            frame_type=str(data.get("frame_type", "jpg")).lower(),
            movie_rate_kbps=int(data.get("movie_rate_kbps", 5000)),
            record_limit_sec=float(data.get("record_limit_sec", 30.0)),
            software_trigger_enabled=bool(data.get("software_trigger_enabled", False)),
            software_trigger_fps=float(data.get("software_trigger_fps", 40.0)),
            camera_bindings=bindings,
            pressure_sensors=sensors or PressureSensorSettings.default_sensors(),
            subjects=clean_subjects or ["测试"],
            pressure_display_max=float(data.get("pressure_display_max", 1000.0)),
            pressure_scale_x=float(data.get("pressure_scale_x", 1.0)),
            pressure_scale_y=float(data.get("pressure_scale_y", 2.7)),
            pressure_colormap=str(data.get("pressure_colormap", "viridis")),
            pressure_display_mode=str(data.get("pressure_display_mode", "heatmap")),
            pressure_quality_auto=bool(data.get("pressure_quality_auto", True)),
            pressure_quality_output_dir=str(data.get("pressure_quality_output_dir", "")),
            pressure_processing_method=str(data.get("pressure_processing_method", "original")),
            pressure_suspect_left=list(data.get("pressure_suspect_left", [])),
            pressure_suspect_right=list(data.get("pressure_suspect_right", [])),
            legacy_serial_sync=SerialSyncSettings(
                port=serial_payload.get("port", ""),
                baud_rate=int(serial_payload.get("baud_rate", 115200)),
                start_token=serial_payload.get("start_token", "START"),
                stop_token=serial_payload.get("stop_token", "STOP"),
                outbound_start_line=serial_payload.get("outbound_start_line", "REC_START"),
                outbound_stop_line=serial_payload.get("outbound_stop_line", "REC_STOP"),
            ),
        )

    def to_dict(self) -> dict:
        return {
            "save_dir": self.save_dir,
            "image_type": self.image_type,
            "frame_type": self.frame_type,
            "movie_rate_kbps": self.movie_rate_kbps,
            "record_limit_sec": self.record_limit_sec,
            "software_trigger_enabled": self.software_trigger_enabled,
            "software_trigger_fps": self.software_trigger_fps,
            "camera_bindings": [
                {
                    "slot": item.slot,
                    "device_serial": item.device_serial,
                    "output_id": item.output_id,
                    "record_enabled": item.record_enabled,
                    "snapshot_enabled": item.snapshot_enabled,
                    "exposure_time_us": item.exposure_time_us,
                    "acquisition_frame_rate_fps": item.acquisition_frame_rate_fps,
                    "gain_db": item.gain_db,
                    "gamma_value": item.gamma_value,
                    "black_level": item.black_level,
                }
                for item in self.camera_bindings
            ],
            "pressure_sensors": [
                {
                    "sensor_id": item.sensor_id,
                    "enabled": item.enabled,
                    "port": item.port,
                }
                for item in self.pressure_sensors
            ],
            "subjects": [item.strip() for item in self.subjects if item.strip()],
            "pressure_display_max": self.pressure_display_max,
            "pressure_scale_x": self.pressure_scale_x,
            "pressure_scale_y": self.pressure_scale_y,
            "pressure_colormap": self.pressure_colormap,
            "pressure_display_mode": self.pressure_display_mode,
            "pressure_quality_auto": self.pressure_quality_auto,
            "pressure_quality_output_dir": self.pressure_quality_output_dir,
            "pressure_processing_method": self.pressure_processing_method,
            "pressure_suspect_left": self.pressure_suspect_left,
            "pressure_suspect_right": self.pressure_suspect_right,
        }

    def binding_for_slot(self, slot: str) -> CameraBinding | None:
        return next((item for item in self.camera_bindings if item.slot == slot), None)

    def pressure_sensor_for_id(self, sensor_id: str) -> PressureSensorSettings | None:
        return next((item for item in self.pressure_sensors if item.sensor_id == sensor_id), None)


def load_subjects() -> List[str]:
    target = subject_list_file()
    if not target.exists() and legacy_subject_list_file().exists():
        return [
            item.strip()
            for item in legacy_subject_list_file().read_text(encoding="utf-8-sig").splitlines()
            if item.strip()
        ]
    if not target.exists():
        return ["测试"]
    return [item.strip() for item in target.read_text(encoding="utf-8-sig").splitlines() if item.strip()]


def save_subjects(subjects: List[str]) -> None:
    lines = [item.strip() for item in subjects if item.strip()]
    subject_list_file().write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


class SettingsStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or settings_file()

    def load(self) -> AppSettings:
        if not self.path.exists():
            settings = AppSettings(subjects=load_subjects())
            self.save(settings)
            return settings
        payload = json.loads(self.path.read_text(encoding="utf-8-sig"))
        return AppSettings.from_dict(payload)

    def save(self, settings: AppSettings) -> None:
        save_subjects(settings.subjects)
        self.path.write_text(
            json.dumps(settings.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
