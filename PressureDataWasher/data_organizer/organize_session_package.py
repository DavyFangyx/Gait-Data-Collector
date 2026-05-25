"""Package one pressure/video/mocap session into a washed output folder.

Example:
    python data_organizer/organize_session_package.py ^
        --session-dir "F:\3_Experiment_Data\FourVideosData\Experiment\S1\rec20260419_162851_万山_S101_1" ^
        --cleaned-dir "data_washer\pressure_cleaned\rec20260419_162851_万山_S101_1" ^
        --calibration-csv "F:\3_Experiment_Data\AlignResult\S1011.csv" ^
        --output-root "F:\3_Experiment_Data\Data_Washed"

Batch mode can discover all default S1-S4 sessions and matching calibration CSVs:
python data_organizer/organize_session_package.py --all --overwrite
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import pandas as pd


REPO_ROOT = Path(__file__).resolve().parents[1]

DEFAULT_SESSION_ROOTS = [
    Path(r"F:\3_Experiment_Data\FourVideosData\Experiment\S1"),
    Path(r"F:\3_Experiment_Data\FourVideosData\Experiment\S2"),
    Path(r"F:\3_Experiment_Data\FourVideosData\Experiment\S3"),
    Path(r"F:\3_Experiment_Data\FourVideosData\Experiment\S4"),
]
DEFAULT_CLEANED_ROOT = REPO_ROOT / "data_washer" / "pressure_cleaned"
DEFAULT_ALIGN_ROOT = Path(r"F:\3_Experiment_Data\AlignResult")
DEFAULT_MOCAP_ROOTS = [
    Path(r"F:\3_Experiment_Data\动捕数据\20260419"),
    Path(r"F:\3_Experiment_Data\动捕数据\20260422"),
]
DEFAULT_OUTPUT_ROOT = Path(r"F:\3_Experiment_Data\Data_Washed")

PRESSURE_SIDES = ("left", "right")
PRESSURE_CSV_PATTERN = "Pressure_{side}_*.csv"
CLEANED_PARQUET_NAME = "pressure_{side}.parquet"
LOG_FILE_NAME = "log.json"
META_FILE_NAME = "meta.json"
MOCAP_DIR_NAME = "Mocap"
OFFSET_TOLERANCE_S = 1e-6
OFFSET_FLOAT_EPSILON_S = 1e-9
IMAGE_EXTENSIONS = {".jpg", ".jpeg"}
MP4_EXTENSION = ".mp4"


@dataclass
class PackageIssueLog:
    warnings: list[str] = field(default_factory=list)
    missing_info: list[str] = field(default_factory=list)

    def warn(self, message: str) -> None:
        self.warnings.append(message)

    def missing(self, message: str) -> None:
        self.missing_info.append(message)


@dataclass
class CalibrationInfo:
    session_id: str | None
    visual_window: list[float]
    mocap_window: list[float]
    offset_s: float
    mp4_paths: list[Path]
    avi_path: Path | None
    bvh_paths: list[Path]
    source_csv: Path
    columns: dict[str, str]


def normalized_name(value: str) -> str:
    return re.sub(r"[\s_\-（）()\[\].:：/\\]+", "", str(value).lower())


def read_text_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_text_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def path_to_str(path: Path | None) -> str | None:
    return str(path) if path is not None else None


def infer_session_id(session_dir: Path) -> str | None:
    match = re.search(r"(S\d{3})[_\-]?(\d+)", session_dir.name, flags=re.IGNORECASE)
    if match:
        return f"{match.group(1).upper()}{match.group(2)}"
    return None


def discover_sessions(roots: Iterable[Path]) -> list[Path]:
    sessions: set[Path] = set()
    for root in roots:
        if not root.exists():
            continue
        for pressure_csv in root.rglob("Pressure_left_*.csv"):
            candidate = pressure_csv.parent
            if any((candidate / str(cam)).is_dir() for cam in range(1, 5)):
                sessions.add(candidate)
    return sorted(sessions, key=lambda p: str(p))


def find_calibration_csv(session_dir: Path, align_root: Path) -> Path:
    session_id = infer_session_id(session_dir)
    if not session_id:
        raise ValueError(f"Cannot infer calibration id from session name: {session_dir.name}")
    candidate = align_root / f"{session_id}.csv"
    if not candidate.is_file():
        raise FileNotFoundError(f"Calibration CSV not found for {session_dir.name}: {candidate}")
    return candidate


def find_cleaned_dir(session_dir: Path, cleaned_root: Path) -> Path:
    candidate = cleaned_root / session_dir.name
    if not candidate.is_dir():
        raise FileNotFoundError(f"Cleaned session folder not found: {candidate}")
    return candidate


def find_column(columns: Iterable[str], aliases: Iterable[str]) -> str | None:
    lookup = {normalized_name(col): col for col in columns}
    for alias in aliases:
        key = normalized_name(alias)
        if key in lookup:
            return lookup[key]
    for col in columns:
        normalized = normalized_name(col)
        if any(normalized_name(alias) in normalized for alias in aliases):
            return col
    return None


def find_marker_column(df: pd.DataFrame) -> str:
    marker_col = find_column(df.columns, ["marker", "label", "标记", "事件"])
    if marker_col is not None:
        return marker_col
    for col in df.columns:
        values = {str(v).strip().lower() for v in df[col].dropna().tolist()}
        if {"start", "end"}.issubset(values):
            return col
    raise ValueError("Calibration CSV has no marker column with start/end rows")


def require_float(row: pd.Series, col: str, row_name: str) -> float:
    try:
        return float(row[col])
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Bad numeric value in {row_name}.{col}: {row[col]!r}") from exc


def candidate_roots_for_paths(
    session_dir: Path,
    calibration_csv: Path,
    mocap_roots: list[Path],
) -> list[Path]:
    roots = [session_dir, calibration_csv.parent, *mocap_roots]
    existing: list[Path] = []
    for root in roots:
        if root.exists() and root not in existing:
            existing.append(root)
    return existing


def resolve_existing_path(
    raw_value: Any,
    roots: list[Path],
    suffixes: set[str] | None,
    issues: PackageIssueLog,
    label: str,
    required: bool = True,
) -> Path | None:
    if pd.isna(raw_value) or str(raw_value).strip() == "":
        if required:
            issues.missing(f"{label} path is empty in calibration CSV")
        return None

    text = str(raw_value).strip().strip('"')
    direct = Path(text)
    if direct.exists():
        return direct

    name = direct.name
    if not name:
        if required:
            issues.missing(f"{label} path has no filename: {text}")
        return None

    matches: list[Path] = []
    lowered_suffixes = {s.lower() for s in suffixes} if suffixes else None
    for root in roots:
        try:
            for match in root.rglob(name):
                if lowered_suffixes is None or match.suffix.lower() in lowered_suffixes:
                    matches.append(match)
        except OSError as exc:
            issues.warn(f"Cannot search {root} while resolving {label}: {exc}")

    unique = sorted({m.resolve() for m in matches}, key=lambda p: str(p))
    if len(unique) == 1:
        issues.warn(f"Resolved stale {label} path by filename: {text} -> {unique[0]}")
        return unique[0]
    if len(unique) > 1:
        issues.missing(f"{label} path is ambiguous for {name}: {[str(p) for p in unique]}")
        return None

    if required:
        issues.missing(f"{label} file not found: {text}")
    return None


def locate_single_by_suffix(
    roots: list[Path],
    suffix: str,
    issues: PackageIssueLog,
    label: str,
    required: bool = True,
) -> Path | None:
    matches: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        try:
            matches.extend(path for path in root.rglob(f"*{suffix}") if path.is_file())
        except OSError as exc:
            issues.warn(f"Cannot scan {root} for {label}: {exc}")

    unique = sorted({m.resolve() for m in matches}, key=lambda p: str(p))
    if len(unique) == 1:
        return unique[0]
    if len(unique) > 1:
        issues.missing(f"{label} is ambiguous: {[str(p) for p in unique]}")
        return None
    if required:
        issues.missing(f"{label} not found under {[str(r) for r in roots]}")
    return None


def read_calibration(
    calibration_csv: Path,
    session_dir: Path,
    mocap_roots: list[Path],
    issues: PackageIssueLog,
) -> CalibrationInfo:
    df = pd.read_csv(calibration_csv, encoding="utf-8-sig")
    marker_col = find_marker_column(df)
    visual_col = find_column(df.columns, ["visual_time", "视觉时间", "视觉时间s"])
    mocap_col = find_column(df.columns, ["mocap_time", "动捕时间", "动捕时间s"])
    offset_col = find_column(df.columns, ["offset", "偏移量", "偏移量s"])
    session_id_col = find_column(df.columns, ["session_id", "session", "会话编号"])

    if visual_col is None:
        raise ValueError(f"Cannot find visual time column in {calibration_csv}")
    if mocap_col is None:
        raise ValueError(f"Cannot find mocap time column in {calibration_csv}")

    marker_values = df[marker_col].astype(str).str.strip().str.lower()
    start_rows = df.loc[marker_values == "start"]
    end_rows = df.loc[marker_values == "end"]
    if start_rows.shape[0] != 1 or end_rows.shape[0] != 1:
        raise ValueError(f"Expected exactly one start row and one end row in {calibration_csv}")

    start = start_rows.iloc[0]
    end = end_rows.iloc[0]
    start_visual = require_float(start, visual_col, "start")
    end_visual = require_float(end, visual_col, "end")
    start_mocap = require_float(start, mocap_col, "start")
    end_mocap = require_float(end, mocap_col, "end")
    start_offset = start_visual - start_mocap
    end_offset = end_visual - end_mocap
    offset_diff = abs(start_offset - end_offset)
    if offset_diff > OFFSET_TOLERANCE_S + OFFSET_FLOAT_EPSILON_S:
        raise ValueError(
            f"Calibration offset mismatch: start={start_offset:.12f}, "
            f"end={end_offset:.12f}, diff={offset_diff:.12f}, tolerance={OFFSET_TOLERANCE_S}"
        )
    if offset_col is not None:
        table_offset = require_float(start, offset_col, "start")
        if abs(table_offset - start_offset) > OFFSET_TOLERANCE_S:
            issues.warn(
                f"Computed offset {start_offset:.12f} differs from table offset "
                f"{table_offset:.12f}; computed value is used"
            )

    roots = candidate_roots_for_paths(session_dir, calibration_csv, mocap_roots)
    path_columns = [col for col in df.columns if "路径" in col or "path" in normalized_name(col)]
    mp4_cols = [
        col for col in path_columns
        if "cam" in normalized_name(col) or str(col).lower().endswith("_path")
    ]
    if not mp4_cols:
        mp4_cols = [
            col for col in path_columns
            if str(start.get(col, "")).lower().endswith(".mp4")
        ]

    mp4_paths: list[Path] = []
    for col in mp4_cols:
        resolved = resolve_existing_path(
            start[col], [session_dir], {".mp4"}, issues, f"MP4 column {col}", required=False
        )
        if resolved is not None and resolved not in mp4_paths:
            mp4_paths.append(resolved)
    if not mp4_paths:
        mp4_paths = sorted(path for path in session_dir.glob("*.mp4") if path.is_file())
        if not mp4_paths:
            issues.missing(f"No MP4 files found in session directory: {session_dir}")

    avi_col = find_column(df.columns, ["mocap_avi路径", "mocap_avi_path", "avi路径", "avi_path", "avi"])
    if avi_col is not None:
        avi_path = resolve_existing_path(start[avi_col], roots, {".avi"}, issues, f"AVI column {avi_col}")
    else:
        issues.warn("No AVI column found in calibration CSV; scanning mocap folders by session id")
        scan_roots = mocap_roots
        avi_path = locate_single_by_suffix(scan_roots, ".avi", issues, "AVI file")

    bvh_cols = [
        col for col in df.columns
        if "bvh" in normalized_name(col) and ("路径" in col or "path" in normalized_name(col))
    ]
    bvh_paths: list[Path] = []
    for col in bvh_cols:
        resolved = resolve_existing_path(start[col], roots, {".bvh"}, issues, f"BVH column {col}")
        if resolved is not None and resolved not in bvh_paths:
            bvh_paths.append(resolved)
    if not bvh_paths:
        bvh_path = locate_single_by_suffix(mocap_roots, ".bvh", issues, "BVH file")
        if bvh_path is not None:
            bvh_paths.append(bvh_path)

    session_id = str(start[session_id_col]).strip() if session_id_col is not None else infer_session_id(session_dir)
    visual_window = [start_visual, end_visual]
    mocap_window = [value - start_offset for value in visual_window]
    return CalibrationInfo(
        session_id=session_id,
        visual_window=visual_window,
        mocap_window=mocap_window,
        offset_s=start_offset,
        mp4_paths=mp4_paths,
        avi_path=avi_path,
        bvh_paths=bvh_paths,
        source_csv=calibration_csv,
        columns={
            "marker": marker_col,
            "visual_time": visual_col,
            "mocap_time": mocap_col,
            "offset": offset_col or "",
            "avi": avi_col or "",
            "bvh": ", ".join(bvh_cols),
        },
    )


def count_camera_frames(session_dir: Path) -> dict[str, int]:
    counts: dict[str, int] = {}
    for idx in range(1, 5):
        cam_dir = session_dir / str(idx)
        if cam_dir.is_dir():
            counts[str(idx)] = sum(
                1 for path in cam_dir.iterdir()
                if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
            )
    return counts


def count_bvh_frames(path: Path) -> int | None:
    with path.open("r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            stripped = line.strip()
            if stripped.lower().startswith("frames:"):
                try:
                    return int(stripped.split(":", 1)[1].strip())
                except ValueError:
                    return None
    return None


def read_pressure_summary(
    side: str,
    session_dir: Path,
    cleaned_dir: Path,
    log: dict[str, Any],
    issues: PackageIssueLog,
) -> tuple[dict[str, Any], Path | None, Path | None]:
    side_log = log.get("pressure", {}).get(side, {})
    csv_path = Path(side_log["input_path"]) if "input_path" in side_log else None
    if csv_path is None or not csv_path.exists():
        matches = sorted(session_dir.glob(PRESSURE_CSV_PATTERN.format(side=side)))
        csv_path = matches[0] if len(matches) == 1 else None
        if csv_path is None:
            issues.missing(f"Cannot find original pressure CSV for {side}")

    parquet_path = cleaned_dir / CLEANED_PARQUET_NAME.format(side=side)
    if not parquet_path.is_file():
        issues.missing(f"Cleaned parquet missing for {side}: {parquet_path}")
        parquet_path = None

    cleaned_rows: int | None = None
    mask_coverage: float | None = None
    if parquet_path is not None:
        df = pd.read_parquet(parquet_path)
        cleaned_rows = int(df.shape[0])
        if "mask" in df.columns and cleaned_rows > 0:
            mask_coverage = float((df["mask"] != 0).mean())
        elif "mask" not in df.columns:
            issues.warn(f"{parquet_path} has no mask column; mask coverage is null")

    heartbeat_counts = side_log.get("heartbeat_counts", {}) if isinstance(side_log, dict) else {}
    summary = {
        "original_csv": csv_path.name if csv_path else None,
        "original_csv_path": path_to_str(csv_path),
        "original_csv_rows": side_log.get("original_rows"),
        "cleaned_parquet": None,
        "cleaned_parquet_rows": cleaned_rows,
        "mask_coverage": mask_coverage,
        "drop_low_heartbeats": heartbeat_counts.get("drop_low"),
        "zoh_empty_heartbeats": heartbeat_counts.get("zoh_empty"),
    }
    return summary, csv_path, parquet_path


def copytree_contents(src: Path, dst: Path, overwrite: bool) -> None:
    if not dst.exists():
        shutil.copytree(src, dst)
        return

    for child in src.iterdir():
        target = dst / child.name
        if child.is_dir():
            shutil.copytree(child, target, dirs_exist_ok=overwrite)
        else:
            copy_file(child, target, overwrite=overwrite)


def copy_file(src: Path, dst: Path, overwrite: bool) -> None:
    if dst.exists() and not overwrite:
        raise FileExistsError(f"Output file already exists: {dst}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def package_session(
    session_dir: Path,
    cleaned_dir: Path,
    calibration_csv: Path,
    output_root: Path,
    mocap_roots: list[Path],
    overwrite: bool,
    dry_run: bool,
) -> dict[str, Any]:
    session_dir = session_dir.resolve()
    cleaned_dir = cleaned_dir.resolve()
    calibration_csv = calibration_csv.resolve()
    output_dir = output_root / session_dir.name
    issues = PackageIssueLog()

    if output_dir.exists() and any(output_dir.iterdir()) and not overwrite and not dry_run:
        raise FileExistsError(f"Output folder exists and is not empty: {output_dir}")

    log_path = cleaned_dir / LOG_FILE_NAME
    if not log_path.is_file():
        raise FileNotFoundError(f"Task 1 log.json not found: {log_path}")
    log = read_text_json(log_path)
    calibration = read_calibration(calibration_csv, session_dir, mocap_roots, issues)

    pressure_meta: dict[str, Any] = {}
    pressure_copies: list[tuple[Path, Path]] = []
    for side in PRESSURE_SIDES:
        summary, csv_path, parquet_path = read_pressure_summary(side, session_dir, cleaned_dir, log, issues)
        if csv_path is not None and parquet_path is not None:
            target_name = f"{csv_path.stem}.parquet"
            summary["cleaned_parquet"] = target_name
            pressure_copies.append((parquet_path, output_dir / target_name))
        pressure_meta[side] = summary

    mocap_copies: list[tuple[Path, Path]] = []
    mocap_dir = output_dir / MOCAP_DIR_NAME
    if calibration.avi_path is not None:
        mocap_copies.append((calibration.avi_path, mocap_dir / calibration.avi_path.name))
    for bvh_path in calibration.bvh_paths:
        mocap_copies.append((bvh_path, mocap_dir / bvh_path.name))

    bvh_frame_counts = {
        bvh_path.name: count_bvh_frames(bvh_path)
        for bvh_path in calibration.bvh_paths
        if bvh_path.exists()
    }

    mp4_files = sorted(path.name for path in session_dir.glob("*.mp4") if path.is_file())
    if len(mp4_files) != 4:
        issues.warn(f"Expected 4 MP4 files in {session_dir}, found {len(mp4_files)}")

    meta = {
        "session_name": session_dir.name,
        "session_id": calibration.session_id,
        "source_dir": str(session_dir),
        "cleaned_dir": str(cleaned_dir),
        "calibration_csv": str(calibration_csv),
        "output_dir": str(output_dir),
        "processed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "video_fps": log.get("video_fps"),
        "frame_span": log.get("frame_span"),
        "offset_s": calibration.offset_s,
        "visual_window": calibration.visual_window,
        "mocap_window": calibration.mocap_window,
        "cameras": {
            "frame_counts": count_camera_frames(session_dir),
            "mp4_files": mp4_files,
            "mp4_paths_from_calibration": [str(path) for path in calibration.mp4_paths],
        },
        "mocap": {
            "avi_file": calibration.avi_path.name if calibration.avi_path else None,
            "avi_path": path_to_str(calibration.avi_path),
            "bvh_files": [path.name for path in calibration.bvh_paths],
            "bvh_paths": [str(path) for path in calibration.bvh_paths],
            "bvh_frame_counts": bvh_frame_counts,
        },
        "pressure": pressure_meta,
        "calibration_columns": calibration.columns,
        "warnings": issues.warnings,
        "missing_info": issues.missing_info,
    }

    if issues.missing_info:
        raise RuntimeError(
            "Missing or ambiguous information:\n"
            + "\n".join(f"- {item}" for item in issues.missing_info)
        )

    if not dry_run:
        output_dir.mkdir(parents=True, exist_ok=True)
        copytree_contents(session_dir, output_dir, overwrite=overwrite)
        for src, dst in pressure_copies:
            copy_file(src, dst, overwrite=overwrite)
        for src, dst in mocap_copies:
            copy_file(src, dst, overwrite=overwrite)
        write_text_json(output_dir / META_FILE_NAME, meta)

    return meta


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Copy raw session data, cleaned pressure parquet, and mocap files into one package."
    )
    parser.add_argument("--session-dir", type=Path, help="Raw session directory.")
    parser.add_argument("--cleaned-dir", type=Path, help="Task 1 cleaned/<session> directory.")
    parser.add_argument("--calibration-csv", type=Path, help="Alignment/calibration CSV for this session.")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--cleaned-root", type=Path, default=DEFAULT_CLEANED_ROOT)
    parser.add_argument("--align-root", type=Path, default=DEFAULT_ALIGN_ROOT)
    parser.add_argument("--session-root", action="append", type=Path, dest="session_roots")
    parser.add_argument("--mocap-root", action="append", type=Path, dest="mocap_roots")
    parser.add_argument("--all", action="store_true", help="Discover all sessions under default or given roots.")
    parser.add_argument("--overwrite", action="store_true", help="Allow copying into existing output folders/files.")
    parser.add_argument("--dry-run", action="store_true", help="Build and print metadata without copying files.")
    return parser


def validate_single_args(args: argparse.Namespace) -> tuple[Path, Path, Path]:
    if args.session_dir is None:
        raise SystemExit("--session-dir is required unless --all is used")
    session_dir = args.session_dir
    cleaned_dir = args.cleaned_dir or find_cleaned_dir(session_dir, args.cleaned_root)
    calibration_csv = args.calibration_csv or find_calibration_csv(session_dir, args.align_root)
    return session_dir, cleaned_dir, calibration_csv


def main() -> int:
    args = build_arg_parser().parse_args()
    mocap_roots = args.mocap_roots or DEFAULT_MOCAP_ROOTS
    session_roots = args.session_roots or DEFAULT_SESSION_ROOTS

    if args.all:
        sessions = discover_sessions(session_roots)
        if not sessions:
            raise SystemExit(f"No sessions found under {[str(root) for root in session_roots]}")
        failures: list[dict[str, str]] = []
        for idx, session_dir in enumerate(sessions, start=1):
            try:
                cleaned_dir = find_cleaned_dir(session_dir, args.cleaned_root)
                calibration_csv = find_calibration_csv(session_dir, args.align_root)
                meta = package_session(
                    session_dir=session_dir,
                    cleaned_dir=cleaned_dir,
                    calibration_csv=calibration_csv,
                    output_root=args.output_root,
                    mocap_roots=mocap_roots,
                    overwrite=args.overwrite,
                    dry_run=args.dry_run,
                )
                status = "checked" if args.dry_run else "packaged"
                print(f"[{idx}/{len(sessions)}] {status} {meta['session_name']}")
            except Exception as exc:
                calibration_csv = args.align_root / f"{infer_session_id(session_dir) or session_dir.name}.csv"
                failure = {
                    "session_name": session_dir.name,
                    "session_id": infer_session_id(session_dir) or "",
                    "session_dir": str(session_dir),
                    "calibration_csv": str(calibration_csv),
                    "error": str(exc),
                }
                failures.append(failure)
                print(f"[{idx}/{len(sessions)}] FAILED {session_dir.name}: {exc}")

        if failures:
            failure_path = args.output_root / "organize_failures.json"
            if not args.dry_run:
                args.output_root.mkdir(parents=True, exist_ok=True)
                write_text_json(
                    failure_path,
                    {
                        "processed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                        "failure_count": len(failures),
                        "failures": failures,
                    },
                )
                print(f"Finished with {len(failures)} failed session(s). Details: {failure_path}")
            else:
                print(f"Dry-run finished with {len(failures)} failed session(s).")
            return 1
        return 0

    session_dir, cleaned_dir, calibration_csv = validate_single_args(args)
    meta = package_session(
        session_dir=session_dir,
        cleaned_dir=cleaned_dir,
        calibration_csv=calibration_csv,
        output_root=args.output_root,
        mocap_roots=mocap_roots,
        overwrite=args.overwrite,
        dry_run=args.dry_run,
    )
    if args.dry_run:
        print(json.dumps(meta, ensure_ascii=False, indent=2))
    else:
        print(f"packaged {meta['session_name']} -> {meta['output_dir']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
