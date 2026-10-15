import csv
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from python_app.pressure_pipeline.io import digest, discover_recordings, read_side, write_csv
from python_app.pressure_pipeline.preprocessing import process_side
from python_app.pressure_pipeline.quality import profile_side
from python_app.pressure_pipeline.schema import Cancelled, Flag, Profile, Rules
from python_app.pressure_pipeline.service import load_completed_run, run_pipeline

LAYOUT=Path(__file__).resolve().parents[2]/'foot_sensor_layout'


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name); self.rec=self.root/'raw'/'rec_one'; self.rec.mkdir(parents=True)
        self.out=self.root/'analysis'

    def fixture(self,times=None,values=None,sides=('left','right'),inventory=None):
        times=np.arange(40)*25000 if times is None else np.asarray(times)
        values=np.full((len(times),48),100.) if values is None else np.asarray(values)
        if inventory is None:
            inventory=[dict(sensor_id=s+'_foot',enabled=True) for s in sides]
        (self.rec/'meta.json').write_text(json.dumps(dict(pressure=inventory,timebase='host_monotonic_receipt')))
        for side in sides:
            with (self.rec/f'pressure_{side}.csv').open('w',newline='') as f:
                w=csv.writer(f); w.writerow(['frame_idx','t_us']+[str(i) for i in range(1,49)])
                w.writerows([i,t,*v] for i,(t,v) in enumerate(zip(times,values)))

    def test_original_is_exact_and_does_not_touch_sources(self):
        values=np.random.default_rng(4).integers(0,65536,(40,48))
        self.fixture(values=values)
        before={p.name:digest(p) for p in self.rec.iterdir()}
        run=run_pipeline(self.rec,self.out)
        receipt,arrays=load_completed_run(run)
        self.assertEqual(receipt['data_status'],'review')
        self.assertFalse(receipt['physical_sync_certified'])
        for pack in arrays.values():
            np.testing.assert_array_equal(pack['source'],values)
            np.testing.assert_array_equal(pack['processed'],values)
        self.assertEqual(before,{p.name:digest(p) for p in self.rec.iterdir()})
        self.assertEqual(receipt['model_window_status'],'not_evaluated')

    def test_original_csv_preserves_nonfinite_pressure_and_clock(self):
        values=np.full((5,48),100.)
        values[1,0]=np.nan; values[2,1]=np.inf; values[3,2]=-np.inf
        times=np.array([0.,25000.,np.nan,np.inf,100000.])
        self.fixture(times=times,values=values,sides=('left',))
        run=run_pipeline(self.rec,self.out)
        output=read_side(run/'processed'/'original'/'pressure_left.csv','left')
        np.testing.assert_array_equal(output.values,values)
        np.testing.assert_array_equal(output.times_us,times)
        receipt,_=load_completed_run(run)
        self.assertEqual(receipt['data_status'],'review')

    def test_single_foot_and_enabled_inventory(self):
        self.fixture(sides=('left',))
        receipt,_=load_completed_run(run_pipeline(self.rec,self.out))
        self.assertEqual(receipt['data_status'],'no_flags_detected')
        self.assertEqual(receipt['expected_sides'],['left'])

    def test_missing_both_is_discovered_from_meta(self):
        self.fixture(sides=(),inventory=[dict(sensor_id='left_foot',enabled=True),dict(sensor_id='right_foot',enabled=True)])
        self.assertEqual(discover_recordings(self.root/'raw'),[self.rec])
        receipt,_=load_completed_run(run_pipeline(self.rec,self.out))
        self.assertEqual(receipt['data_status'],'error')
        self.assertEqual(len(receipt['recording_rows']),2)

    def test_camera_only_not_applicable(self):
        self.fixture(sides=(),inventory=[])
        receipt,_=load_completed_run(run_pipeline(self.rec,self.out))
        self.assertEqual(receipt['data_status'],'not_applicable')

    def test_empty_missing_and_malformed_are_errors(self):
        self.fixture(times=[])
        (self.rec/'pressure_right.csv').unlink()
        receipt,_=load_completed_run(run_pipeline(self.rec,self.out))
        self.assertEqual([x['status'] for x in receipt['recording_rows']],['empty','unreadable'])
        (self.rec/'pressure_left.csv').write_text('frame_idx,t_us,1,1\n0,1,2,3\n')
        receipt,_=load_completed_run(run_pipeline(self.rec,self.out))
        self.assertEqual(receipt['data_status'],'error')

    def test_receive_clock_duplicates_not_deleted_or_declared_empty(self):
        times=[0,0,25000,400001,400000]
        self.fixture(times=times)
        receipt,arrays=load_completed_run(run_pipeline(self.rec,self.out))
        flags=receipt['recording_rows'][0]['flags']
        for flag in ['duplicate_receive_clock','backward_receive_clock','receive_gap_over300ms']:
            self.assertIn(flag,flags)
        self.assertEqual(len(arrays['left']['source']),5)
        self.assertTrue(arrays['left']['usable'].all())
        self.assertTrue(arrays['left']['flags'][1,0]&Flag.DUPLICATE_RECEIVE_TIME)

    def test_invalid_values_retained_and_tagged(self):
        values=np.full((40,48),100.); values[5,0]=np.nan; values[6,1]=np.inf; values[7,2]=-5
        self.fixture(values=values)
        _,arrays=load_completed_run(run_pipeline(self.rec,self.out,profile=Profile('median3')))
        pack=arrays['left']
        for i in (5,6,7):
            self.assertFalse(pack['usable'][i]); self.assertTrue(np.all(pack['flags'][i]&Flag.UNUSABLE))
            np.testing.assert_array_equal(pack['source'][i],pack['processed'][i])

    def test_d7_applies_to_arbitrary_channel_without_cleaning(self):
        values=np.full((40,48),100.); values[:,1]=20000
        self.fixture(values=values)
        data=read_side(self.rec/'pressure_right.csv','right')
        row,channels=profile_side(data,Rules())
        self.assertTrue(channels[1]['d7_suspect']); self.assertIn('D7',row['flags'])
        run=run_pipeline(self.rec,self.out)
        _,arrays=load_completed_run(run)
        np.testing.assert_array_equal(arrays['right']['processed'],values)

    def test_spatial_neighbors_exclude_configured_suspects(self):
        values=np.full((40,48),10.); values[:,34]=60000
        self.fixture(values=values)
        data=read_side(self.rec/'pressure_right.csv','right')
        result,flags,spans,binding=process_side(data,Profile('replace4',suspect_right=(35,)),Rules(),LAYOUT)
        np.testing.assert_allclose(result[:,34],10)
        np.testing.assert_array_equal(result[:,:34],values[:,:34])
        self.assertTrue(np.all(flags[:,34]&Flag.SPATIALLY_ESTIMATED))
        self.assertFalse(np.any(flags[:,34]&Flag.SOURCE_RETAINED))
        self.assertTrue(np.all(flags[:,34]&Flag.SUSPECT_CHANNEL))
        self.assertTrue(all(35 not in cells for cells in binding['neighbors_1based']))

    def test_spatial_constant_signal_preserved(self):
        self.fixture()
        data=read_side(self.rec/'pressure_left.csv','left')
        result,*_=process_side(data,Profile('spatial25'),Rules(),LAYOUT)
        np.testing.assert_array_equal(result,data.values)

    def test_temporal_no_filter_across_gap_and_uses_actual_fps(self):
        times=np.r_[np.arange(40)*10000,np.arange(40)*10000+1000000]
        values=np.r_[np.full((40,48),10.),np.full((40,48),1000.)]
        self.fixture(times,values)
        data=read_side(self.rec/'pressure_left.csv','left')
        result,flags,spans,_=process_side(data,Profile('lowpass6'),Rules())
        np.testing.assert_allclose(result,values,atol=1e-9)
        self.assertEqual(len(spans),2)
        self.assertTrue(all(s['estimated_receive_fps']==100 for s in spans))

    def test_irregular_temporal_spans_are_retained_not_resampled(self):
        self.fixture(times=np.cumsum([0,10000,20000,10000,30000,10000]))
        data=read_side(self.rec/'pressure_left.csv','left')
        result,flags,spans,_=process_side(data,Profile('median3'),Rules())
        np.testing.assert_array_equal(result,data.values)
        self.assertFalse(spans[0]['filter_applied'])
        self.assertTrue(np.all(flags&Flag.PROCESSING_SKIPPED))

    def test_nyquist_and_short_span_refuse_filter(self):
        self.fixture(times=np.arange(5)*100000)
        data=read_side(self.rec/'pressure_left.csv','left')
        _,_,spans,_=process_side(data,Profile('lowpass6'),Rules())
        self.assertIn('nyquist',spans[0]['skip_reason'])

    def test_all_registered_methods_finite_and_invalid_region_unchanged(self):
        from python_app.pressure_pipeline.schema import METHODS
        values=np.random.default_rng(7).uniform(0,500,(60,48)); values[30]=np.nan
        self.fixture(values=values)
        data=read_side(self.rec/'pressure_left.csv','left')
        for method in METHODS:
            with self.subTest(method=method):
                result,*_=process_side(data,Profile(method,suspect_left=(5,)),Rules(),LAYOUT)
                self.assertTrue(np.isfinite(result[:30]).all()); self.assertTrue(np.isfinite(result[31:]).all())
                np.testing.assert_array_equal(result[30],data.values[30])

    def test_output_in_raw_refused_and_new_runs_do_not_overwrite(self):
        self.fixture()
        with self.assertRaises(ValueError): run_pipeline(self.rec,self.rec/'analysis')
        one=run_pipeline(self.rec,self.out); two=run_pipeline(self.rec,self.out)
        self.assertNotEqual(one,two)
        self.assertEqual(json.loads((one/'run.json').read_text())['status'],'completed')

    def test_cancelled_run_is_not_consumable(self):
        self.fixture(); event=threading.Event()
        def progress(_): event.set()
        with self.assertRaises(Cancelled): run_pipeline(self.rec,self.out,cancel=event,progress=progress)
        run=next(self.out.iterdir())
        self.assertEqual(json.loads((run/'run.json').read_text())['status'],'cancelled')
        with self.assertRaises(ValueError): load_completed_run(run)

    def test_changed_source_fails_receipt(self):
        self.fixture()
        # Mutate metadata after its binding was frozen, before the final check.
        def progress(_): (self.rec/'meta.json').write_text('{"pressure": []}')
        with self.assertRaisesRegex(RuntimeError,'Source changed'):
            run_pipeline(self.rec,self.out,progress=progress)
        run=next(self.out.iterdir())
        self.assertEqual(json.loads((run/'run.json').read_text())['status'],'failed')

    def test_saved_comparison_tamper_rejected(self):
        self.fixture(); run=run_pipeline(self.rec,self.out)
        path=run/'processed/original/left_comparison.npz'
        path.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'hash mismatch'): load_completed_run(run)

    def test_report_tamper_rejected_and_time_events_locate_source_row(self):
        self.fixture(times=[0,25000,50000,450001])
        run=run_pipeline(self.rec,self.out)
        with (run/'time_events.csv').open(encoding='utf-8-sig',newline='') as f:
            events=list(csv.DictReader(f))
        self.assertTrue(any(x['row_index']=='3' and 'receive_gap' in x['reasons'] for x in events))
        (run/'recording_quality.csv').write_text('modified')
        with self.assertRaisesRegex(ValueError,'hash mismatch'): load_completed_run(run)

    def test_profile_and_rules_validation(self):
        for profile in (Profile('wrong'),Profile('original',(0,)),Profile('original',(1,1)),Profile('spatial25',tuple(range(1,46)))):
            with self.assertRaises(ValueError): profile.validate()
        with self.assertRaises(ValueError): Rules(long_gap_us=0).validate()

    def test_discovery_skips_analysis_outputs_and_direct_derived_inputs(self):
        self.fixture()
        run = run_pipeline(self.rec, self.out)
        derived = run / 'processed' / 'original'
        self.assertEqual(discover_recordings(self.root), [self.rec])
        for path in (run, derived):
            self.assertEqual(discover_recordings(path), [])
            with self.assertRaisesRegex(ValueError, 'cannot be used as raw'):
                run_pipeline(path, self.root / 'another_analysis')
        self.assertFalse((self.root / 'another_analysis').exists())

    def test_incomplete_analysis_is_pruned_but_legacy_csv_still_found(self):
        self.fixture()
        run = self.out / 'interrupted'
        derived = run / 'processed' / 'median3'
        derived.mkdir(parents=True)
        (run / 'run.json').write_text(json.dumps(dict(schema_version=1, pipeline_version='1.0.0', status='cancelled')))
        (derived / 'pressure_left.csv').write_text('partial')
        legacy = self.root / 'legacy' / 'rec_20260101_120000_subject_1'
        legacy.mkdir(parents=True)
        (legacy / 'pressure_left.csv').write_text('legacy CSV without meta')
        self.assertEqual(discover_recordings(self.root), sorted([self.rec, legacy]))
        (run / 'profile.json').write_text('{}')
        (run / 'run.json').write_text('{interrupted')
        self.assertEqual(discover_recordings(self.root), sorted([self.rec, legacy]))

    def test_explicit_csv_schema_is_streamed(self):
        written = threading.Event()
        original = csv.DictWriter.writerow
        def observe(writer, row):
            result = original(writer, row)
            if row.get('frame_idx') == 0:
                written.set()
            return result
        def records():
            yield {'frame_idx': 0}
            self.assertTrue(written.is_set(), 'Writer buffered the whole generator')
            yield {'frame_idx': 1}
        with patch.object(csv.DictWriter, 'writerow', observe):
            write_csv(self.root / 'stream.csv', records(), ['frame_idx'])

    def test_csv_cancellation_is_atomic_and_removes_temporary_file(self):
        path = self.root / 'out.csv'
        path.write_text('previous result')
        cancel = threading.Event()
        def records():
            yield {'value': 1}
            cancel.set()
            yield {'value': 2}
        with self.assertRaises(Cancelled):
            write_csv(path, records(), ['value'], cancel=cancel)
        self.assertEqual(path.read_text(), 'previous result')
        self.assertFalse(list(self.root.glob('.writing_*')))

    def test_csv_cancel_before_commit_and_report_field_union(self):
        path = self.root / 'out.csv'
        cancel = threading.Event()
        def records():
            yield {'value': 1}
            cancel.set()
        with self.assertRaises(Cancelled):
            write_csv(path, records(), ['value'], cancel=cancel)
        self.assertFalse(path.exists())
        write_csv(path, iter([{'one': 1}, {'two': 2}]))
        with path.open(encoding='utf-8-sig', newline='') as handle:
            reader = csv.DictReader(handle)
            self.assertEqual(reader.fieldnames, ['one', 'two'])
            self.assertEqual(len(list(reader)), 2)


if __name__=='__main__': unittest.main()
