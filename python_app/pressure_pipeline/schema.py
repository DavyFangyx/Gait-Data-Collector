from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import IntFlag
from pathlib import Path
import numpy as np

SCHEMA_VERSION = 1
PIPELINE_VERSION = "1.0.0"
METHODS = ("original", "spatial25", "replace4", "median3", "median5", "lowpass6", "lowpass10", "hampel5", "savgol5", "ema05")


class Flag(IntFlag):
    SOURCE_RETAINED = 1
    SPATIALLY_ESTIMATED = 2
    TEMPORALLY_FILTERED = 4
    UNUSABLE = 8
    ABOVE_MODEL_REFERENCE = 16
    SUSPECT_CHANNEL = 32
    PROCESSING_SKIPPED = 64
    DUPLICATE_RECEIVE_TIME = 128


@dataclass(frozen=True)
class Rules:
    long_gap_us: float = 300_000.0
    model_reference: float = 1023.0
    d7_cv_max: float = 0.4
    d7_run_min: int = 10
    d7_ratio_min: float = 3.0
    regular_tolerance: float = 0.02

    def validate(self):
        values = (self.long_gap_us, self.model_reference, self.d7_cv_max, self.d7_ratio_min, self.regular_tolerance)
        if not all(np.isfinite(x) and x > 0 for x in values) or self.d7_run_min < 1:
            raise ValueError("Quality thresholds must be finite and positive")


@dataclass(frozen=True)
class Profile:
    method: str = "original"
    # Physical CSV channel IDs, 1-based. Empty means no assumed faulty cells.
    suspect_left: tuple[int, ...] = ()
    suspect_right: tuple[int, ...] = ()

    def validate(self):
        if self.method not in METHODS:
            raise ValueError(f"Unknown method: {self.method}")
        for ids in (self.suspect_left, self.suspect_right):
            if len(ids) != len(set(ids)) or any(type(x) is not int or not 1 <= x <= 48 for x in ids):
                raise ValueError("Suspect cells must be unique integer IDs in 1..48")
            if self.method in ("spatial25", "replace4") and len(ids) > 43:
                raise ValueError("Spatial processing requires at least four non-suspect donor cells")

    @property
    def offline_future_samples(self):
        return self.method in ("median3", "median5", "lowpass6", "lowpass10", "hampel5", "savgol5")

    def to_dict(self):
        return asdict(self)


@dataclass
class SideData:
    side: str
    path: Path
    frame_ids: np.ndarray
    times_us: np.ndarray
    values: np.ndarray
    source_valid: np.ndarray
    source_sha256: str
    source_size: int


class Cancelled(Exception):
    pass
