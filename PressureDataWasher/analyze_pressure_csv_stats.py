#!/usr/bin/env python
# coding: utf-8

import argparse
import csv
import math
import re
from collections import Counter, defaultdict
from pathlib import Path


DEFAULT_ROOT_DIRS = [r"data\test"]
DEFAULT_FILE_PATTERNS = ["pressure_left.csv", "pressure_right.csv"]
DEFAULT_OUTPUT_DIR = r"PressureDataWasher\analyze_pressure_results"
LARGE_FRAME_COUNT_THRESHOLD = 50
RECORDING_FOLDER_RE = re.compile(r"^rec_?\d{8}_\d{6}_.+", re.IGNORECASE)
METADATA_COLUMNS = {
    "frame_idx",
    "frame_index",
    "heartbeat_id",
    "local_frame_index",
    "sensor_frame_index",
    "t_us",
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


def normalized_channel_value(value):
    number = parse_float(value)
    if number is None:
        return None
    if number == 0:
        return 0
    if number.is_integer():
        return int(number)
    return number


def get_subject_from_path(path):
    for part in Path(path).parts:
        if re.fullmatch(r"S\d+", part):
            return part
    return ""


def get_side_from_name(path):
    match = re.search(r"pressure_(left|right)\.csv$", Path(path).name, re.IGNORECASE)
    if match:
        return match.group(1).lower()
    match = re.search(r"Pressure(?:Only)?_(left|right)_", Path(path).name)
    return match.group(1).lower() if match else ""


def find_files(root_dirs, patterns, explicit_files):
    files = {Path(item).resolve() for item in explicit_files}
    for root in root_dirs:
        root_path = Path(root)
        if not root_path.exists():
            print(f"Warning: root not found: {root_path}")
            continue
        for pattern in patterns:
            for path in root_path.rglob(pattern):
                if RECORDING_FOLDER_RE.match(path.parent.name):
                    files.add(path.resolve())
    return sorted(files, key=lambda path: str(path).lower())


def frame_identity(row, channel_columns):
    values = tuple(normalized_channel_value(row.get(channel, "")) for channel in channel_columns)
    is_nonzero = any(value not in (None, 0) for value in values)
    return values, is_nonzero


def add_duplicate_run(rows, path, subject, sample_folder, side, run):
    if len(run) < 2:
        return
    start = run[0]
    end = run[-1]
    repeated_values = start["values"]
    rows.append({
        "subject": subject,
        "sample_folder": sample_folder,
        "side": side,
        "repeat_times": len(run),
        "duplicate_index": "",
        "start_csv_row_number": start["csv_row_number"],
        "end_csv_row_number": end["csv_row_number"],
        "start_frame_idx": start["frame_idx"],
        "end_frame_idx": end["frame_idx"],
        "start_t_us": start["t_us"],
        "end_t_us": end["t_us"],
        "duration_us": (end["t_us"] - start["t_us"]) if start["t_us"] is not None and end["t_us"] is not None else "",
        "adjacent_duplicate_pair_count": len(run) - 1,
        "nonzero_channel_count": sum(1 for value in repeated_values if value not in (None, 0)),
        "repeated_values": ";".join("" if value is None else str(value) for value in repeated_values),
    })


def add_same_timestamp_run(rows, subject, sample_folder, side, run):
    if len(run) < 2:
        return
    start = run[0]
    end = run[-1]
    rows.append({
        "subject": subject,
        "sample_folder": sample_folder,
        "side": side,
        "t_us": start["t_us"],
        "repeat_times": len(run),
        "duplicate_index": "",
        "start_csv_row_number": start["csv_row_number"],
        "end_csv_row_number": end["csv_row_number"],
        "start_frame_idx": start["frame_idx"],
        "end_frame_idx": end["frame_idx"],
    })


def analyze_pressure_csv(path):
    path = Path(path)
    subject = get_subject_from_path(path)
    sample_folder = path.parent.name
    side = get_side_from_name(path)

    total_rows = 0
    t_us_values = []
    frames_per_second = Counter()
    rows_by_second = defaultdict(list)
    duplicate_run_rows = []
    current_run = []
    previous_values = None
    timestamp_run_rows = []
    current_timestamp_run = []
    previous_t_us = None
    original_headers = []

    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError("empty CSV file")

        headers = [name.strip() for name in reader.fieldnames]
        original_headers = headers
        channel_columns = sorted(
            [name for name in headers if is_channel_column(name)],
            key=natural_channel_key,
        )
        if not channel_columns:
            channel_columns = [
                name for name in headers
                if name.strip() not in METADATA_COLUMNS
            ]

        for csv_row_number, row in enumerate(reader, start=2):
            total_rows += 1

            t_us = parse_int(row.get("t_us", row.get("time_us", "")))
            frame_idx = parse_int(row.get("frame_idx", row.get("frame_index", "")))
            if t_us is not None:
                second_index = t_us // 1_000_000
                t_us_values.append(t_us)
                frames_per_second[second_index] += 1
                original_row = {
                    "subject": subject,
                    "sample_folder": sample_folder,
                    "side": side,
                    "second_index": second_index,
                    "csv_row_number": csv_row_number,
                }
                original_row.update({header: row.get(header, "") for header in headers})
                rows_by_second[second_index].append(original_row)

                timestamp_record = {
                    "csv_row_number": csv_row_number,
                    "frame_idx": frame_idx if frame_idx is not None else "",
                    "t_us": t_us,
                }
                if previous_t_us == t_us:
                    current_timestamp_run.append(timestamp_record)
                else:
                    add_same_timestamp_run(
                        timestamp_run_rows,
                        subject,
                        sample_folder,
                        side,
                        current_timestamp_run,
                    )
                    current_timestamp_run = [timestamp_record]
                previous_t_us = t_us
            else:
                add_same_timestamp_run(
                    timestamp_run_rows,
                    subject,
                    sample_folder,
                    side,
                    current_timestamp_run,
                )
                current_timestamp_run = []
                previous_t_us = None

            values, is_nonzero = frame_identity(row, channel_columns)
            frame_record = {
                "csv_row_number": csv_row_number,
                "frame_idx": frame_idx if frame_idx is not None else "",
                "t_us": t_us,
                "values": values,
            }
            if is_nonzero and previous_values == values:
                current_run.append(frame_record)
            else:
                add_duplicate_run(
                    duplicate_run_rows,
                    path,
                    subject,
                    sample_folder,
                    side,
                    current_run,
                )
                current_run = [frame_record] if is_nonzero else []

            previous_values = values if is_nonzero else None

    add_duplicate_run(
        duplicate_run_rows,
        path,
        subject,
        sample_folder,
        side,
        current_run,
    )
    add_same_timestamp_run(
        timestamp_run_rows,
        subject,
        sample_folder,
        side,
        current_timestamp_run,
    )

    second_rows = []
    large_original_rows = []
    max_t_us = max(t_us_values) if t_us_values else None
    for second_index in sorted(frames_per_second):
        second_end_us = (second_index + 1) * 1_000_000 - 1
        if max_t_us is not None and second_end_us > max_t_us:
            continue
        frame_count = frames_per_second[second_index]
        second_rows.append({
            "subject": subject,
            "sample_folder": sample_folder,
            "side": side,
            "second_index": second_index,
            "second_start_us": second_index * 1_000_000,
            "second_end_us": second_end_us,
            "frame_count": frame_count,
        })
        if frame_count > LARGE_FRAME_COUNT_THRESHOLD:
            large_original_rows.extend(rows_by_second[second_index])

    frame_count_values = [row["frame_count"] for row in second_rows]
    summary = {
        "subject": subject,
        "sample_folder": sample_folder,
        "side": side,
        "total_rows": total_rows,
        "channel_count": len(channel_columns),
        "t_us_min": min(t_us_values) if t_us_values else "",
        "t_us_max": max_t_us if max_t_us is not None else "",
        "second_bucket_count": len(second_rows),
        "frames_per_second_min": min(frame_count_values) if frame_count_values else 0,
        "frames_per_second_max": max(frame_count_values) if frame_count_values else 0,
        "frames_per_second_avg": (
            sum(frame_count_values) / len(frame_count_values)
            if frame_count_values else 0.0
        ),
        "nonzero_duplicate_run_count": len(duplicate_run_rows),
        "nonzero_adjacent_duplicate_pairs": sum(
            row["adjacent_duplicate_pair_count"] for row in duplicate_run_rows
        ),
        "nonzero_duplicate_frame_count": sum(
            row["repeat_times"] for row in duplicate_run_rows
        ),
        "same_timestamp_run_count": len(timestamp_run_rows),
        "same_timestamp_frame_count": sum(
            row["repeat_times"] for row in timestamp_run_rows
        ),
    }

    return summary, second_rows, duplicate_run_rows, large_original_rows, timestamp_run_rows, original_headers


def assign_duplicate_indexes(rows):
    counters = defaultdict(int)
    for row in rows:
        key = row["repeat_times"]
        counters[key] += 1
        row["duplicate_index"] = counters[key]


def write_csv(path, rows, fieldnames):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def setup_matplotlib():
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams.update({
        "figure.dpi": 140,
        "savefig.dpi": 180,
        "font.size": 10,
        "axes.grid": True,
        "grid.alpha": 0.25,
        "axes.spines.top": False,
        "axes.spines.right": False,
    })
    return plt


def plot_label(text):
    label = str(text).replace("测试", "test").replace("娴嬭瘯", "test")
    label = re.sub(r"[^\x20-\x7E]+", "_", label)
    label = re.sub(r"_+", "_", label).strip("_")
    return label


def plot_frame_count_distribution(output_dir, frame_count_rows):
    if not frame_count_rows:
        return None
    output_dir.mkdir(parents=True, exist_ok=True)
    plt = setup_matplotlib()
    counts = Counter(row["frame_count"] for row in frame_count_rows)
    x_values = sorted(counts)
    y_values = [counts[value] for value in x_values]

    fig, ax = plt.subplots(figsize=(10.5, 5.0))
    ax.bar(x_values, y_values, color="#2f6f8f", width=0.82)
    ax.set_title("Frame Count Per 1s Distribution")
    ax.set_xlabel("Frames in each t_us second bucket")
    ax.set_ylabel("Second bucket count")
    ax.set_xticks(x_values)
    fig.tight_layout()

    output_path = output_dir / "frame_count_per_second_distribution.png"
    fig.savefig(output_path)
    plt.close(fig)
    return output_path


def plot_duplicate_repeat_distribution(output_dir, duplicate_rows):
    repeat_values = [2, 3, 4]
    labels = []
    counts_by_label = defaultdict(Counter)
    for row in duplicate_rows:
        repeat_times = row["repeat_times"]
        if repeat_times not in repeat_values:
            continue
        label = plot_label(f"{row['sample_folder']}_{row['side']}")
        if label not in labels:
            labels.append(label)
        counts_by_label[label][repeat_times] += 1

    if not labels:
        return None

    output_dir.mkdir(parents=True, exist_ok=True)
    plt = setup_matplotlib()
    width = 0.24
    positions = list(range(len(labels)))
    colors = {2: "#2f6f8f", 3: "#b85c38", 4: "#5f7f3f"}

    fig_width = max(12.0, len(labels) * 0.72)
    fig, ax = plt.subplots(figsize=(fig_width, 5.6))
    for offset_index, repeat_times in enumerate(repeat_values):
        offset = (offset_index - 1) * width
        values = [counts_by_label[label][repeat_times] for label in labels]
        ax.bar(
            [position + offset for position in positions],
            values,
            width=width,
            label=f"repeat {repeat_times}",
            color=colors[repeat_times],
        )

    ax.set_title("Nonzero Adjacent Duplicate Repeat 2/3/4 Distribution")
    ax.set_xlabel("Sample and side")
    ax.set_ylabel("Occurrence count")
    ax.set_xticks(positions)
    ax.set_xticklabels(labels, rotation=45, ha="right")
    ax.legend()
    fig.tight_layout()

    output_path = output_dir / "nonzero_duplicate_repeat_2_3_4_distribution.png"
    fig.savefig(output_path)
    plt.close(fig)
    return output_path


def plot_timestamp_stuck_distribution(output_dir, timestamp_rows):
    if not timestamp_rows:
        return None
    output_dir.mkdir(parents=True, exist_ok=True)
    plt = setup_matplotlib()
    counts = Counter(row["repeat_times"] for row in timestamp_rows)
    x_values = sorted(counts)
    y_values = [counts[value] for value in x_values]

    fig, ax = plt.subplots(figsize=(10.5, 5.0))
    ax.bar(x_values, y_values, color="#7a4d8f", width=0.82)
    ax.set_title("Consecutive Same t_us Run Distribution")
    ax.set_xlabel("Consecutive rows with identical t_us")
    ax.set_ylabel("Run count")
    ax.set_xticks(x_values)
    fig.tight_layout()

    output_path = output_dir / "same_timestamp_repeat_distribution.png"
    fig.savefig(output_path)
    plt.close(fig)
    return output_path


def write_side_results(
    output_root,
    side,
    file_summaries,
    frame_count_rows,
    duplicate_run_rows,
    large_rows,
    timestamp_rows,
    original_headers,
):
    output_dir = output_root / side
    csv_dir = output_dir / "CSV"
    png_dir = output_dir / "PNG"
    assign_duplicate_indexes(duplicate_run_rows)
    assign_duplicate_indexes(timestamp_rows)

    write_csv(csv_dir / "file_summary.csv", file_summaries, list(file_summaries[0].keys()))
    write_csv(
        csv_dir / "frame_count_by_t_us_second.csv",
        frame_count_rows,
        [
            "subject", "sample_folder", "side", "second_index", "second_start_us",
            "second_end_us", "frame_count",
        ],
    )
    write_csv(
        csv_dir / "frame_count_large.csv",
        large_rows,
        ["subject", "sample_folder", "side", "second_index", "csv_row_number"] + original_headers,
    )
    write_csv(
        csv_dir / "nonzero_adjacent_duplicate_frames.csv",
        duplicate_run_rows,
        [
            "subject", "sample_folder", "side", "repeat_times", "duplicate_index",
            "start_csv_row_number", "end_csv_row_number", "start_frame_idx",
            "end_frame_idx", "start_t_us", "end_t_us", "duration_us",
            "adjacent_duplicate_pair_count", "nonzero_channel_count",
            "repeated_values",
        ],
    )
    write_csv(
        csv_dir / "same_timestamp_runs.csv",
        timestamp_rows,
        [
            "subject", "sample_folder", "side", "t_us", "repeat_times",
            "duplicate_index", "start_csv_row_number", "end_csv_row_number",
            "start_frame_idx", "end_frame_idx",
        ],
    )

    frame_distribution_path = plot_frame_count_distribution(png_dir, frame_count_rows)
    duplicate_distribution_path = plot_duplicate_repeat_distribution(png_dir, duplicate_run_rows)
    timestamp_distribution_path = plot_timestamp_stuck_distribution(png_dir, timestamp_rows)
    return output_dir, csv_dir, png_dir, frame_distribution_path, duplicate_distribution_path, timestamp_distribution_path


def main():
    parser = argparse.ArgumentParser(
        description="Analyze pressure CSV frame counts by t_us and nonzero adjacent duplicate frames."
    )
    parser.add_argument("--root-dirs", nargs="*", default=DEFAULT_ROOT_DIRS)
    parser.add_argument("--file-patterns", nargs="*", default=DEFAULT_FILE_PATTERNS)
    parser.add_argument("--files", nargs="*", default=[])
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()

    output_root = Path(args.output_dir)
    files = find_files(args.root_dirs, args.file_patterns, args.files)
    if not files:
        raise SystemExit("No pressure CSV files found.")

    file_summaries_by_side = defaultdict(list)
    frame_count_rows_by_side = defaultdict(list)
    duplicate_run_rows_by_side = defaultdict(list)
    large_rows_by_side = defaultdict(list)
    timestamp_rows_by_side = defaultdict(list)
    original_headers_by_side = {}

    for index, file_path in enumerate(files, start=1):
        print(f"[{index}/{len(files)}] {file_path}")
        (
            summary,
            second_rows,
            duplicate_rows,
            large_rows,
            timestamp_rows,
            original_headers,
        ) = analyze_pressure_csv(file_path)
        side = summary["side"] or "unknown"
        file_summaries_by_side[side].append(summary)
        frame_count_rows_by_side[side].extend(second_rows)
        duplicate_run_rows_by_side[side].extend(duplicate_rows)
        large_rows_by_side[side].extend(large_rows)
        timestamp_rows_by_side[side].extend(timestamp_rows)
        original_headers_by_side.setdefault(side, original_headers)

    print("")
    print(f"Done. Results saved to: {output_root.resolve()}")
    for side in ("left", "right"):
        if side not in file_summaries_by_side:
            continue
        (
            output_dir,
            csv_dir,
            png_dir,
            frame_distribution_path,
            duplicate_distribution_path,
            timestamp_distribution_path,
        ) = write_side_results(
            output_root,
            side,
            file_summaries_by_side[side],
            frame_count_rows_by_side[side],
            duplicate_run_rows_by_side[side],
            large_rows_by_side[side],
            timestamp_rows_by_side[side],
            original_headers_by_side[side],
        )
        print(f"  {side}: {output_dir}")
        print(f"    CSV: {csv_dir}")
        print(f"      {csv_dir / 'file_summary.csv'}")
        print(f"      {csv_dir / 'frame_count_by_t_us_second.csv'}")
        print(f"      {csv_dir / 'frame_count_large.csv'}")
        print(f"      {csv_dir / 'nonzero_adjacent_duplicate_frames.csv'}")
        print(f"      {csv_dir / 'same_timestamp_runs.csv'}")
        print(f"    PNG: {png_dir}")
        if frame_distribution_path:
            print(f"      {frame_distribution_path}")
        if duplicate_distribution_path:
            print(f"      {duplicate_distribution_path}")
        if timestamp_distribution_path:
            print(f"      {timestamp_distribution_path}")


if __name__ == "__main__":
    main()
