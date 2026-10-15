from __future__ import annotations

import numpy as np
from .io import check_cancel


def longest_run(mask):
    edges = np.diff(np.r_[False, mask, False].astype(int))
    starts, ends = np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)
    return int(np.max(ends - starts, initial=0))


def valid_rows(data):
    return (np.isfinite(data.values).all(axis=1) & (data.values >= 0).all(axis=1)
            & np.isfinite(data.times_us) & (data.times_us >= 0)
            & np.isfinite(data.frame_ids) & (data.frame_ids >= 0)
            & (data.frame_ids == np.floor(data.frame_ids)) & data.source_valid)


def profile_side(data, rules, cancel=None):
    values, times = data.values, data.times_us
    flags = []
    if not len(values):
        return dict(side=data.side, status="empty", n_rows=0, flags="empty_foot"), []
    dt = np.diff(times)
    finite = values[np.isfinite(values)]
    for condition, name in [
        (not np.isfinite(values).all(), "nonfinite_pressure"),
        (np.any(values < 0), "negative_pressure"),
        (not np.isfinite(times).all(), "nonfinite_receive_clock"),
        (np.any(times < 0), "negative_receive_clock"),
        (np.any(dt < 0), "backward_receive_clock"),
        (np.any(dt == 0), "duplicate_receive_clock"),
        (np.any(dt > rules.long_gap_us), "receive_gap_over300ms" if rules.long_gap_us == 300000 else "receive_gap_over_threshold"),
        (np.any(values > rules.model_reference), "above_model_reference"),
        (not data.source_valid.all(), "reconstructed_or_invalid_source_rows"),
        (not np.isfinite(data.frame_ids).all() or np.any(data.frame_ids < 0) or np.any(data.frame_ids != np.floor(data.frame_ids)), "invalid_frame_ids"),
        (len(np.unique(data.frame_ids)) != len(data.frame_ids), "duplicate_frame_ids"),
    ]:
        if condition:
            flags.append(name)
    channels = []
    for cell in range(48):
        check_cancel(cancel)
        series = values[:, cell]
        f = series[np.isfinite(series)]
        over = series > rules.model_reference
        high = f[f > rules.model_reference]
        other = np.delete(values, cell, axis=1)
        other = other[np.isfinite(other)]
        p99 = float(np.quantile(other, .99)) if len(other) else float("nan")
        maximum = float(f.max()) if len(f) else float("nan")
        ratio = maximum / p99 if p99 > 0 else float("inf")
        cv = float(high.std() / high.mean()) if len(high) >= 2 and high.mean() != 0 else float("nan")
        run = longest_run(over)
        suspect = bool(np.isfinite(cv) and cv < rules.d7_cv_max and run > rules.d7_run_min and ratio > rules.d7_ratio_min)
        channels.append(dict(side=data.side, channel=cell+1, n=len(f), mean=float(f.mean()) if len(f) else None,
                             std=float(f.std()) if len(f) else None, p99=float(np.quantile(f,.99)) if len(f) else None,
                             max=maximum, zero_rate=float(np.mean(f == 0)) if len(f) else None,
                             above_clip_count=int(over.sum()), cv_above_clip=cv, longest_above_clip_run=run,
                             max_to_other_channels_p99=ratio, d7_suspect=suspect))
    if any(c['d7_suspect'] for c in channels):
        flags.append("D7_suspect_channel")
    row = dict(side=data.side, status="readable", n_rows=len(values), flags="|".join(flags),
               unusable_rows=int((~valid_rows(data)).sum()),
               dt_zero_count=int((dt == 0).sum()), dt_negative_count=int((dt < 0).sum()),
               long_gap_count=int((dt > rules.long_gap_us).sum()),
               dt_max_ms=float(np.nanmax(dt)/1000) if len(dt) else None,
               above_clip_count=int((values > rules.model_reference).sum()),
               value_max=float(finite.max()) if len(finite) else None,
               start_time_s=float(times[0]/1e6),end_time_s=float(times[-1]/1e6),
               dt_median_ms=float(np.median(dt)/1000) if len(dt) else None)
    return row, channels


def time_events(data,rules):
    events=[]
    usable=valid_rows(data)
    for i in range(len(data.times_us)):
        reasons=[]
        if not np.isfinite(data.times_us[i]): reasons.append('nonfinite_receive_clock')
        if i:
            dt=data.times_us[i]-data.times_us[i-1]
            if dt==0: reasons.append('duplicate_receive_clock')
            if dt<0: reasons.append('backward_receive_clock')
            if dt>rules.long_gap_us: reasons.append('receive_gap')
        else: dt=None
        if not usable[i]: reasons.append('unusable_row')
        if reasons:
            events.append(dict(side=data.side,row_index=i,time_us=float(data.times_us[i]),
                               delta_us=float(dt) if dt is not None else None,reasons='|'.join(reasons)))
    return events
