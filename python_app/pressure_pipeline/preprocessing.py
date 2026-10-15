from __future__ import annotations

import csv
from pathlib import Path
import numpy as np
from scipy.ndimage import median_filter
from scipy.signal import butter, sosfiltfilt, savgol_filter

from .io import check_cancel, digest
from .quality import valid_rows
from .schema import Flag


def load_neighbors(path, suspect=()):
    with Path(path).open(encoding="utf-8-sig", newline="") as f:
        rows = sorted(csv.DictReader(f), key=lambda r: int(r['dot_index']))
    if [int(r['dot_index']) for r in rows] != list(range(1,49)):
        raise ValueError("Layout must contain each channel ID 1..48 exactly once")
    xy = np.asarray([[float(r['norm_x']), float(r['norm_y'])] for r in rows])
    if not np.isfinite(xy).all() or len(np.unique(xy,axis=0)) != 48:
        raise ValueError("Layout contains invalid or duplicate coordinates")
    distances = ((xy[:,None] - xy[None,:])**2).sum(-1)
    np.fill_diagonal(distances, np.inf)
    if suspect:
        distances[:, np.asarray(suspect)-1] = np.inf
    near = np.argsort(distances, axis=1, kind="stable")[:,:4]
    if not np.isfinite(np.take_along_axis(distances,near,axis=1)).all():
        raise ValueError("Insufficient valid neighbor cells")
    return near, digest(path)


def segments(data, rules):
    """Index-contiguous, valid, strictly increasing receive-time spans."""
    good = valid_rows(data)
    current = []
    for i in range(len(good)):
        if current and (not good[i] or data.times_us[i] <= data.times_us[i-1]
                        or data.times_us[i]-data.times_us[i-1] > rules.long_gap_us):
            yield np.asarray(current, dtype=int)
            current = []
        if good[i]:
            current.append(i)
    if current:
        yield np.asarray(current,dtype=int)


def process_side(data, profile, rules, layout_dir=None, cancel=None):
    profile.validate(); rules.validate()
    values = data.values.copy()
    good = valid_rows(data)
    flags = np.full(values.shape, int(Flag.SOURCE_RETAINED), dtype=np.uint16)
    flags[~good] |= int(Flag.UNUSABLE)
    flags[data.values > rules.model_reference] |= int(Flag.ABOVE_MODEL_REFERENCE)
    dup = np.r_[False, np.diff(data.times_us) == 0]
    flags[dup] |= int(Flag.DUPLICATE_RECEIVE_TIME)
    suspect = profile.suspect_left if data.side == "left" else profile.suspect_right
    if suspect:
        flags[:,np.asarray(suspect)-1] |= int(Flag.SUSPECT_CHANNEL)
    receipts, binding = [], {}
    if profile.method in ("spatial25", "replace4"):
        if layout_dir is None:
            raise ValueError("Spatial processing requires an explicit foot_sensor_layout directory")
        path = Path(layout_dir) / (data.side + "foot_dots.csv")
        near, sha = load_neighbors(path, suspect)
        binding = {'file':path.name, 'sha256':sha, 'neighbors_1based':(near+1).tolist()}
        rows = np.flatnonzero(good)
        if profile.method == "spatial25":
            values[rows] = .75*data.values[rows]+.25*data.values[rows][:,near].mean(axis=2)
            flags[rows] |= int(Flag.SPATIALLY_ESTIMATED)
        elif suspect:
            for cell in np.asarray(suspect)-1:
                values[rows,cell] = data.values[rows][:,near[cell]].mean(axis=1)
                flags[rows,cell] &= np.uint16(~int(Flag.SOURCE_RETAINED) & 65535)
                flags[rows,cell] |= int(Flag.SPATIALLY_ESTIMATED)
    temporal = profile.method not in ("original","spatial25","replace4")
    for chunk in segments(data,rules):
        check_cancel(cancel)
        times = data.times_us[chunk]
        dt = np.diff(times)
        step = float(np.median(dt)) if len(dt) else None
        regular = bool(step is not None and np.all(np.abs(dt-step) <= max(1.0,step*rules.regular_tolerance)))
        fs = 1e6/step if step else None
        reason, applied = "", False
        if temporal:
            if not regular:
                reason = "irregular_or_single_sample_span_retained"
            else:
                block = data.values[chunk].copy()
                method = profile.method
                if method in ("median3","median5"):
                    width = int(method[-1])
                    if len(chunk) < width:
                        reason = "short_span_retained"
                    else:
                        block = median_filter(block,size=(width,1),mode="nearest")
                elif method.startswith("lowpass"):
                    fc = float(method.removeprefix('lowpass'))
                    if fs <= 2*fc:
                        reason = "cutoff_not_below_nyquist_retained"
                    elif len(chunk) <= 12:
                        reason = "short_span_retained"
                    else:
                        block = sosfiltfilt(butter(2,fc,fs=fs,output='sos'),block,axis=0)
                elif method == "savgol5":
                    if len(chunk) < 5:
                        reason = "short_span_retained"
                    else:
                        block = savgol_filter(block,5,2,axis=0,mode="interp")
                elif method == "hampel5":
                    if len(chunk) < 5:
                        reason = "short_span_retained"
                    else:
                        padded=np.pad(block,((2,2),(0,0)),mode='edge')
                        windows=np.stack([padded[i:i+len(block)] for i in range(5)])
                        med=np.median(windows,axis=0); mad=np.median(np.abs(windows-med),axis=0)
                        block=np.where(np.abs(block-med)>np.maximum(3*1.4826*mad,20),med,block)
                elif method == "ema05":
                    for i in range(1,len(block)):
                        if i%1024 == 0: check_cancel(cancel)
                        block[i]=.5*block[i]+.5*block[i-1]
                if not reason:
                    values[chunk]=np.maximum(block,0)
                    flags[chunk] |= int(Flag.TEMPORALLY_FILTERED)
                    applied=True
            if reason:
                flags[chunk] |= int(Flag.PROCESSING_SKIPPED)
        receipts.append(dict(side=data.side,start_row=int(chunk[0]),end_row=int(chunk[-1]),
                             samples=len(chunk),start_time_us=float(times[0]),end_time_us=float(times[-1]),
                             estimated_receive_fps=fs,regular=regular,filter_applied=applied,skip_reason=reason))
    if not np.array_equal(values[~good],data.values[~good],equal_nan=True):
        raise AssertionError("Invalid source rows must remain unchanged")
    return values, flags, receipts, binding
