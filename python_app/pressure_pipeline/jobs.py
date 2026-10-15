"""Single background queue. Source saves finish before callers submit jobs."""
from __future__ import annotations

import queue
import threading
import uuid
from PyQt5 import QtCore

from .schema import Cancelled
from .service import run_pipeline


class _Control:
    def __init__(self, paused):
        self.cancelled=threading.Event()
        self.paused=paused

    def is_set(self):
        # Quality checkpoints also pause active analysis during capture/saving.
        while self.paused.is_set() and not self.cancelled.wait(.1):
            pass
        return self.cancelled.is_set()


class PipelineJobs(QtCore.QObject):
    changed=QtCore.pyqtSignal(str,str,str)  # job ID, state, detail
    completed=QtCore.pyqtSignal(str,str)  # job ID, output directory
    loaded=QtCore.pyqtSignal(str,str,object,object)
    discovered=QtCore.pyqtSignal(str,object)

    def __init__(self,parent=None):
        super().__init__(parent)
        self._queue=queue.Queue()
        self._paused=threading.Event()
        self._stopping=threading.Event()
        self._controls={}
        self._lock=threading.Lock()
        self._thread=threading.Thread(target=self._work,name='pressure-analysis',daemon=True)
        self._thread.start()

    def submit(self,recording,output_root,**kwargs):
        with self._lock:
            if self._stopping.is_set(): raise RuntimeError('Analysis queue is closing')
            if self._queue.full(): raise RuntimeError('Analysis queue is full')
            job=uuid.uuid4().hex
            control=_Control(self._paused)
            self._controls[job]=control
            self._queue.put_nowait((job,control,recording,output_root,kwargs))
        self.changed.emit(job,'queued',str(recording))
        return job

    def set_capture_busy(self,busy):
        if busy: self._paused.set()
        else: self._paused.clear()

    def cancel(self,job):
        with self._lock:
            control=self._controls.get(job)
            if control is not None: control.cancelled.set()

    def load_run(self,run):
        return self.submit(run,None,_load_run=run)

    def discover(self,root):
        return self.submit(root,None,_discover_root=root)

    def _work(self):
        while not self._stopping.is_set():
            try: item=self._queue.get(timeout=.1)
            except queue.Empty: continue
            job,control,recording,out,kwargs=item
            try:
                if control.is_set(): raise Cancelled('Task cancelled')
                self.changed.emit(job,'running',str(recording))
                discovery_root=kwargs.pop('_discover_root',None)
                if discovery_root is not None:
                    from .io import discover_recordings
                    paths=discover_recordings(discovery_root,cancel=control)
                    if not paths: raise ValueError('No recording directories found')
                    self.discovered.emit(job,paths)
                    self.changed.emit(job,'completed',f'发现 {len(paths)} 个录制目录')
                    continue
                load_path=kwargs.pop('_load_run',None)
                if load_path is None:
                    run=run_pipeline(recording,out,cancel=control,
                        progress=lambda msg:self.changed.emit(job,'running',msg),**kwargs)
                else:
                    run=load_path
                from .service import load_completed_run
                receipt,arrays=load_completed_run(run,cancel=control)
                self.changed.emit(job,'completed',str(run))
                self.completed.emit(job,str(run))
                self.loaded.emit(job,str(run),receipt,arrays)
            except Cancelled as exc:
                self.changed.emit(job,'cancelled',str(exc))
            except Exception as exc:
                self.changed.emit(job,'failed',str(exc))
            finally:
                with self._lock: self._controls.pop(job,None)
                self._queue.task_done()

    def shutdown(self,timeout=3.0):
        self._stopping.set()
        with self._lock:
            for control in self._controls.values(): control.cancelled.set()
        self._paused.clear()
        self._thread.join(timeout)
        return not self._thread.is_alive()
