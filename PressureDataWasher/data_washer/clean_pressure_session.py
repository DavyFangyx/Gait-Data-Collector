"""触觉压力 CSV 清洗：以参考相机 JPG 时间戳为时间基，按心跳做 RLE 去重和定长槽位重采样。

输入  session 目录下的 Pressure_{left,right}_*.csv 和 `1/` 参考相机 JPG 序列
输出  OUTPUT_ROOT_DIR/<session_name>/ 下的 pressure_{side}.parquet 与 log.json

python data_washer/clean_pressure_session.py

"""
from __future__ import annotations
import argparse
import json
import re
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

# User config ---------------------------------------------------------------
DEFAULT_INPUT_PATHS = [
    Path(r"F:\3_Experiment_Data\FourVideosData\Experiment\S1"),
    Path(r"F:\3_Experiment_Data\FourVideosData\Experiment\S2"),
    Path(r"F:\3_Experiment_Data\FourVideosData\Experiment\S3"),
    Path(r"F:\3_Experiment_Data\FourVideosData\Experiment\S4"),
]
DEFAULT_RECURSIVE = True

VIDEO_SUBDIR_NAME = "1"
SCRIPT_DIR = Path(__file__).resolve().parent
OUTPUT_ROOT_DIR = SCRIPT_DIR / "pressure_cleaned"
LOG_FILE_NAME = "log.json"

PRESSURE_SIDES = ("left", "right")
PRESSURE_INPUT_PATTERN = "Pressure_{side}_*.csv"
PRESSURE_OUTPUT_FILE = "pressure_{side}.parquet"

CSV_ENCODING = "utf-8-sig"
PARQUET_ENGINE = "pyarrow"

# JPG 文件名时间戳解析 -------------------------------------------------------
REFERENCE_IMAGE_EXTENSIONS = {".jpg", ".jpeg"}
JPG_TIME_PATTERN = r"(\d{6})(?:\.(\d+))?(?=\.jpe?g$)"   # 文件名末尾的 HHMMSS[.fff]
HHMMSS_PATTERN = r"(\d{2})(\d{2})(\d{2})(?:\.(\d+))?"

MIN_REFERENCE_FRAME_COUNT = 2                  # 至少 2 张 JPG 才能估 video_fps
MIDNIGHT_WRAP_THRESHOLD_SECONDS = 12 * 3600    # 时间倒退超过此值视作跨午夜
SECONDS_PER_DAY = 24 * 3600
RELATIVE_TIME_EXTRA_TOLERANCE_SECONDS = 5.0    # captured_at 视作相对秒时允许的尾部余量

# 清洗规则 -----------------------------------------------------------------
RLE_DEDUP_MIN_RUN_LENGTH = 3                   # 非零行连续相同 ≥ 该值，仅保留首行
DROP_LOW_MIN_EFFECTIVE_COUNT = 5.0             # 心跳每秒有效样本数 < 该值直接丢弃
EMPTY_MASK_VALUE = 0                           # zoh_empty 槽位的 mask
KEEP_MASK_VALUE = 1                            # keep 槽位的 mask

HEARTBEAT_ID_COL = "heartbeat_id"
CAPTURED_AT_COL = "captured_at"
FRAME_INDEX_COL = "frame_index"
SLOT_IDX_COL = "slot_idx"
T_VISUAL_COL = "t_visual"
MASK_COL = "mask"


JPG_TIME_RE = re.compile(JPG_TIME_PATTERN, re.IGNORECASE)


def parse_hhmmss_seconds(text: str) -> float:
    match = re.fullmatch(HHMMSS_PATTERN, text)
    if not match:
        raise ValueError(f"Bad HHMMSS timestamp: {text!r}")
    hour = int(match.group(1))
    minute = int(match.group(2))
    second = int(match.group(3))
    frac = match.group(4) or ""
    if hour > 23 or minute > 59 or second > 59:
        raise ValueError(f"Out-of-range HHMMSS timestamp: {text!r}")
    return hour * 3600.0 + minute * 60.0 + second + (float(f"0.{frac}") if frac else 0.0)


def parse_frame_times(video_dir: Path) -> tuple[np.ndarray, float]:
    if not video_dir.is_dir():
        raise FileNotFoundError(f"Reference camera directory not found: {video_dir}")

    parsed: list[tuple[str, float]] = []
    for path in sorted(video_dir.iterdir(), key=lambda p: p.name):
        if not path.is_file() or path.suffix.lower() not in REFERENCE_IMAGE_EXTENSIONS:
            continue
        match = JPG_TIME_RE.search(path.name)
        if not match:
            continue
        timestamp = match.group(1) + (f".{match.group(2)}" if match.group(2) else "")
        parsed.append((path.name, parse_hhmmss_seconds(timestamp)))

    if len(parsed) < MIN_REFERENCE_FRAME_COUNT:
        raise ValueError(f"Need at least two timestamped JPG files in {video_dir}")

    unwrapped: list[float] = []
    day_offset = 0.0
    prev = parsed[0][1]
    for _, value in parsed:
        if value + day_offset < prev - MIDNIGHT_WRAP_THRESHOLD_SECONDS:
            day_offset += SECONDS_PER_DAY
        current = value + day_offset
        unwrapped.append(current)
        prev = current

    absolute = np.asarray(unwrapped, dtype=np.float64)
    if not np.all(np.diff(absolute) > 0):
        raise ValueError(f"Frame timestamps are not strictly increasing in {video_dir}")
    return absolute - absolute[0], float(absolute[0])


def numeric_hhmmss_to_seconds(values: np.ndarray) -> np.ndarray | None:
    out = np.empty(values.shape[0], dtype=np.float64)
    for idx, value in enumerate(values):
        if not np.isfinite(value):
            return None
        text = f"{value:.9f}".rstrip("0").rstrip(".")
        integer, _, frac = text.partition(".")
        integer = integer.zfill(6)
        if len(integer) != 6:
            return None
        try:
            out[idx] = parse_hhmmss_seconds(integer + (f".{frac}" if frac else ""))
        except ValueError:
            return None
    return out


def coerce_captured_at(series: pd.Series, frame_times: np.ndarray, first_frame_seconds: float) -> tuple[np.ndarray, str]:
    numeric = pd.to_numeric(series, errors="coerce")
    if numeric.notna().all():
        values = numeric.to_numpy(dtype=np.float64)
        duration = float(frame_times[-1])
        if np.nanmin(values) >= -1e-6 and np.nanmax(values) <= duration + RELATIVE_TIME_EXTRA_TOLERANCE_SECONDS:
            return values, "captured_at_relative_seconds"

        hhmmss = numeric_hhmmss_to_seconds(values)
        if hhmmss is not None and np.nanmin(hhmmss) >= 0:
            return hhmmss - first_frame_seconds, "captured_at_hhmmss"

        if np.nanmin(values) >= 0.0 and np.nanmax(values) < SECONDS_PER_DAY:
            return values - first_frame_seconds, "captured_at_seconds_of_day"

        return values, "captured_at_numeric_as_is"

    parsed = pd.to_datetime(series, errors="coerce")
    if parsed.notna().all():
        seconds = (
            parsed.dt.hour.to_numpy(dtype=np.float64) * 3600.0
            + parsed.dt.minute.to_numpy(dtype=np.float64) * 60.0
            + parsed.dt.second.to_numpy(dtype=np.float64)
            + parsed.dt.microsecond.to_numpy(dtype=np.float64) / 1_000_000.0
        )
        return seconds - first_frame_seconds, "captured_at_datetime_time_of_day"

    raise ValueError("captured_at cannot be converted to numeric seconds or datetimes")


def captured_at_from_frame_index(series: pd.Series, frame_times: np.ndarray) -> tuple[np.ndarray, str]:
    frame_index = pd.to_numeric(series, errors="coerce")
    if frame_index.isna().any():
        bad = int(frame_index.isna().sum())
        raise ValueError(f"frame_index contains {bad} non-numeric values")
    positions = frame_index.to_numpy(dtype=np.float64)
    x = np.arange(frame_times.shape[0], dtype=np.float64)
    return np.interp(positions, x, frame_times, left=frame_times[0], right=frame_times[-1]), "frame_index_to_reference_frame_time"


def recompute_heartbeat_ids(captured_at: np.ndarray, frame_times: np.ndarray, frame_span: int) -> tuple[np.ndarray, np.ndarray]:
    frame_positions = np.searchsorted(frame_times, captured_at, side="right") - 1
    valid = frame_positions >= 0
    hb_ids = np.full(captured_at.shape[0], -1, dtype=np.int64)
    hb_ids[valid] = frame_positions[valid] // frame_span
    return hb_ids, valid


def dedupe_nonzero_rle(df: pd.DataFrame, channel_cols: list[str]) -> tuple[pd.DataFrame, int]:
    if df.empty:
        return df.copy(), 0

    channel_values = df[channel_cols].apply(pd.to_numeric, errors="coerce").fillna(0.0)
    signatures = pd.util.hash_pandas_object(channel_values, index=False).to_numpy()
    nonzero = np.any(channel_values.to_numpy(dtype=np.float64) != 0.0, axis=1)

    keep = np.ones(df.shape[0], dtype=bool)
    start = 0
    while start < df.shape[0]:
        end = start + 1
        while end < df.shape[0] and signatures[end] == signatures[start]:
            end += 1
        run_len = end - start
        if nonzero[start] and run_len >= RLE_DEDUP_MIN_RUN_LENGTH:
            keep[start + 1 : end] = False
        start = end

    return df.loc[keep].copy(), int((~keep).sum())


def empty_output_frame(channel_cols: list[str]) -> pd.DataFrame:
    data: dict[str, pd.Series] = {
        HEARTBEAT_ID_COL: pd.Series(dtype="int64"),
        SLOT_IDX_COL: pd.Series(dtype="int64"),
        T_VISUAL_COL: pd.Series(dtype="float64"),
    }
    for col in channel_cols:
        data[col] = pd.Series(dtype="float64")
    data[MASK_COL] = pd.Series(dtype="uint8")
    return pd.DataFrame(data)


def make_slot_frame(
    hb_id: int,
    slot_times: np.ndarray,
    values: np.ndarray,
    channel_cols: list[str],
    mask: int,
) -> pd.DataFrame:
    out = pd.DataFrame(
        {
            HEARTBEAT_ID_COL: np.full(slot_times.shape[0], hb_id, dtype=np.int64),
            SLOT_IDX_COL: np.arange(slot_times.shape[0], dtype=np.int64),
            T_VISUAL_COL: slot_times.astype(np.float64, copy=False),
        }
    )
    for idx, col in enumerate(channel_cols):
        out[col] = values[:, idx].astype(np.float64, copy=False)
    out[MASK_COL] = np.full(slot_times.shape[0], mask, dtype=np.uint8)
    return out


def process_pressure_csv(
    csv_path: Path,
    out_path: Path,
    frame_times: np.ndarray,
    first_frame_seconds: float,
    video_fps: float,
    frame_span: int,
) -> dict[str, object]:
    raw = pd.read_csv(csv_path, encoding=CSV_ENCODING)
    original_rows = int(raw.shape[0])

    lower_cols = {col.lower(): col for col in raw.columns}
    if CAPTURED_AT_COL in lower_cols:
        captured_at, time_source = coerce_captured_at(raw[lower_cols[CAPTURED_AT_COL]], frame_times, first_frame_seconds)
        meta_cols = {lower_cols.get(HEARTBEAT_ID_COL), lower_cols[CAPTURED_AT_COL], lower_cols.get(FRAME_INDEX_COL)}
    elif FRAME_INDEX_COL in lower_cols:
        captured_at, time_source = captured_at_from_frame_index(raw[lower_cols[FRAME_INDEX_COL]], frame_times)
        meta_cols = {lower_cols.get(HEARTBEAT_ID_COL), lower_cols[FRAME_INDEX_COL]}
    else:
        raise ValueError(f"{csv_path} has neither captured_at nor frame_index")

    channel_cols = [col for col in raw.columns if col not in meta_cols and col is not None]
    if not channel_cols:
        raise ValueError(f"No pressure channel columns found in {csv_path}")

    hb_ids, in_frame = recompute_heartbeat_ids(captured_at, frame_times, frame_span)
    work = raw.loc[in_frame, channel_cols].copy()
    work.insert(0, CAPTURED_AT_COL, captured_at[in_frame])
    work.insert(0, HEARTBEAT_ID_COL, hb_ids[in_frame])
    work = work.sort_values("captured_at", kind="mergesort").reset_index(drop=True)

    for col in channel_cols:
        work[col] = pd.to_numeric(work[col], errors="coerce").fillna(0.0).astype(np.float64)

    deduped, dedup_deleted = dedupe_nonzero_rle(work, channel_cols)
    deduped = deduped.sort_values("captured_at", kind="mergesort").reset_index(drop=True)

    counters = {"keep": 0, "drop_low": 0, "zoh_empty": 0}
    zoh = np.zeros(len(channel_cols), dtype=np.float64)
    slot_frames: list[pd.DataFrame] = []

    max_observed_hb_id = int(deduped["heartbeat_id"].max()) if not deduped.empty else -1
    grouped = {int(hb): group for hb, group in deduped.groupby("heartbeat_id", sort=False)}

    for hb_id in range(max_observed_hb_id + 1):
        rows = grouped.get(hb_id)
        count = 0 if rows is None else int(rows.shape[0])
        is_edge = hb_id == 0 or hb_id == max_observed_hb_id
        hb_start = float(frame_times[min(hb_id * frame_span, len(frame_times) - 1)])

        if is_edge and count > 0:
            actual_dur = float(rows["captured_at"].max()) - hb_start
        else:
            actual_dur = frame_span / video_fps
        effective_count = count / actual_dur if actual_dur > 0 else 0.0

        if count == 0:
            counters["zoh_empty"] += 1
            slot_times = np.linspace(hb_start, hb_start + frame_span / video_fps, frame_span, endpoint=False)
            values = np.repeat(zoh.reshape(1, -1), frame_span, axis=0)
            slot_frames.append(make_slot_frame(hb_id, slot_times, values, channel_cols, mask=EMPTY_MASK_VALUE))
            continue

        if effective_count < DROP_LOW_MIN_EFFECTIVE_COUNT:
            counters["drop_low"] += 1
            continue

        counters["keep"] += 1
        n_slots = max(1, int(round(actual_dur * video_fps))) if is_edge else frame_span
        slot_times = np.linspace(hb_start, hb_start + actual_dur, n_slots, endpoint=False)
        row_times = rows["captured_at"].to_numpy(dtype=np.float64)
        row_values = rows[channel_cols].to_numpy(dtype=np.float64)
        values = np.empty((n_slots, len(channel_cols)), dtype=np.float64)
        for idx in range(len(channel_cols)):
            values[:, idx] = np.interp(slot_times, row_times, row_values[:, idx])
        zoh = values[-1].copy()
        slot_frames.append(make_slot_frame(hb_id, slot_times, values, channel_cols, mask=KEEP_MASK_VALUE))

    if slot_frames:
        out_df = pd.concat(slot_frames, ignore_index=True)
        out_df = out_df.sort_values("t_visual", kind="mergesort").reset_index(drop=True)
    else:
        out_df = empty_output_frame(channel_cols)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_df.to_parquet(out_path, engine=PARQUET_ENGINE, index=False)

    return {
        "input_path": str(csv_path),
        "output_path": str(out_path),
        "time_source": time_source,
        "channel_count": len(channel_cols),
        "original_rows": original_rows,
        "out_of_frame_rows": int((~in_frame).sum()),
        "rows_before_dedup": int(work.shape[0]),
        "dedup_deleted_rows": dedup_deleted,
        "rows_after_dedup": int(deduped.shape[0]),
        "max_observed_heartbeat_id": max_observed_hb_id,
        "emitted_slot_rows": int(out_df.shape[0]),
        "heartbeat_counts": counters,
    }


def find_pressure_csv(session_dir: Path, side: str) -> Path | None:
    matches = sorted(session_dir.glob(PRESSURE_INPUT_PATTERN.format(side=side)))
    if len(matches) > 1:
        names = ", ".join(path.name for path in matches)
        raise ValueError(f"Multiple {side} pressure CSV files in {session_dir}: {names}")
    return matches[0] if matches else None


def process_session(session_dir: Path) -> dict[str, object]:
    session_dir = session_dir.resolve()
    frame_times, first_frame_seconds = parse_frame_times(session_dir / VIDEO_SUBDIR_NAME)
    video_fps = (len(frame_times) - 1) / (float(frame_times[-1]) - float(frame_times[0]))
    frame_span = max(1, int(round(video_fps)))
    cleaned_dir = OUTPUT_ROOT_DIR / session_dir.name

    log: dict[str, object] = {
        "session_dir": str(session_dir),
        "output_dir": str(cleaned_dir),
        "video_fps": video_fps,
        "frame_span": frame_span,
        "frame_count": int(len(frame_times)),
        "frame_time_first_seconds_of_day": first_frame_seconds,
        "frame_time_last_relative": float(frame_times[-1]),
        "pressure": {},
    }

    for side in PRESSURE_SIDES:
        csv_path = find_pressure_csv(session_dir, side)
        if csv_path is None:
            log["pressure"][side] = {"status": "missing"}
            continue
        out_path = cleaned_dir / PRESSURE_OUTPUT_FILE.format(side=side)
        log["pressure"][side] = process_pressure_csv(
            csv_path=csv_path,
            out_path=out_path,
            frame_times=frame_times,
            first_frame_seconds=first_frame_seconds,
            video_fps=video_fps,
            frame_span=frame_span,
        )

    cleaned_dir.mkdir(parents=True, exist_ok=True)
    log_path = cleaned_dir / LOG_FILE_NAME
    log_path.write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")
    return log


def discover_sessions(paths: Iterable[Path], recursive: bool) -> list[Path]:
    sessions: set[Path] = set()
    for path in paths:
        path = path.resolve()
        candidates = [path]
        if recursive:
            pressure_files = list(path.rglob("Pressure_left_*.csv")) + list(path.rglob("Pressure_right_*.csv"))
            candidates.extend(file.parent for file in pressure_files)
        for candidate in candidates:
            if (candidate / "1").is_dir() and (
                list(candidate.glob("Pressure_left_*.csv")) or list(candidate.glob("Pressure_right_*.csv"))
            ):
                sessions.add(candidate)
    return sorted(sessions, key=lambda p: str(p))


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Clean left/right tactile pressure CSV files into fixed-slot parquet files."
    )
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        help="Session directory path(s), or root dirs with --recursive. Defaults to the four experiment roots.",
    )
    parser.add_argument(
        "--recursive",
        action="store_true",
        default=DEFAULT_RECURSIVE,
        help="Recursively discover session directories below paths.",
    )
    return parser


def main() -> int:
    args = build_arg_parser().parse_args()
    input_paths = args.paths or DEFAULT_INPUT_PATHS
    sessions = discover_sessions(input_paths, args.recursive)
    if not sessions:
        raise SystemExit("No session directories found.")

    for idx, session_dir in enumerate(sessions, start=1):
        log = process_session(session_dir)
        print(f"[{idx}/{len(sessions)}] cleaned {log['session_dir']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
