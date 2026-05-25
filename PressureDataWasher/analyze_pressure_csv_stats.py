#!/usr/bin/env python
# coding: utf-8

import argparse
import csv
import math
import os
import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path


DEFAULT_ROOT_DIRS = [
    r"F:\3_Experiment_Data\FourVideosData\Experiment\S1",
    r"F:\3_Experiment_Data\FourVideosData\Experiment\S2",
    r"F:\3_Experiment_Data\FourVideosData\Experiment\S3",
    r"F:\3_Experiment_Data\FourVideosData\Experiment\S4",
]
DEFAULT_FILE_PATTERNS = [
    "Pressure_left_*.csv",
    "Pressure_right_*.csv",
    "PressureOnly_left_*.csv",
    "PressureOnly_right_*.csv",
]
METADATA_COLUMNS = {
    "frame_index",
    "heartbeat_id",
    "local_frame_index",
    "sensor_frame_index",
    "time_us",
    "time_s",
    "monotonic_us",
    "captured_at_iso",
}


def is_channel_column(name):
    text = str(name).strip()
    return (
        re.fullmatch(r"\d+", text) is not None
        or re.fullmatch(r"R\d+_C\d+", text) is not None
    )


def natural_channel_key(name):
    text = str(name).strip()
    if text.isdigit():
        return 0, int(text)
    match = re.fullmatch(r"R(\d+)_C(\d+)", text)
    if match:
        return 1, int(match.group(1)), int(match.group(2))
    return 9, text


def parse_int(value):
    try:
        text = str(value).strip()
        if text == "":
            return None
        return int(float(text))
    except (TypeError, ValueError):
        return None


def parse_float(value):
    try:
        text = str(value).strip()
        if text == "":
            return None
        result = float(text)
        if math.isnan(result) or math.isinf(result):
            return None
        return result
    except (TypeError, ValueError):
        return None


def is_missing_value(value):
    text = str(value).strip()
    return text == "" or text.lower() in {"nan", "none", "null", "na", "n/a"}


def is_zero_value(value):
    number = parse_float(value)
    return number == 0.0 if number is not None else False


def get_subject_from_path(path):
    for part in Path(path).parts:
        if re.fullmatch(r"S\d+", part):
            return part
    return ""


def get_side_from_name(path):
    match = re.search(r"Pressure(?:Only)?_(left|right)_", Path(path).name)
    return match.group(1) if match else ""


def find_files(root_dirs, patterns, explicit_files):
    files = {Path(item).resolve() for item in explicit_files}
    for root in root_dirs:
        root_path = Path(root)
        if not root_path.exists():
            print(f"Warning: root not found: {root_path}")
            continue
        for pattern in patterns:
            files.update(path.resolve() for path in root_path.rglob(pattern))
    return sorted(files, key=lambda path: str(path).lower())


def build_gap_rows(file_path, subject, sample_folder, side, column_name, values):
    rows = []
    valid_values = [value for value in values if value is not None]
    if not valid_values:
        return rows
    previous = valid_values[0]
    for value in valid_values[1:]:
        if value > previous + 1:
            rows.append({
                "file_path": str(file_path),
                "subject": subject,
                "sample_folder": sample_folder,
                "side": side,
                "index_column": column_name,
                "before_id": previous,
                "after_id": value,
                "missing_count": value - previous - 1,
                "missing_range_start": previous + 1,
                "missing_range_end": value - 1,
            })
        previous = value
    return rows


def analyze_pressure_csv(path):
    path = Path(path)
    subject = get_subject_from_path(path)
    sample_folder = path.parent.name
    side = get_side_from_name(path)

    total_rows = 0
    bad_rows = 0
    all_zero_rows = 0
    rows_with_missing_channels = 0
    adjacent_duplicate_rows = 0
    previous_channel_tuple = None
    frame_values = defaultdict(list)
    duplicate_id_counts = Counter()
    seen_ids = defaultdict(Counter)
    channel_missing_counts = Counter()
    channel_nonzero_counts = Counter()
    missing_row_details = []

    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError("empty CSV file")

        headers = [name.strip() for name in reader.fieldnames]
        channel_columns = sorted(
            [name for name in headers if is_channel_column(name)],
            key=natural_channel_key,
        )
        if not channel_columns:
            channel_columns = [
                name for name in headers
                if name.strip() not in METADATA_COLUMNS
            ]

        index_columns = [
            name for name in ("local_frame_index", "sensor_frame_index", "frame_index", "heartbeat_id")
            if name in headers
        ]

        for csv_row_number, row in enumerate(reader, start=2):
            total_rows += 1
            if not row:
                bad_rows += 1
                continue

            for column in index_columns:
                value = parse_int(row.get(column))
                if value is None:
                    continue
                frame_values[column].append(value)
                seen_ids[column][value] += 1
                if seen_ids[column][value] == 2:
                    duplicate_id_counts[column] += 1

            missing_channels = [
                channel for channel in channel_columns
                if is_missing_value(row.get(channel, ""))
            ]
            for channel in missing_channels:
                channel_missing_counts[channel] += 1
            if missing_channels:
                rows_with_missing_channels += 1
                missing_row_details.append({
                    "file_path": str(path),
                    "subject": subject,
                    "sample_folder": sample_folder,
                    "side": side,
                    "csv_row_number": csv_row_number,
                    "local_frame_index": row.get("local_frame_index", ""),
                    "sensor_frame_index": row.get("sensor_frame_index", ""),
                    "frame_index": row.get("frame_index", ""),
                    "heartbeat_id": row.get("heartbeat_id", ""),
                    "missing_channel_count": len(missing_channels),
                    "missing_channels": ";".join(missing_channels),
                })

            values = [row.get(channel, "") for channel in channel_columns]
            if values and all(is_zero_value(value) for value in values):
                all_zero_rows += 1
            for channel, value in zip(channel_columns, values):
                if not is_missing_value(value) and not is_zero_value(value):
                    channel_nonzero_counts[channel] += 1

            channel_tuple = tuple(values)
            if previous_channel_tuple is not None and channel_tuple == previous_channel_tuple:
                adjacent_duplicate_rows += 1
            previous_channel_tuple = channel_tuple

    gap_rows = []
    missing_slot_counts = {}
    for column, values in frame_values.items():
        gap_rows.extend(build_gap_rows(path, subject, sample_folder, side, column, values))
        if values:
            min_value = min(values)
            max_value = max(values)
            observed = set(values)
            missing_slot_counts[column] = sum(
                1 for value in range(min_value, max_value + 1)
                if value not in observed
            )
        else:
            missing_slot_counts[column] = 0

    channel_count = len(channel_columns)
    total_cells = total_rows * channel_count
    missing_cells = sum(channel_missing_counts.values())
    adjacent_pairs = max(0, total_rows - 1)
    summary = {
        "file_path": str(path),
        "subject": subject,
        "sample_folder": sample_folder,
        "side": side,
        "total_rows": total_rows,
        "bad_rows": bad_rows,
        "channel_count": channel_count,
        "channel_missing_cell_count": missing_cells,
        "channel_missing_cell_ratio": missing_cells / total_cells if total_cells else 0.0,
        "rows_with_missing_channels": rows_with_missing_channels,
        "rows_with_missing_channels_ratio": rows_with_missing_channels / total_rows if total_rows else 0.0,
        "all_zero_rows": all_zero_rows,
        "all_zero_row_ratio": all_zero_rows / total_rows if total_rows else 0.0,
        "adjacent_duplicate_rows": adjacent_duplicate_rows,
        "adjacent_pairs": adjacent_pairs,
        "adjacent_duplicate_ratio": adjacent_duplicate_rows / adjacent_pairs if adjacent_pairs else 0.0,
    }
    for column in ("local_frame_index", "sensor_frame_index", "frame_index", "heartbeat_id"):
        values = frame_values.get(column, [])
        summary[f"{column}_min"] = min(values) if values else ""
        summary[f"{column}_max"] = max(values) if values else ""
        summary[f"{column}_observed_unique_count"] = len(set(values)) if values else 0
        summary[f"{column}_missing_slot_count"] = missing_slot_counts.get(column, 0)
        summary[f"{column}_duplicate_id_count"] = duplicate_id_counts.get(column, 0)

    channel_rows = []
    for channel in channel_columns:
        channel_rows.append({
            "file_path": str(path),
            "subject": subject,
            "sample_folder": sample_folder,
            "side": side,
            "channel": channel,
            "missing_count": channel_missing_counts[channel],
            "missing_ratio": channel_missing_counts[channel] / total_rows if total_rows else 0.0,
            "nonzero_count": channel_nonzero_counts[channel],
            "nonzero_ratio": channel_nonzero_counts[channel] / total_rows if total_rows else 0.0,
        })

    return summary, gap_rows, channel_rows, missing_row_details


def write_csv(path, rows, fieldnames):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(
        description="统计压力/触觉 CSV 的缺失帧、缺失通道、空帧和重复帧。"
    )
    parser.add_argument("--root-dirs", nargs="*", default=DEFAULT_ROOT_DIRS)
    parser.add_argument("--file-patterns", nargs="*", default=DEFAULT_FILE_PATTERNS)
    parser.add_argument("--files", nargs="*", default=[])
    parser.add_argument("--output-dir", default="")
    args = parser.parse_args()

    output_dir = Path(args.output_dir) if args.output_dir else Path(
        f"pressure_missing_stats_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    )
    files = find_files(args.root_dirs, args.file_patterns, args.files)
    if not files:
        raise SystemExit("No pressure CSV files found.")

    file_summaries = []
    frame_gap_rows = []
    channel_missing_rows = []
    missing_row_details = []

    for index, file_path in enumerate(files, start=1):
        print(f"[{index}/{len(files)}] {file_path}")
        summary, gaps, channel_rows, row_details = analyze_pressure_csv(file_path)
        file_summaries.append(summary)
        frame_gap_rows.extend(gaps)
        channel_missing_rows.extend(channel_rows)
        missing_row_details.extend(row_details)

    summary_fields = list(file_summaries[0].keys())
    write_csv(output_dir / "file_summary.csv", file_summaries, summary_fields)
    write_csv(
        output_dir / "frame_gap_by_file.csv",
        frame_gap_rows,
        [
            "file_path", "subject", "sample_folder", "side", "index_column",
            "before_id", "after_id", "missing_count", "missing_range_start",
            "missing_range_end",
        ],
    )
    write_csv(
        output_dir / "channel_missing_by_file.csv",
        channel_missing_rows,
        [
            "file_path", "subject", "sample_folder", "side", "channel",
            "missing_count", "missing_ratio", "nonzero_count", "nonzero_ratio",
        ],
    )
    write_csv(
        output_dir / "missing_row_detail.csv",
        missing_row_details,
        [
            "file_path", "subject", "sample_folder", "side", "csv_row_number",
            "local_frame_index", "sensor_frame_index", "frame_index",
            "heartbeat_id", "missing_channel_count", "missing_channels",
        ],
    )

    totals = {
        "file_count": len(file_summaries),
        "total_rows": sum(row["total_rows"] for row in file_summaries),
        "total_channel_missing_cells": sum(row["channel_missing_cell_count"] for row in file_summaries),
        "total_rows_with_missing_channels": sum(row["rows_with_missing_channels"] for row in file_summaries),
        "total_all_zero_rows": sum(row["all_zero_rows"] for row in file_summaries),
        "total_adjacent_duplicate_rows": sum(row["adjacent_duplicate_rows"] for row in file_summaries),
        "total_frame_gap_segments": len(frame_gap_rows),
        "total_missing_row_detail_records": len(missing_row_details),
    }
    total_cells = sum(row["total_rows"] * row["channel_count"] for row in file_summaries)
    totals["overall_channel_missing_cell_ratio"] = (
        totals["total_channel_missing_cells"] / total_cells if total_cells else 0.0
    )
    totals["overall_rows_with_missing_channels_ratio"] = (
        totals["total_rows_with_missing_channels"] / totals["total_rows"]
        if totals["total_rows"] else 0.0
    )
    write_csv(output_dir / "overall_summary.csv", [totals], list(totals.keys()))

    print("")
    print(f"Done. Results saved to: {output_dir.resolve()}")
    print(f"  {output_dir / 'overall_summary.csv'}")
    print(f"  {output_dir / 'file_summary.csv'}")
    print(f"  {output_dir / 'frame_gap_by_file.csv'}")
    print(f"  {output_dir / 'channel_missing_by_file.csv'}")
    print(f"  {output_dir / 'missing_row_detail.csv'}")


if __name__ == "__main__":
    main()
