from __future__ import annotations

import csv
import hashlib
import json
import os
from pathlib import Path
import tempfile
import numpy as np

from .schema import Cancelled, SideData


def check_cancel(cancel=None):
    if cancel is not None and cancel.is_set():
        raise Cancelled("Task cancelled")


def digest(path, cancel=None):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        while True:
            check_cancel(cancel)
            block = f.read(1024 * 1024)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def _clean_json(value):
    if isinstance(value, dict):
        return {str(k): _clean_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean_json(v) for v in value]
    if isinstance(value, np.generic):
        return _clean_json(value.item())
    if isinstance(value, float) and not np.isfinite(value):
        return None
    return value


def write_json(path, payload):
    path = Path(path)
    fd, tmp = tempfile.mkstemp(prefix=".writing_", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(_clean_json(payload), f, ensure_ascii=False, indent=2, allow_nan=False)
            f.write("\n")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def write_csv(path, rows, fields=None):
    rows = list(rows)
    fields = fields or list(dict.fromkeys(k for row in rows for k in row)) or ["status"]
    path = Path(path)
    fd, tmp = tempfile.mkstemp(prefix=".writing_", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            for row in rows:
                # CSV supports nan/inf literals; preserve them for numeric round trips.
                # JSON reports separately use null for nonfinite values.
                writer.writerow({k: v.item() if isinstance(v, np.generic) else v
                                 for k, v in row.items()})
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def read_side(path, side, cancel=None):
    path = Path(path)
    before = digest(path, cancel)
    ids, times, values, valid = [], [], [], []
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        required = ["frame_idx", "t_us"] + [str(x) for x in range(1, 49)]
        fields = reader.fieldnames or []
        if len(fields) != len(set(fields)) or not set(required) <= set(fields):
            raise ValueError("Expected unique frame_idx,t_us,1..48 columns")
        def numeric(value):
            try:
                return float(value)
            except (TypeError, ValueError):
                return float("nan")
        for index, row in enumerate(reader):
            if index % 1024 == 0:
                check_cancel(cancel)
            if None in row:
                raise ValueError(f"Extra CSV fields at data row {index}")
            ids.append(numeric(row.get("frame_idx")))
            times.append(numeric(row.get("t_us")))
            values.append([numeric(row.get(str(x))) for x in range(1, 49)])
            marker = str(row.get("valid_mask", "1")).strip().lower()
            valid.append(marker in ("1", "1.0", "true"))
    after = digest(path, cancel)
    if before != after:
        raise RuntimeError("Source changed during CSV scan")
    return SideData(side, path, np.asarray(ids), np.asarray(times),
                    np.asarray(values, dtype=float).reshape(-1, 48), np.asarray(valid, bool), before, path.stat().st_size)


def load_metadata(recording):
    path = Path(recording) / "meta.json"
    if not path.is_file():
        return {}, ("left", "right"), ["metadata_missing_expected_both_feet"]
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError("meta.json must be an object")
    pressure = payload.get("pressure")
    if not isinstance(pressure, list):
        return payload, ("left", "right"), ["pressure_inventory_unknown_expected_both_feet"]
    sides = []
    for item in pressure:
        if not isinstance(item, dict) or not isinstance(item.get("enabled", True), bool):
            raise ValueError("Invalid pressure inventory")
        if not item.get("enabled", True):
            continue
        side = {"left_foot": "left", "right_foot": "right"}.get(item.get("sensor_id"))
        if side is None or side in sides:
            raise ValueError("Unknown or duplicate enabled pressure sensor")
        sides.append(side)
    return payload, tuple(sides), []


def discover_recordings(root,cancel=None):
    root = Path(root).resolve(strict=True)
    if not root.is_dir():
        raise ValueError("Recording root must be a directory")
    if (root / "meta.json").is_file() or any((root / f"pressure_{s}.csv").exists() for s in ("left", "right")):
        return [root]
    found=set()
    for pattern in ("meta.json", "pressure_left.csv", "pressure_right.csv"):
        for path in root.rglob(pattern):
            check_cancel(cancel)
            if path.is_file(): found.add(path.parent)
    # meta-only recordings are included, so missing both CSVs cannot disappear.
    return sorted(found)
