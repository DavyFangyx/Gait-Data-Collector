from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
import os
import tempfile
import uuid
import numpy as np

from .io import check_cancel, digest, load_metadata, read_side, write_csv, write_json
from .preprocessing import process_side
from .quality import profile_side, valid_rows, time_events
from .schema import Cancelled, Flag, PIPELINE_VERSION, Profile, Rules, SCHEMA_VERSION


def _npz(path, **arrays):
    fd, tmp = tempfile.mkstemp(prefix=".writing_",dir=path.parent)
    try:
        with os.fdopen(fd,"wb") as f:
            np.savez_compressed(f,**arrays)
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)


def run_pipeline(recording, output_root, *, profile=None, rules=None, layout_dir=None, cancel=None, progress=None):
    """Read finalized recording only; append one self-contained immutable run.

    completed means computation finished, not that data passed quality checks.
    All outputs are outside the source recording; no trained model is needed.
    """
    profile, rules = profile or Profile(), rules or Rules()
    profile.validate(); rules.validate()
    recording = Path(recording).resolve(strict=True)
    if not recording.is_dir(): raise ValueError("Recording must be a directory")
    output_root = Path(output_root).resolve()
    if output_root == recording or recording in output_root.parents:
        raise ValueError("Analysis output must be outside the raw recording")
    check_cancel(cancel)
    output_root.mkdir(parents=True,exist_ok=True)
    run = output_root / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_')+uuid.uuid4().hex[:12])
    run.mkdir()
    state = dict(schema_version=SCHEMA_VERSION,pipeline_version=PIPELINE_VERSION,
                 recording_name=recording.name,status="running",profile=profile.to_dict(),
                 physical_sync_certified=False,model_windows="not_evaluated")
    write_json(run/'run.json',state)
    tracked, manifest = {}, []
    try:
        meta_path = recording/'meta.json'
        if meta_path.exists(): tracked[meta_path] = digest(meta_path,cancel)
        meta, expected, notes = load_metadata(recording)
        if any((recording/f'pressure_{s}.csv').exists() for s in ('left','right') if s not in expected):
            notes.append('pressure_file_for_disabled_sensor_not_evaluated')
        write_json(run/'profile.json',dict(profile=profile.to_dict(),rules=asdict(rules),
                   flag_bits={f.name:int(f) for f in Flag},
                   future_information_used=profile.offline_future_samples,
                   resampling="none; temporal filters require regular receive-time spans",
                   pressure_units="source output units; not independently calibrated",
                   channel_order="CSV 1..48; no additional row reversal",
                   timebase=meta.get('timebase','unknown receive-clock semantics'),
                   timestamp_kind="host receipt proxy; not hardware sampling/exposure"))
        rows, channels, spans, comparisons, bindings, events = [], [], [], {}, {}, []
        processed = run/'processed'/profile.method
        processed.mkdir(parents=True)
        for index,side in enumerate(expected):
            check_cancel(cancel)
            if progress: progress(f"检查 {side} ({index+1}/{len(expected)})")
            path=recording/f'pressure_{side}.csv'
            if path.exists():
                tracked[path]=digest(path,cancel)
                manifest.append(dict(file=path.name,sha256=tracked[path],bytes=path.stat().st_size))
            try:
                data=read_side(path,side,cancel)
            except (OSError,ValueError) as exc:
                rows.append(dict(side=side,status='unreadable',n_rows=0,flags='missing_or_unreadable_foot',error=str(exc)))
                continue
            row,per_channel=profile_side(data,rules,cancel)
            rows.append(row); channels.extend(per_channel)
            events.extend(time_events(data,rules))
            if not len(data.values): continue
            values,flags,side_spans,binding=process_side(data,profile,rules,layout_dir,cancel)
            for channel in per_channel:
                if channel['d7_suspect']: flags[:,channel['channel']-1] |= int(Flag.SUSPECT_CHANNEL)
            spans.extend(side_spans); bindings[side]=binding
            if binding:
                tracked[Path(layout_dir)/(side+'foot_dots.csv')]=binding['sha256']
            check_cancel(cancel)
            out_file=processed/f'pressure_{side}.csv'
            # Compute eligibility once, rather than once per generated CSV row.
            eligible=valid_rows(data)
            fields=['frame_idx','t_us','valid_mask','usable']+[str(c) for c in range(1,49)]
            def records_fast():
                for i in range(len(values)):
                    if i%1024 == 0: check_cancel(cancel)
                    yield dict(frame_idx=float(data.frame_ids[i]),t_us=float(data.times_us[i]),
                               valid_mask=int(data.source_valid[i]),usable=int(eligible[i]),
                               **{str(c+1):float(values[i,c]) for c in range(48)})
            write_csv(out_file,records_fast(),fields)
            _npz(processed/f'{side}_comparison.npz',source=data.values,processed=values,
                 time_us=data.times_us,frame_idx=data.frame_ids,flags=flags,usable=eligible,
                 source_valid=data.source_valid)
            comparisons[side] = {'file':str(out_file.relative_to(run)),
                                  'sha256':digest(out_file,cancel),'samples':len(values),
                                  'comparison_file':str((processed/f'{side}_comparison.npz').relative_to(run)),
                                  'comparison_sha256':digest(processed/f'{side}_comparison.npz',cancel)}
        check_cancel(cancel)
        for path,expected_sha in tracked.items():
            if digest(path,cancel) != expected_sha: raise RuntimeError(f"Source changed during analysis: {path.name}")
        write_csv(run/'recording_quality.csv',rows)
        write_csv(run/'channel_quality.csv',channels)
        write_csv(run/'review_queue.csv',[r for r in rows if r.get('flags')])
        write_csv(run/'time_segments.csv',spans)
        write_csv(run/'time_events.csv',events)
        by_side={r['side']:r for r in rows}
        write_csv(run/'foot_pair_review.csv',[dict(expected_sides='|'.join(expected),
                  left_rows=by_side.get('left',{}).get('n_rows'),right_rows=by_side.get('right',{}).get('n_rows'),
                  row_count_mismatch=(len(expected)==2 and by_side['left']['n_rows']!=by_side['right']['n_rows']),
                  note='Different row counts alone do not prove missing samples or physical misalignment')])
        if meta_path in tracked:
            manifest.append(dict(file='meta.json',sha256=tracked[meta_path],bytes=meta_path.stat().st_size))
        write_json(run/'input_manifest.json',dict(recording_name=recording.name,files=manifest,
                   expected_sides=expected,layout=bindings,notes=notes))
        if not expected:
            quality='not_applicable'
        elif any(r['status']!='readable' for r in rows): quality='error'
        elif notes or any(r.get('flags') for r in rows): quality='review'
        else: quality='no_flags_detected'
        write_json(processed/'processing_receipt.json',dict(method=profile.method,outputs=comparisons,layout=bindings,
                   future_information_used=profile.offline_future_samples,physical_sync_certified=False,
                   temporal_skipped_spans=sum(bool(s['skip_reason']) for s in spans),
                   original_modified=False,resampling_performed=False))
        receipt=dict(scan_status='completed',data_status=quality,expected_sides=expected,
                     notes=notes,model_window_status='not_evaluated',physical_sync_certified=False,
                     original_modified=False,recording_rows=rows,outputs=comparisons,
                     limitations=['D7 is a triage rule, not a hardware diagnosis',
                                  'Reference amplitude is not an ADC limit',
                                  'Receive-time duplicates are retained; saved order may already be sorted',
                                  'No motion truth, physical synchronization, or model quality certification'])
        write_json(run/'raw_check_receipt.json',receipt)
        artifacts={str(p.relative_to(run)):digest(p,cancel) for p in sorted(run.rglob('*')) if p.is_file() and p.name!='run.json'}
        write_json(run/'artifact_manifest.json',artifacts)
        state.update(status='completed',data_status=quality)
        state['artifact_manifest_sha256']=digest(run/'artifact_manifest.json',cancel)
        write_json(run/'run.json',state)
        if progress: progress(f"完成：{quality}")
        return run
    except Exception as exc:
        state.update(status='cancelled' if isinstance(exc,Cancelled) else 'failed',error=str(exc))
        write_json(run/'run.json',state)
        raise


def load_completed_run(run,cancel=None):
    """Verify persisted outputs before showing a historical comparison."""
    import json
    run=Path(run).resolve()
    state=json.loads((run/'run.json').read_text(encoding='utf-8'))
    if state.get('status')!='completed': raise ValueError('Run is not completed')
    if digest(run/'artifact_manifest.json',cancel)!=state.get('artifact_manifest_sha256'):
        raise ValueError('Saved artifact manifest hash mismatch')
    artifacts=json.loads((run/'artifact_manifest.json').read_text(encoding='utf-8'))
    for name,sha in artifacts.items():
        path=(run/name).resolve()
        if run not in path.parents: raise ValueError('Artifact path escapes run directory')
        if digest(path,cancel)!=sha: raise ValueError('Saved output hash mismatch')
    receipt=json.loads((run/'raw_check_receipt.json').read_text(encoding='utf-8'))
    arrays={}
    for side,item in receipt['outputs'].items():
        for key,hash_key in [('file','sha256'),('comparison_file','comparison_sha256')]:
            path=(run/item[key]).resolve()
            if run not in path.parents: raise ValueError('Output path escapes run directory')
            if digest(path,cancel)!=item[hash_key]: raise ValueError('Saved output hash mismatch')
        with np.load(run/item['comparison_file'],allow_pickle=False) as data:
            arrays[side]={k:data[k].copy() for k in data.files}
    return receipt,arrays
