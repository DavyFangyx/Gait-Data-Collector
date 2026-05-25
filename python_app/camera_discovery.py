from __future__ import annotations

from .mvs import MvsDeviceDescriptor, enumerate_gige_devices, runtime_diagnostics


CameraDescriptor = MvsDeviceDescriptor


def enumerate_camera_devices() -> list[CameraDescriptor]:
    diagnostics = runtime_diagnostics()
    if not diagnostics.available:
        return []
    return enumerate_gige_devices()

