"""Offscreen UI and worker tests: no cameras, ports or hardware capture."""
import json
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from PyQt5 import QtWidgets
from python_app.config import AppSettings,SettingsStore
from python_app.pressure_pipeline.jobs import PipelineJobs
from python_app.ui.pages.quality_page import QualityPage


class QtPipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name); self.raw=self.root/'raw'; self.rec=self.raw/'rec1'; self.rec.mkdir(parents=True)
        (self.rec/'meta.json').write_text(json.dumps({'pressure':[{'sensor_id':'left_foot','enabled':True}]}))
        header='frame_idx,t_us,'+','.join(map(str,range(1,49)))+'\n'
        text=''.join(f'{i},{i*25000},'+','.join(['100']*48)+'\n' for i in range(20))
        (self.rec/'pressure_left.csv').write_text(header+text)

    def wait_for(self,condition,seconds=5):
        end=time.monotonic()+seconds
        while not condition() and time.monotonic()<end:
            self.app.processEvents(); time.sleep(.005)
        self.app.processEvents()
        self.assertTrue(condition(),'Timed out waiting for Qt worker')

    def test_queue_pauses_and_cancel_does_not_start_raw_read(self):
        jobs=PipelineJobs(); self.addCleanup(jobs.shutdown)
        states=[]; jobs.changed.connect(lambda job,state,detail:states.append(state))
        jobs.set_capture_busy(True)
        job=jobs.submit(self.rec,self.root/'analysis')
        end=time.monotonic()+.12
        while time.monotonic()<end: self.app.processEvents(); time.sleep(.005)
        self.assertFalse((self.root/'analysis').exists())
        jobs.cancel(job); self.wait_for(lambda:'cancelled' in states)
        self.assertFalse((self.root/'analysis').exists())

    def test_page_auto_qc_is_original_and_loads_comparison(self):
        settings=AppSettings(save_dir=str(self.raw),pressure_processing_method='lowpass6')
        page=QualityPage(settings); self.addCleanup(page.close); self.addCleanup(page.shutdown)
        page.recording_saved(str(self.rec))
        self.wait_for(lambda:bool(page._arrays))
        self.assertEqual(page.tasks.rowCount(),1)
        run=page._current_run
        state=json.loads((run/'run.json').read_text())
        self.assertEqual(state['profile']['method'],'original')
        self.assertIn('no_flags_detected',page.tasks.item(0,1).text())
        self.assertEqual(len(page._arrays['left']['source']),20)
        page.side.setCurrentText('left'); page.slider.setValue(3)
        self.assertIn('0.075000',page.frame_info.text())
        page.side.setCurrentText('right')
        self.assertIn('无可回放',page.frame_info.text())

    def test_disabled_auto_does_not_submit(self):
        page=QualityPage(AppSettings(save_dir=str(self.raw),pressure_quality_auto=False))
        self.addCleanup(page.close); self.addCleanup(page.shutdown)
        page.recording_saved(str(self.rec)); self.assertEqual(page.tasks.rowCount(),0)

    def test_settings_roundtrip(self):
        settings=AppSettings(save_dir=str(self.raw),pressure_quality_auto=False,
            pressure_quality_output_dir=str(self.root/'analysis'),pressure_processing_method='median3',
            pressure_suspect_right=[35,38])
        loaded=AppSettings.from_dict(settings.to_dict())
        self.assertFalse(loaded.pressure_quality_auto)
        self.assertEqual(loaded.pressure_processing_method,'median3')
        self.assertEqual(loaded.pressure_suspect_right,[35,38])
        self.assertEqual(loaded.pressure_quality_output_dir,settings.pressure_quality_output_dir)

    def test_batch_discovery_and_historical_load_use_worker(self):
        jobs=PipelineJobs(); self.addCleanup(jobs.shutdown)
        found=[]; jobs.discovered.connect(lambda job,paths:found.extend(paths))
        jobs.discover(self.raw); self.wait_for(lambda:bool(found))
        self.assertEqual(found,[self.rec])
        loaded=[]; jobs.loaded.connect(lambda job,path,receipt,arrays:loaded.append((path,receipt)))
        jobs.submit(self.rec,self.root/'analysis')
        self.wait_for(lambda:len(loaded)==1)
        jobs.load_run(loaded[0][0]); self.wait_for(lambda:len(loaded)==2)
        self.assertEqual(loaded[1][1]['data_status'],'no_flags_detected')

    def test_main_window_hooks_for_both_recording_modes(self):
        from python_app.ui.main_window import MainWindow
        settings=AppSettings(save_dir=str(self.raw))
        with patch.object(MainWindow,'_apply_settings_to_runtime'):
            window=MainWindow(SettingsStore(self.root/'settings.json'),settings)
        self.addCleanup(window.close)
        with patch.object(window.quality_page,'recording_saved') as saved, patch.object(QtWidgets.QMessageBox,'information'):
            window._handle_save_completed(str(self.rec))
            saved.assert_called_once_with(str(self.rec))
            saved.reset_mock()
            window.pressure_only_recording.is_recording=True
            def stop():
                window.pressure_only_recording.is_recording=False
                return 20,str(self.rec)
            with patch.object(window.pressure_only_recording,'stop_recording',side_effect=stop):
                window._stop_pressure_only_recording('test')
            saved.assert_called_once_with(str(self.rec))
        self.assertEqual(window.tabs.tabText(window.tabs.count()-1),'数据检查与处理')


if __name__=='__main__': unittest.main()
