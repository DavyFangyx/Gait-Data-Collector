from dataclasses import dataclass


APP_TITLE = "四机位同步采集系统"
DEFAULT_SAVE_DIR = r"D:\Data"
MAX_CAMERA_PROBE = 8
PREVIEW_INTERVAL_MS = 80
STATUS_REFRESH_MS = 500


@dataclass(frozen=True)
class SlotDefinition:
    key: str
    label: str
    default_output_id: str


@dataclass(frozen=True)
class PressureSensorDefinition:
    key: str
    label: str


CAMERA_SLOTS = [
    SlotDefinition("top", "1", "1"),
    SlotDefinition("left", "2", "2"),
    SlotDefinition("front", "3", "3"),
    SlotDefinition("right", "4", "4"),
]

PRESSURE_SENSORS = [
    PressureSensorDefinition("left_foot", "左脚"),
    PressureSensorDefinition("right_foot", "右脚"),
]

SLOT_LABELS = {slot.key: slot.label for slot in CAMERA_SLOTS}
DEFAULT_OUTPUT_IDS = {slot.key: slot.default_output_id for slot in CAMERA_SLOTS}
PRESSURE_SENSOR_LABELS = {sensor.key: sensor.label for sensor in PRESSURE_SENSORS}
STATE_INIT = "init"
STATE_IDLE = "idle"
STATE_RECORDING = "recording"
STATE_SAVING = "saving"
