from __future__ import annotations

import ctypes
import importlib
import os
import platform
import struct
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Optional

import numpy as np


_VENDOR_DIR = Path(__file__).resolve().parent / "mvs_vendor"
_SDK_LOCK = threading.RLock()
_ENUM_LOCK = threading.Lock()
_SDK_MODULES: SimpleNamespace | None = None
_SDK_IMPORT_ERROR: Exception | None = None
_SDK_INITIALIZED = False
_DLL_DIRECTORY_HANDLES: list[object] = []
_RUNTIME_ENV_CONFIGURED = False

_APP_ROOT = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[1]
_LOCAL_RUNTIME_DIR = _APP_ROOT / "runtime" / "mvs" / "Win64_x64"
_SYSTEM_RUNTIME_DIR = Path(r"C:\Program Files (x86)\Common Files\MVS\Runtime\Win64_x64")
_COMMON_RUNTIME_DLL = _SYSTEM_RUNTIME_DIR / "MvCameraControl.dll"


@dataclass(frozen=True)
class RuntimeDiagnostics:
    available: bool
    message: str
    sdk_version: str = ""


@dataclass(frozen=True)
class FloatNodeRange:
    key: str
    current: float
    minimum: float
    maximum: float
    available: bool = True


@dataclass(frozen=True)
class CameraParameterProfile:
    serial_number: str
    parameters: dict[str, FloatNodeRange]
    message: str = ""

    def get(self, key: str) -> FloatNodeRange | None:
        return self.parameters.get(key)


@dataclass
class MvsDeviceDescriptor:
    serial_number: str
    model_name: str
    user_defined_name: str
    ip_address: str
    accessible: bool
    transport: str
    raw_info: object | None = field(default=None, repr=False, compare=False)

    @property
    def name(self) -> str:
        return self.display_name

    @property
    def display_name(self) -> str:
        core = self.user_defined_name or self.model_name or "未命名工业相机"
        return core.strip()

    @property
    def summary(self) -> str:
        parts = [self.model_name or "未知型号"]
        if self.user_defined_name:
            parts.append(self.user_defined_name)
        if self.serial_number:
            parts.append(f"SN {self.serial_number}")
        if self.ip_address:
            parts.append(self.ip_address)
        return " | ".join(part for part in parts if part)

    @property
    def serial_tail(self) -> str:
        if not self.serial_number:
            return "-"
        return self.serial_number[-6:]


class MvsError(RuntimeError):
    def __init__(self, message: str, code: int | None = None) -> None:
        self.code = code
        detail = f"{message}（{ret_to_hex(code)}）" if code is not None else message
        super().__init__(detail)


def _ensure_vendor_path() -> None:
    vendor_path = str(_VENDOR_DIR)
    if vendor_path not in sys.path:
        sys.path.insert(0, vendor_path)


def _existing_runtime_directories() -> list[Path]:
    seen: set[str] = set()
    runtime_dirs: list[Path] = []
    for candidate in (_LOCAL_RUNTIME_DIR, _SYSTEM_RUNTIME_DIR):
        if not candidate.exists():
            continue
        for runtime_dir in (candidate, candidate / "ThirdParty"):
            if not runtime_dir.exists():
                continue
            key = str(runtime_dir).lower()
            if key in seen:
                continue
            runtime_dirs.append(runtime_dir)
            seen.add(key)
    return runtime_dirs


def _find_runtime_dll() -> Path | None:
    for runtime_dir in _existing_runtime_directories():
        dll_path = runtime_dir / "MvCameraControl.dll"
        if dll_path.exists():
            return dll_path
    return None


def _configure_runtime_environment() -> None:
    global _RUNTIME_ENV_CONFIGURED
    if _RUNTIME_ENV_CONFIGURED or os.name != "nt":
        return

    runtime_dirs = _existing_runtime_directories()
    if runtime_dirs:
        current_path = os.environ.get("PATH", "")
        path_entries = current_path.split(os.pathsep) if current_path else []
        lowered_entries = {entry.lower() for entry in path_entries}
        for runtime_dir in reversed(runtime_dirs):
            runtime_str = str(runtime_dir)
            if runtime_str.lower() not in lowered_entries:
                path_entries.insert(0, runtime_str)
                lowered_entries.add(runtime_str.lower())
            if hasattr(os, "add_dll_directory"):
                _DLL_DIRECTORY_HANDLES.append(os.add_dll_directory(runtime_str))
        os.environ["PATH"] = os.pathsep.join(path_entries)
    _RUNTIME_ENV_CONFIGURED = True


def _load_sdk_modules() -> SimpleNamespace:
    global _SDK_MODULES, _SDK_IMPORT_ERROR
    if _SDK_MODULES is not None:
        return _SDK_MODULES
    if _SDK_IMPORT_ERROR is not None:
        raise _SDK_IMPORT_ERROR

    _configure_runtime_environment()
    _ensure_vendor_path()
    try:
        _SDK_MODULES = SimpleNamespace(
            camera=importlib.import_module("MvCameraControl_class"),
            header=importlib.import_module("CameraParams_header"),
            const=importlib.import_module("CameraParams_const"),
            pixel=importlib.import_module("PixelType_header"),
            error=importlib.import_module("MvErrorDefine_const"),
        )
        return _SDK_MODULES
    except Exception as exc:  # pragma: no cover - 取决于本机 SDK 安装
        _SDK_IMPORT_ERROR = exc
        raise


def runtime_diagnostics() -> RuntimeDiagnostics:
    if os.name != "nt":
        return RuntimeDiagnostics(False, "当前工业相机后端仅支持 Windows。")
    if struct.calcsize("P") * 8 != 64:
        return RuntimeDiagnostics(False, "工业相机后端仅支持 64 位 Python。")
    try:
        modules = _load_sdk_modules()
    except OSError as exc:  # pragma: no cover - 依赖本机环境
        if _COMMON_RUNTIME_DLL.exists():
            return RuntimeDiagnostics(False, f"MVS Runtime 已安装，但无法加载 DLL：{exc}")
        return RuntimeDiagnostics(
            False,
            "未找到 MVS Runtime。请确认已安装海康 MVS 客户端，并将 Runtime\\Win64_x64 加入 PATH。",
        )
    except Exception as exc:  # pragma: no cover - 依赖本机环境
        return RuntimeDiagnostics(False, f"MVS SDK 初始化失败：{exc}")

    try:
        version_value = modules.camera.MvCamera.MV_CC_GetSDKVersion()
        return RuntimeDiagnostics(True, "MVS SDK 已就绪。", _format_sdk_version(version_value))
    except Exception:  # pragma: no cover - 版本读取失败不阻断
        return RuntimeDiagnostics(True, "MVS SDK 已就绪。")


def runtime_diagnostics() -> RuntimeDiagnostics:
    if os.name != "nt":
        return RuntimeDiagnostics(False, "当前工业相机后端仅支持 Windows。")
    if struct.calcsize("P") * 8 != 64:
        return RuntimeDiagnostics(False, "工业相机后端仅支持 64 位 Python。")

    runtime_dll = _find_runtime_dll()
    try:
        modules = _load_sdk_modules()
    except OSError as exc:  # pragma: no cover - depends on local SDK setup
        if runtime_dll is not None:
            return RuntimeDiagnostics(False, f"MVS Runtime 已发现，但无法加载 DLL：{runtime_dll.parent}，{exc}")
        return RuntimeDiagnostics(
            False,
            "未找到 MVS Runtime。请安装海康 MVS Runtime / Client，或将 Runtime\\Win64_x64 复制到应用目录 runtime\\mvs\\Win64_x64。",
        )
    except Exception as exc:  # pragma: no cover - depends on local SDK setup
        return RuntimeDiagnostics(False, f"MVS SDK 初始化失败：{exc}")

    try:
        version_value = modules.camera.MvCamera.MV_CC_GetSDKVersion()
        return RuntimeDiagnostics(True, "MVS SDK 已就绪。", _format_sdk_version(version_value))
    except Exception:  # pragma: no cover - version read failure should not block startup
        return RuntimeDiagnostics(True, "MVS SDK 已就绪。")


def initialize_sdk() -> None:
    global _SDK_INITIALIZED
    diagnostics = runtime_diagnostics()
    if not diagnostics.available:
        raise MvsError(diagnostics.message)

    with _SDK_LOCK:
        if _SDK_INITIALIZED:
            return
        modules = _load_sdk_modules()
        ret = modules.camera.MvCamera.MV_CC_Initialize()
        if ret != modules.error.MV_OK:
            raise MvsError("初始化 MVS SDK 失败", ret)
        _SDK_INITIALIZED = True


def finalize_sdk() -> None:
    global _SDK_INITIALIZED
    with _SDK_LOCK:
        if not _SDK_INITIALIZED:
            return
        modules = _load_sdk_modules()
        modules.camera.MvCamera.MV_CC_Finalize()
        _SDK_INITIALIZED = False


def ret_to_hex(code: int | None) -> str:
    if code is None:
        return ""
    value = int(code)
    if value < 0:
        value += 2**32
    return f"0x{value:08X}"


def decode_char_array(value) -> str:
    raw = memoryview(value).tobytes()
    raw = raw.split(b"\x00", 1)[0]
    for encoding in ("utf-8", "gbk", "latin-1"):
        try:
            return raw.decode(encoding).strip()
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", errors="replace").strip()


def ip_from_uint(value: int) -> str:
    if not value:
        return ""
    return ".".join(str((value >> shift) & 0xFF) for shift in (24, 16, 8, 0))


def enumerate_gige_devices() -> list[MvsDeviceDescriptor]:
    diagnostics = runtime_diagnostics()
    if not diagnostics.available:
        return []

    initialize_sdk()
    modules = _load_sdk_modules()
    with _ENUM_LOCK:
        device_list = modules.header.MV_CC_DEVICE_INFO_LIST()
        tlayer = modules.const.MV_GIGE_DEVICE | modules.const.MV_GENTL_GIGE_DEVICE
        sort_method = getattr(modules.header, "SortMethod_SerialNumber", 0)
        ret = modules.camera.MvCamera.MV_CC_EnumDevicesEx2(tlayer, device_list, "", sort_method)
        if ret != modules.error.MV_OK:
            raise MvsError("枚举工业相机失败", ret)

        descriptors: list[MvsDeviceDescriptor] = []
        for index in range(device_list.nDeviceNum):
            raw = ctypes.cast(device_list.pDeviceInfo[index], ctypes.POINTER(modules.header.MV_CC_DEVICE_INFO)).contents
            raw_copy = modules.header.MV_CC_DEVICE_INFO()
            ctypes.memmove(ctypes.byref(raw_copy), ctypes.byref(raw), ctypes.sizeof(modules.header.MV_CC_DEVICE_INFO))

            if raw_copy.nTLayerType not in (modules.const.MV_GIGE_DEVICE, modules.const.MV_GENTL_GIGE_DEVICE):
                continue

            info = raw_copy.SpecialInfo.stGigEInfo
            descriptors.append(
                MvsDeviceDescriptor(
                    serial_number=decode_char_array(info.chSerialNumber),
                    model_name=decode_char_array(info.chModelName),
                    user_defined_name=decode_char_array(info.chUserDefinedName),
                    ip_address=ip_from_uint(info.nCurrentIp),
                    accessible=bool(modules.camera.MvCamera.MV_CC_IsDeviceAccessible(raw_copy, modules.const.MV_ACCESS_Exclusive)),
                    transport="GigE",
                    raw_info=raw_copy,
                )
            )
        descriptors.sort(key=lambda item: (item.serial_number or "~", item.ip_address))
        return descriptors


def is_hb_pixel_format(pixel_type: int) -> bool:
    modules = _load_sdk_modules()
    pixel = modules.pixel
    return pixel_type in {
        pixel.PixelType_Gvsp_HB_Mono8,
        pixel.PixelType_Gvsp_HB_Mono10,
        pixel.PixelType_Gvsp_HB_Mono10_Packed,
        pixel.PixelType_Gvsp_HB_Mono12,
        pixel.PixelType_Gvsp_HB_Mono12_Packed,
        pixel.PixelType_Gvsp_HB_Mono16,
        pixel.PixelType_Gvsp_HB_BayerGR8,
        pixel.PixelType_Gvsp_HB_BayerRG8,
        pixel.PixelType_Gvsp_HB_BayerGB8,
        pixel.PixelType_Gvsp_HB_BayerBG8,
        pixel.PixelType_Gvsp_HB_BayerRBGG8,
        pixel.PixelType_Gvsp_HB_BayerGR10,
        pixel.PixelType_Gvsp_HB_BayerRG10,
        pixel.PixelType_Gvsp_HB_BayerGB10,
        pixel.PixelType_Gvsp_HB_BayerBG10,
        pixel.PixelType_Gvsp_HB_BayerGR12,
        pixel.PixelType_Gvsp_HB_BayerRG12,
        pixel.PixelType_Gvsp_HB_BayerGB12,
        pixel.PixelType_Gvsp_HB_BayerBG12,
        pixel.PixelType_Gvsp_HB_BayerGR10_Packed,
        pixel.PixelType_Gvsp_HB_BayerRG10_Packed,
        pixel.PixelType_Gvsp_HB_BayerGB10_Packed,
        pixel.PixelType_Gvsp_HB_BayerBG10_Packed,
        pixel.PixelType_Gvsp_HB_BayerGR12_Packed,
        pixel.PixelType_Gvsp_HB_BayerRG12_Packed,
        pixel.PixelType_Gvsp_HB_BayerGB12_Packed,
        pixel.PixelType_Gvsp_HB_BayerBG12_Packed,
        pixel.PixelType_Gvsp_HB_YUV422_Packed,
        pixel.PixelType_Gvsp_HB_YUV422_YUYV_Packed,
        pixel.PixelType_Gvsp_HB_RGB8_Packed,
        pixel.PixelType_Gvsp_HB_BGR8_Packed,
        pixel.PixelType_Gvsp_HB_RGBA8_Packed,
        pixel.PixelType_Gvsp_HB_BGRA8_Packed,
        pixel.PixelType_Gvsp_HB_RGB16_Packed,
        pixel.PixelType_Gvsp_HB_BGR16_Packed,
        pixel.PixelType_Gvsp_HB_RGBA16_Packed,
        pixel.PixelType_Gvsp_HB_BGRA16_Packed,
    }


def is_mono_pixel_format(pixel_type: int) -> bool:
    modules = _load_sdk_modules()
    pixel = modules.pixel
    return pixel_type in {
        pixel.PixelType_Gvsp_Mono8,
        pixel.PixelType_Gvsp_Mono10,
        pixel.PixelType_Gvsp_Mono10_Packed,
        pixel.PixelType_Gvsp_Mono12,
        pixel.PixelType_Gvsp_Mono12_Packed,
        pixel.PixelType_Gvsp_Mono14,
        pixel.PixelType_Gvsp_Mono16,
    }


class MvsCameraDevice:
    def __init__(self, descriptor: MvsDeviceDescriptor) -> None:
        self.descriptor = descriptor
        self._camera = None
        self._modules = None
        self._is_open = False
        self._is_grabbing = False
        self._software_trigger = False
        self._exposure_time_us: float | None = None
        self._acquisition_frame_rate_fps: float | None = None
        self._gain_db: float | None = None
        self._gamma_value: float | None = None
        self._black_level: float | None = None

    def open(
        self,
        exposure_time_us: float | None = None,
        acquisition_frame_rate_fps: float | None = None,
        gain_db: float | None = None,
        gamma_value: float | None = None,
        black_level: float | None = None,
    ) -> None:
        if exposure_time_us is not None:
            self._exposure_time_us = float(exposure_time_us)
        if acquisition_frame_rate_fps is not None:
            self._acquisition_frame_rate_fps = float(acquisition_frame_rate_fps)
        if gain_db is not None:
            self._gain_db = float(gain_db)
        if gamma_value is not None:
            self._gamma_value = float(gamma_value)
        if black_level is not None:
            self._black_level = float(black_level)
        initialize_sdk()
        self._modules = _load_sdk_modules()
        header = self._modules.header
        const = self._modules.const
        error = self._modules.error

        if self.descriptor.raw_info is None:
            raise MvsError("当前设备信息无效，请重新刷新设备列表。")

        self._camera = self._modules.camera.MvCamera()
        dev_info = header.MV_CC_DEVICE_INFO()
        ctypes.memmove(ctypes.byref(dev_info), ctypes.byref(self.descriptor.raw_info), ctypes.sizeof(header.MV_CC_DEVICE_INFO))

        ret = self._camera.MV_CC_CreateHandle(dev_info)
        if ret != error.MV_OK:
            raise MvsError(f"创建设备句柄失败：{self.descriptor.serial_number}", ret)

        try:
            ret = self._camera.MV_CC_OpenDevice(const.MV_ACCESS_Exclusive, 0)
            if ret != error.MV_OK:
                raise MvsError(f"打开设备失败：{self.descriptor.serial_number}", ret)
            self._is_open = True

            if dev_info.nTLayerType in (const.MV_GIGE_DEVICE, const.MV_GENTL_GIGE_DEVICE):
                packet_size = self._camera.MV_CC_GetOptimalPacketSize()
                if int(packet_size) > 0:
                    ret = self._camera.MV_CC_SetIntValue("GevSCPSPacketSize", int(packet_size))
                    if ret != error.MV_OK:
                        raise MvsError(f"设置最优网络包长失败：{self.descriptor.serial_number}", ret)

            self.apply_runtime_parameters(
                exposure_time_us=self._exposure_time_us,
                acquisition_frame_rate_fps=self._acquisition_frame_rate_fps,
                gain_db=self._gain_db,
                gamma_value=self._gamma_value,
                black_level=self._black_level,
            )
            self.set_trigger_mode(False)
            ret = self._camera.MV_CC_StartGrabbing()
            if ret != error.MV_OK:
                raise MvsError(f"开始采流失败：{self.descriptor.serial_number}", ret)
            self._is_grabbing = True
        except Exception:
            self.close()
            raise

    def close(self) -> None:
        if self._camera is None:
            return
        try:
            if self._is_grabbing:
                self._camera.MV_CC_StopGrabbing()
        finally:
            self._is_grabbing = False
        try:
            if self._is_open:
                self._camera.MV_CC_CloseDevice()
        finally:
            self._is_open = False
        try:
            self._camera.MV_CC_DestroyHandle()
        finally:
            self._camera = None

    def restart(self) -> None:
        self.close()
        self.open(
            exposure_time_us=self._exposure_time_us,
            acquisition_frame_rate_fps=self._acquisition_frame_rate_fps,
            gain_db=self._gain_db,
            gamma_value=self._gamma_value,
            black_level=self._black_level,
        )

    def read_float_value(self, key: str) -> tuple[float, float, float]:
        if self._camera is None:
            raise MvsError("相机尚未打开，无法读取参数。")
        value = self._modules.header.MVCC_FLOATVALUE()
        ctypes.memset(ctypes.byref(value), 0, ctypes.sizeof(self._modules.header.MVCC_FLOATVALUE))
        ret = self._camera.MV_CC_GetFloatValue(key, value)
        if ret != self._modules.error.MV_OK:
            raise MvsError(f"读取参数 {key} 失败：{self.descriptor.serial_number}", ret)
        return float(value.fCurValue), float(value.fMin), float(value.fMax)

    def read_optional_float_value(self, key: str) -> FloatNodeRange | None:
        try:
            current, minimum, maximum = self.read_float_value(key)
        except MvsError:
            return None
        return FloatNodeRange(
            key=key,
            current=current,
            minimum=minimum,
            maximum=maximum,
            available=True,
        )

    def set_float_value(self, key: str, value: float) -> float:
        _current, minimum, maximum = self.read_float_value(key)
        clamped = min(max(float(value), minimum), maximum)
        ret = self._camera.MV_CC_SetFloatValue(key, clamped)
        if ret != self._modules.error.MV_OK:
            raise MvsError(f"设置参数 {key} 失败：{self.descriptor.serial_number}", ret)
        return clamped

    def _try_set_bool_value(self, key: str, value: bool) -> None:
        if self._camera is None:
            return
        self._camera.MV_CC_SetBoolValue(key, value)

    def _try_set_enum_value(self, key: str, value: int) -> None:
        if self._camera is None:
            return
        self._camera.MV_CC_SetEnumValue(key, value)

    def _try_set_float_value(self, key: str, value: float) -> float | None:
        try:
            return self.set_float_value(key, value)
        except MvsError:
            return None

    def apply_runtime_parameters(
        self,
        exposure_time_us: float | None = None,
        acquisition_frame_rate_fps: float | None = None,
        gain_db: float | None = None,
        gamma_value: float | None = None,
        black_level: float | None = None,
    ) -> None:
        if self._camera is None:
            raise MvsError("相机尚未打开，无法设置参数。")

        if exposure_time_us is not None:
            self._try_set_enum_value("ExposureAuto", 0)
            self._exposure_time_us = self.set_float_value("ExposureTime", exposure_time_us)

        if gain_db is not None:
            self._try_set_enum_value("GainAuto", 0)
            self._gain_db = self.set_float_value("Gain", gain_db)

        if acquisition_frame_rate_fps is not None:
            self._try_set_bool_value("AcquisitionFrameRateEnable", True)
            self._acquisition_frame_rate_fps = self.set_float_value(
                "AcquisitionFrameRate",
                acquisition_frame_rate_fps,
            )

        if gamma_value is not None:
            self._try_set_bool_value("GammaEnable", True)
            value = self._try_set_float_value("Gamma", gamma_value)
            if value is not None:
                self._gamma_value = value

        if black_level is not None:
            value = self._try_set_float_value("BlackLevel", black_level)
            if value is not None:
                self._black_level = value

    def parameter_profile(self) -> CameraParameterProfile:
        parameters: dict[str, FloatNodeRange] = {}
        for key in ("ExposureTime", "AcquisitionFrameRate", "Gain", "Gamma", "BlackLevel"):
            item = self.read_optional_float_value(key)
            if item is not None:
                parameters[key] = item
        return CameraParameterProfile(
            serial_number=self.descriptor.serial_number,
            parameters=parameters,
        )

    def set_trigger_mode(self, software_trigger: bool) -> None:
        if self._camera is None:
            raise MvsError("相机尚未打开，无法设置触发模式。")
        mode = "On" if software_trigger else "Off"
        ret = self._camera.MV_CC_SetEnumValueByString("TriggerMode", mode)
        if ret != self._modules.error.MV_OK:
            raise MvsError(f"设置触发模式失败：{self.descriptor.serial_number}", ret)
        if software_trigger:
            ret = self._camera.MV_CC_SetEnumValueByString("TriggerSource", "Software")
            if ret != self._modules.error.MV_OK:
                raise MvsError(f"设置软触发源失败：{self.descriptor.serial_number}", ret)
        self._software_trigger = software_trigger

    def trigger_once(self) -> None:
        if self._camera is None:
            raise MvsError("相机尚未打开，无法软触发。")
        ret = self._camera.MV_CC_SetCommandValue("TriggerSoftware")
        if ret != self._modules.error.MV_OK:
            raise MvsError(f"执行软触发失败：{self.descriptor.serial_number}", ret)

    def read_frame(self, timeout_ms: int = 200) -> Optional[tuple[np.ndarray, dict]]:
        if self._camera is None:
            raise MvsError("相机尚未打开，无法读取图像。")

        header = self._modules.header
        error = self._modules.error
        frame_out = header.MV_FRAME_OUT()
        ctypes.memset(ctypes.byref(frame_out), 0, ctypes.sizeof(header.MV_FRAME_OUT))
        ret = self._camera.MV_CC_GetImageBuffer(frame_out, timeout_ms)
        if ret == error.MV_E_NODATA:
            return None
        if ret != error.MV_OK:
            raise MvsError(f"读取图像失败：{self.descriptor.serial_number}", ret)

        try:
            image = self._convert_to_bgr(frame_out)
            info = frame_out.stFrameInfo
            width = int(info.nExtendWidth or info.nWidth)
            height = int(info.nExtendHeight or info.nHeight)
            timestamp = (int(info.nDevTimeStampHigh) << 32) | int(info.nDevTimeStampLow)
            return image, {
                "frame_number": int(info.nFrameNum),
                "device_timestamp": timestamp,
                "width": width,
                "height": height,
                "pixel_type": int(info.enPixelType),
            }
        finally:
            free_ret = self._camera.MV_CC_FreeImageBuffer(frame_out)
            if free_ret != error.MV_OK:
                raise MvsError(f"释放图像缓存失败：{self.descriptor.serial_number}", free_ret)

    def _convert_to_bgr(self, frame_out) -> np.ndarray:
        header = self._modules.header
        pixel = self._modules.pixel
        error = self._modules.error
        info = frame_out.stFrameInfo
        width = int(info.nExtendWidth or info.nWidth)
        height = int(info.nExtendHeight or info.nHeight)
        src_pixel_type = int(info.enPixelType)
        src_ptr = frame_out.pBufAddr
        src_len = int(info.nFrameLen)
        decode_buffer = None

        if is_hb_pixel_format(src_pixel_type):
            decode_buffer_size = max(src_len * 4, width * height * 4)
            decode_buffer = (ctypes.c_ubyte * decode_buffer_size)()
            decode_param = header.MV_CC_HB_DECODE_PARAM()
            ctypes.memset(ctypes.byref(decode_param), 0, ctypes.sizeof(header.MV_CC_HB_DECODE_PARAM))
            decode_param.pSrcBuf = frame_out.pBufAddr
            decode_param.nSrcLen = src_len
            decode_param.pDstBuf = decode_buffer
            decode_param.nDstBufSize = decode_buffer_size
            ret = self._camera.MV_CC_HBDecode(decode_param)
            if ret != error.MV_OK:
                raise MvsError(f"HB 图像解码失败：{self.descriptor.serial_number}", ret)
            src_ptr = ctypes.cast(decode_buffer, ctypes.POINTER(ctypes.c_ubyte))
            src_len = int(decode_param.nDstBufLen)
            src_pixel_type = int(decode_param.enDstPixelType)

        is_mono = is_mono_pixel_format(src_pixel_type)
        channels = 1 if is_mono else 3
        dst_pixel_type = pixel.PixelType_Gvsp_Mono8 if is_mono else pixel.PixelType_Gvsp_BGR8_Packed
        dst_len = width * height * channels
        dst_buffer = (ctypes.c_ubyte * dst_len)()

        convert_param = header.MV_CC_PIXEL_CONVERT_PARAM_EX()
        ctypes.memset(ctypes.byref(convert_param), 0, ctypes.sizeof(header.MV_CC_PIXEL_CONVERT_PARAM_EX))
        convert_param.nWidth = width
        convert_param.nHeight = height
        convert_param.enSrcPixelType = src_pixel_type
        convert_param.pSrcData = src_ptr
        convert_param.nSrcDataLen = src_len
        convert_param.enDstPixelType = dst_pixel_type
        convert_param.pDstBuffer = ctypes.cast(dst_buffer, ctypes.POINTER(ctypes.c_ubyte))
        convert_param.nDstBufferSize = dst_len
        ret = self._camera.MV_CC_ConvertPixelTypeEx(convert_param)
        if ret != error.MV_OK:
            raise MvsError(f"图像像素格式转换失败：{self.descriptor.serial_number}", ret)

        image = np.frombuffer(dst_buffer, dtype=np.uint8, count=dst_len)
        if is_mono:
            mono = image.reshape(height, width)
            return np.repeat(mono[:, :, None], 3, axis=2)
        return image.reshape(height, width, 3)


def probe_camera_parameter_profile(descriptor: MvsDeviceDescriptor) -> CameraParameterProfile | None:
    if not descriptor.accessible:
        return None
    device = MvsCameraDevice(descriptor)
    try:
        device.open()
        return device.parameter_profile()
    except Exception:
        return None
    finally:
        device.close()


def _format_sdk_version(value: int) -> str:
    return f"V{(value >> 24) & 0xFF}.{(value >> 16) & 0xFF}.{(value >> 8) & 0xFF}.{value & 0xFF}"
