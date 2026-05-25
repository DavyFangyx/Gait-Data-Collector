#!/usr/bin/env python
# coding: utf-8

import os
import re
import sys

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy import signal
from scipy.stats import cauchy, laplace, norm, pearsonr, spearmanr, t as student_t

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
for style_path in (
    SCRIPT_DIR,
    os.path.join(SCRIPT_DIR, ".."),
):
    style_path = os.path.abspath(style_path)
    if style_path not in sys.path:
        sys.path.insert(0, style_path)

try:
    from plot_style import (
        COLORS,
        SAVE_DIRS,
        add_subplot_label,
        apply_measurement_style,
        clear_experiment_outputs,
        save_figure,
    )
except ImportError:
    COLORS = {
        "blue": "#2b7bbb",
        "red": "#c43c39",
        "gray": "#777777",
    }
    SAVE_DIRS = [
        os.path.join(SCRIPT_DIR, "noise_analysis_results"),
        os.path.join(SCRIPT_DIR, "noise_analysis_results"),
    ]

    def apply_measurement_style():
        plt.rcParams.update({
            "figure.dpi": 120,
            "savefig.dpi": 300,
            "font.size": 9,
            "axes.spines.top": False,
            "axes.spines.right": False,
        })

    def clear_experiment_outputs(*_args, **_kwargs):
        return None

    def add_subplot_label(ax, label):
        ax.text(
            0.02,
            0.98,
            label,
            transform=ax.transAxes,
            ha="left",
            va="top",
            fontsize=10,
            fontweight="bold",
        )

    def save_figure(fig, save_dir, stem):
        os.makedirs(save_dir, exist_ok=True)
        path = os.path.join(save_dir, f"{stem}.png")
        fig.savefig(path, bbox_inches="tight")
        return path

# Default input. You can also pass a file path on the command line:
# python 01_Noise_Analysis.py Pressuredata_workspace/PressureOnly_right_20260521_022533.csv
INPUT_FILE = os.path.join(
    "Pressuredata_workspace",
    "PressureOnly_right_20260521_022533.csv",
)

apply_measurement_style()
SAVE_DIR = SAVE_DIRS[1]
CSV_DIR = os.path.join(SAVE_DIR, "csv")
os.makedirs(CSV_DIR, exist_ok=True)
clear_experiment_outputs((SCRIPT_DIR, CSV_DIR), SAVE_DIR, ("01_", "01a_", "01b_"))

FS_DEFAULT = 200
FS = FS_DEFAULT
DT = 1 / FS_DEFAULT
V_REF = 5.0
ADC_BITS = 16
LSB = V_REF / (2**15)


def resolve_input_path(path):
    if os.path.isabs(path):
        return path
    candidates = [
        os.path.join(os.getcwd(), path),
        os.path.join(SCRIPT_DIR, "..", "..", path),
    ]
    for candidate in candidates:
        candidate = os.path.abspath(candidate)
        if os.path.exists(candidate):
            return candidate
    return os.path.abspath(candidates[0])


def parse_channel_name(name):
    row_part, col_part = name.split("_")
    return int(row_part[1:]), int(col_part[1:])


def is_numeric_channel_name(name):
    text = str(name).strip()
    return re.fullmatch(r"\d+", text) is not None


def estimate_sampling_frequency(time_s):
    time_s = np.asarray(time_s, dtype=float)
    time_s = time_s[np.isfinite(time_s)]
    if time_s.size < 2:
        return float(FS_DEFAULT)
    diffs = np.diff(time_s)
    positive_diffs = diffs[np.isfinite(diffs) & (diffs > 0)]
    if positive_diffs.size == 0:
        return float(FS_DEFAULT)
    return float(1.0 / np.median(positive_diffs))


def find_single_channel_columns(df):
    time_col = None
    raw_col = None
    ground_col = None

    for col in df.columns:
        lower = col.lower()
        if time_col is None and "time" in lower:
            time_col = col
        if raw_col is None and ("tactileraw" in lower or lower in {"rawvalue", "raw"}):
            raw_col = col
        if ground_col is None and ("groundref" in lower or lower in {"rawground", "ground"}):
            ground_col = col

    if raw_col is None and len(df.columns) >= 2:
        raw_col = df.columns[1]
    if ground_col is None and len(df.columns) >= 3:
        ground_col = df.columns[2]
    return time_col, raw_col, ground_col


def load_noise_data(file_path):
    df = pd.read_csv(file_path)
    array_cols = [
        col for col in df.columns
        if col.startswith("R") and "_C" in col
    ]
    pressure_channel_cols = [
        col for col in df.columns
        if is_numeric_channel_name(col)
    ]

    if array_cols:
        channel_cols = sorted(array_cols, key=parse_channel_name)
        sample_index = (
            df["Index"].to_numpy(dtype=float)
            if "Index" in df.columns
            else np.arange(len(df), dtype=float)
        )
        time_s = (sample_index - sample_index[0]) * DT
        channel_values = df[channel_cols].apply(pd.to_numeric, errors="coerce")
        channel_baselines = channel_values.mean(axis=0)
        channel_residuals = channel_values - channel_baselines

        reference_baseline = np.nan
        reference_residual = None
        reference_name = ""
        if "VGND" in df.columns:
            reference_name = "VGND"
            vgnd = df["VGND"].astype(float)
            reference_baseline = float(vgnd.mean())
            reference_residual = (vgnd - reference_baseline).to_numpy()

        return {
            "mode": "array_baseline_removed",
            "title": "Noise Characterization — 6×6 Array Baseline Removed",
            "object_label": f"{len(channel_cols)}阵点合并",
            "reference_name": reference_name,
            "time_s": time_s,
            "channel_cols": channel_cols,
            "channel_values": channel_values,
            "channel_baselines": channel_baselines,
            "channel_residuals": channel_residuals,
            "reference_baseline": reference_baseline,
            "reference_residual": reference_residual,
            "fs_hz": estimate_sampling_frequency(time_s),
        }

    if pressure_channel_cols:
        channel_cols = sorted(pressure_channel_cols, key=lambda item: int(str(item)))
        if "time_s" in df.columns:
            time_s = pd.to_numeric(df["time_s"], errors="coerce").to_numpy(dtype=float)
        elif "time_us" in df.columns:
            time_s = pd.to_numeric(df["time_us"], errors="coerce").to_numpy(dtype=float) / 1e6
        elif "local_frame_index" in df.columns:
            sample_index = pd.to_numeric(df["local_frame_index"], errors="coerce").to_numpy(dtype=float)
            time_s = (sample_index - sample_index[0]) * DT
        else:
            time_s = np.arange(len(df), dtype=float) * DT

        if np.isfinite(time_s).any():
            first_time = time_s[np.isfinite(time_s)][0]
            time_s = time_s - first_time
        channel_values = df[channel_cols].apply(pd.to_numeric, errors="coerce")
        channel_baselines = channel_values.mean(axis=0)
        channel_residuals = channel_values - channel_baselines

        return {
            "mode": "pressure_only_numeric_channels",
            "title": f"Noise Characterization — PressureOnly {len(channel_cols)} Channels",
            "object_label": f"{len(channel_cols)}个压力通道",
            "reference_name": "",
            "time_s": time_s,
            "channel_cols": channel_cols,
            "channel_values": channel_values,
            "channel_baselines": channel_baselines,
            "channel_residuals": channel_residuals,
            "reference_baseline": np.nan,
            "reference_residual": None,
            "fs_hz": estimate_sampling_frequency(time_s),
        }

    time_col, raw_col, ground_col = find_single_channel_columns(df)
    if raw_col is None:
        raise ValueError("未找到可分析的单通道原始信号列或 R*_C* 阵列列")

    raw = df[raw_col].astype(float)
    raw_baseline = float(raw.mean())
    time_s = (
        df[time_col].to_numpy(dtype=float) / 1000
        if time_col is not None
        else np.arange(len(df), dtype=float) * DT
    )

    channel_residuals = pd.DataFrame({"Signal": raw - raw_baseline})
    channel_values = pd.DataFrame({"Signal": raw})
    channel_baselines = pd.Series({"Signal": raw_baseline})

    reference_baseline = np.nan
    reference_residual = None
    reference_name = ""
    if ground_col is not None:
        reference_name = ground_col
        ground = df[ground_col].astype(float)
        reference_baseline = float(ground.mean())
        reference_residual = (ground - reference_baseline).to_numpy()

    return {
        "mode": "single_channel_baseline_removed",
        "title": "Noise Characterization — Single Channel Baseline Removed",
        "object_label": "单通道信号",
        "reference_name": reference_name,
        "time_s": time_s,
        "channel_cols": ["Signal"],
        "channel_values": channel_values,
        "channel_baselines": channel_baselines,
        "channel_residuals": channel_residuals,
        "reference_baseline": reference_baseline,
        "reference_residual": reference_residual,
        "fs_hz": estimate_sampling_frequency(time_s),
    }


def fit_noise_distribution(data, x_values, bin_edges):
    if len(data) == 0 or np.nanstd(data) == 0:
        center = 0.0 if len(data) == 0 else float(np.nanmean(data))
        pdf = np.zeros_like(x_values, dtype=float)
        if pdf.size:
            pdf[np.argmin(np.abs(x_values - center))] = 1.0
        return "Degenerate", pdf, [(0.0, "Degenerate", pdf)]

    candidates = []
    hist_density, _ = np.histogram(data, bins=bin_edges, density=True)
    bin_weights = np.histogram(data, bins=bin_edges)[0]
    min_scale = 0.5

    def add_candidate(name, pdf_x, cdf_edges):
        bin_widths = np.diff(bin_edges)
        pdf_bins = np.diff(cdf_edges) / bin_widths
        weighted_sse = np.average((hist_density - pdf_bins) ** 2, weights=bin_weights)
        candidates.append((weighted_sse, name, pdf_x))

    fit_data = data
    if len(data) > 50000:
        rng = np.random.default_rng(20260507)
        fit_data = rng.choice(data, size=50000, replace=False)

    std_gauss = max(np.std(data), min_scale)
    add_candidate(
        "Gaussian",
        norm.pdf(x_values, 0.0, std_gauss),
        norm.cdf(bin_edges, 0.0, std_gauss),
    )

    _, scale_laplace = laplace.fit(fit_data, floc=0.0)
    scale_laplace = max(scale_laplace, min_scale)
    add_candidate(
        "Laplace",
        laplace.pdf(x_values, 0.0, scale_laplace),
        laplace.cdf(bin_edges, 0.0, scale_laplace),
    )

    df_t, _, scale_t = student_t.fit(fit_data, floc=0.0)
    scale_t = max(scale_t, min_scale)
    add_candidate(
        "Student-t",
        student_t.pdf(x_values, df_t, 0.0, scale_t),
        student_t.cdf(bin_edges, df_t, 0.0, scale_t),
    )

    _, scale_cauchy = cauchy.fit(fit_data, floc=0.0)
    scale_cauchy = max(scale_cauchy, min_scale)
    add_candidate(
        "Cauchy",
        cauchy.pdf(x_values, 0.0, scale_cauchy),
        cauchy.cdf(bin_edges, 0.0, scale_cauchy),
    )

    best_score, best_name, best_pdf = min(candidates, key=lambda item: item[0])
    return best_name, best_pdf, candidates


def compute_noise_metrics(residual):
    residual = np.asarray(residual, dtype=float)
    residual = residual[np.isfinite(residual)]
    if residual.size == 0:
        return {
            "lower_bound": np.nan,
            "upper_bound": np.nan,
            "valid_mask": np.array([], dtype=bool),
            "valid_data": np.array([], dtype=float),
            "mean_LSB": np.nan,
            "std_LSB": np.nan,
            "pp_LSB": np.nan,
            "sigma6_LSB": np.nan,
            "ENOB_bits": np.nan,
            "SNR_dB": np.nan,
            "outlier_count": 0,
            "valid_count": 0,
        }

    q1, q3 = np.percentile(residual, [25, 75])
    iqr = q3 - q1
    lower_bound = q1 - 3 * iqr
    upper_bound = q3 + 3 * iqr
    valid_mask = (residual >= lower_bound) & (residual <= upper_bound)
    valid = residual[valid_mask]

    mean_value = float(np.mean(valid))
    std_value = float(np.std(valid))
    pp_value = float(np.max(valid) - np.min(valid))
    sigma_6 = 6 * std_value
    enob_value = ADC_BITS - np.log2(std_value * 6.6) if std_value > 0 else np.nan
    full_scale = 2 ** (ADC_BITS - 1)
    snr_value = 20 * np.log10(full_scale / std_value) if std_value > 0 else np.inf

    return {
        "lower_bound": float(lower_bound),
        "upper_bound": float(upper_bound),
        "valid_mask": valid_mask,
        "valid_data": valid,
        "mean_LSB": mean_value,
        "std_LSB": std_value,
        "pp_LSB": pp_value,
        "sigma6_LSB": float(sigma_6),
        "ENOB_bits": float(enob_value),
        "SNR_dB": float(snr_value),
        "outlier_count": int(np.size(residual) - np.size(valid)),
        "valid_count": int(np.size(valid)),
    }


def compute_simple_channel_metrics(values):
    values = np.asarray(values, dtype=float)
    values = values[np.isfinite(values)]
    if values.size == 0:
        return {
            "mean_LSB": np.nan,
            "sigma_LSB": np.nan,
            "sigma6_LSB": np.nan,
            "peak_to_peak_LSB": np.nan,
            "min_LSB": np.nan,
            "max_LSB": np.nan,
            "valid_count": 0,
        }

    sigma = float(np.std(values))
    return {
        "mean_LSB": float(np.mean(values)),
        "sigma_LSB": sigma,
        "sigma6_LSB": 6.0 * sigma,
        "peak_to_peak_LSB": float(np.max(values) - np.min(values)),
        "min_LSB": float(np.min(values)),
        "max_LSB": float(np.max(values)),
        "valid_count": int(values.size),
    }


input_arg = sys.argv[1] if len(sys.argv) > 1 else INPUT_FILE
FILE_NOISE = resolve_input_path(input_arg)
if not os.path.exists(FILE_NOISE):
    raise FileNotFoundError(f"输入文件不存在: {FILE_NOISE}")

data = load_noise_data(FILE_NOISE)
time_s = data["time_s"]
channel_cols = data["channel_cols"]
channel_values_df = data["channel_values"]
FS = data.get("fs_hz") or FS_DEFAULT
DT = 1 / FS if FS > 0 else 1 / FS_DEFAULT

print(f"采样频率: {FS:.3f} Hz")
print(f"采样周期: {DT*1000:.3f} ms")
print(f"LSB电压: {LSB*1000:.4f} mV")

channel_stats = []
for col in channel_cols:
    values = channel_values_df[col].to_numpy(dtype=float)
    metrics = compute_simple_channel_metrics(values)
    channel_stats.append(
        {
            "channel": col,
            "mean_LSB": metrics["mean_LSB"],
            "sigma_LSB": metrics["sigma_LSB"],
            "sigma6_LSB": metrics["sigma6_LSB"],
            "peak_to_peak_LSB": metrics["peak_to_peak_LSB"],
            "min_LSB": metrics["min_LSB"],
            "max_LSB": metrics["max_LSB"],
            "valid_count": metrics["valid_count"],
        }
    )

channel_stats_df = pd.DataFrame(channel_stats)

print(f"数据文件: {FILE_NOISE}")
print(f"数据格式: {data['mode']}")
print(f"时间采样点数: {len(time_s)}")
print(f"通道数量: {len(channel_cols)}")
finite_time = time_s[np.isfinite(time_s)]
duration_s = float(finite_time[-1] - finite_time[0]) if len(finite_time) else 0.0
print(f"采样时长: {duration_s:.2f} s")

print("=" * 70)
print("【48通道简单噪声统计】")
print("=" * 70)
print(channel_stats_df[[
    "channel",
    "mean_LSB",
    "sigma_LSB",
    "sigma6_LSB",
    "peak_to_peak_LSB",
]].to_string(index=False))

output_csv = os.path.join(CSV_DIR, "01_channel_simple_noise_stats.csv")
channel_stats_df.to_csv(output_csv, index=False)

channel_order = np.arange(len(channel_stats_df), dtype=float)
channel_labels = channel_stats_df["channel"].astype(str).tolist()
step = max(1, len(channel_labels) // 24)

fig_mean, ax_mean = plt.subplots(figsize=(12, 4.8))
mean_values = channel_stats_df["mean_LSB"].to_numpy(dtype=float)
sigma6_values = channel_stats_df["sigma6_LSB"].to_numpy(dtype=float)
ax_mean.plot(channel_order, mean_values, color=COLORS["blue"], marker="o",
             markersize=3.5, linewidth=1.2, label="Mean")
ax_mean.fill_between(
    channel_order,
    mean_values - sigma6_values,
    mean_values + sigma6_values,
    color=COLORS["blue"],
    alpha=0.18,
    label="Mean ± 6σ",
)
ax_mean.set_xlabel("Channel")
ax_mean.set_ylabel("ADC (LSB)")
ax_mean.set_title("Per-channel mean with 6σ band")
ax_mean.set_xticks(channel_order[::step])
ax_mean.set_xticklabels(channel_labels[::step])
ax_mean.grid(axis="y", alpha=0.25)
ax_mean.legend(frameon=False)
plt.tight_layout()
p_mean = save_figure(fig_mean, SAVE_DIR, "01_channel_mean_6sigma_band")
plt.close(fig_mean)

fig_pp, ax_pp = plt.subplots(figsize=(12, 4.8))
ax_pp.bar(
    channel_order,
    channel_stats_df["peak_to_peak_LSB"],
    color=COLORS["red"],
    alpha=0.82,
)
ax_pp.set_xlabel("Channel")
ax_pp.set_ylabel("Peak-to-peak (LSB)")
ax_pp.set_title("Per-channel peak-to-peak noise")
ax_pp.set_xticks(channel_order[::step])
ax_pp.set_xticklabels(channel_labels[::step])
ax_pp.grid(axis="y", alpha=0.25)
plt.tight_layout()
p_pp = save_figure(fig_pp, SAVE_DIR, "01_channel_peak_to_peak")
plt.close(fig_pp)

print("=" * 70)
print("【输出文件】")
print("=" * 70)
print(f"CSV: {output_csv}")
print(f"均值 + 6sigma 分布带图: {p_mean}")
print(f"峰峰值分布图: {p_pp}")
