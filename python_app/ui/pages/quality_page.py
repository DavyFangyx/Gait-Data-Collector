from __future__ import annotations

import csv
import json
from pathlib import Path
import numpy as np
from PyQt5 import QtCore, QtGui, QtWidgets
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg
from matplotlib.figure import Figure

from ...pressure_pipeline.io import discover_recordings
from ...pressure_pipeline.jobs import PipelineJobs
from ...pressure_pipeline.schema import METHODS, Profile, Flag
from ...pressure_pipeline.service import load_completed_run


class PressureMap(FigureCanvasQTAgg):
    """Physical measurement points, without inventing interpolated measurements."""
    def __init__(self):
        self.figure=Figure(figsize=(2,3),tight_layout=True)
        super().__init__(self.figure)
        self._side=None
        self.ax=self.figure.add_subplot(111)
        self.points=None

    def set_values(self,side,values,maximum,title):
        if side!=self._side:
            layout=Path(__file__).resolve().parents[3]/'foot_sensor_layout'
            with (layout/(side+'foot_dots.csv')).open(encoding='utf-8-sig',newline='') as f:
                rows=sorted(csv.DictReader(f),key=lambda r:int(r['dot_index']))
            xy=np.asarray([[float(r['norm_x']),float(r['norm_y'])] for r in rows])
            self.ax.clear()
            self.points=self.ax.scatter(1-xy[:,0],1-xy[:,1],c=np.zeros(48),s=60,cmap='viridis',vmin=0,vmax=maximum)
            self.ax.set_aspect('equal'); self.ax.set_xlim(0,1); self.ax.set_ylim(0,1); self.ax.axis('off')
            self._side=side
        self.points.set_array(values)
        self.points.set_clim(0,maximum)
        self.ax.set_title(title)
        self.draw_idle()

    def clear_values(self):
        self.ax.clear(); self.ax.axis('off'); self._side=None; self.points=None
        self.draw_idle()


class QualityPage(QtWidgets.QWidget):
    settings_changed=QtCore.pyqtSignal()

    def __init__(self,settings,parent=None):
        super().__init__(parent)
        self.settings=settings
        self.jobs=PipelineJobs(self)
        self._job_rows={}; self._job_runs={}; self._arrays={}; self._current_run=None; self._batch_outputs={}
        root=QtWidgets.QVBoxLayout(self)
        options=QtWidgets.QHBoxLayout()
        self.auto_check=QtWidgets.QCheckBox('保存后自动检查（后台）')
        self.auto_check.setChecked(settings.pressure_quality_auto)
        self.auto_check.toggled.connect(self._persist)
        options.addWidget(self.auto_check)
        self.method=QtWidgets.QComboBox(); self.method.addItems(METHODS)
        self.method.setCurrentText(settings.pressure_processing_method)
        self.method.currentTextChanged.connect(self._persist)
        options.addWidget(QtWidgets.QLabel('手动处理方案')); options.addWidget(self.method)
        self.left_cells=QtWidgets.QLineEdit(); self.right_cells=QtWidgets.QLineEdit()
        for widget,key in ((self.left_cells,'pressure_suspect_left'),(self.right_cells,'pressure_suspect_right')):
            widget.setPlaceholderText('疑似点1..48，逗号分隔（可空）')
            widget.setText(','.join(map(str,getattr(settings,key))))
            widget.editingFinished.connect(self._persist)
        options.addWidget(QtWidgets.QLabel('左')); options.addWidget(self.left_cells)
        options.addWidget(QtWidgets.QLabel('右')); options.addWidget(self.right_cells)
        root.addLayout(options)
        paths=QtWidgets.QHBoxLayout()
        self.output=QtWidgets.QLineEdit(settings.pressure_quality_output_dir)
        self.output.setPlaceholderText('独立输出目录；留空使用采集根目录旁的 *_pressure_analysis')
        self.output.editingFinished.connect(self._persist)
        paths.addWidget(QtWidgets.QLabel('分析输出')); paths.addWidget(self.output)
        pick=QtWidgets.QPushButton('选择输出目录'); pick.clicked.connect(self._choose_output); paths.addWidget(pick)
        root.addLayout(paths)
        actions=QtWidgets.QHBoxLayout()
        for label,callback in [('检查/处理录制目录',self._choose_recording),('批量检查（original）',self._choose_batch),
                               ('打开历史结果',self._open_run),('取消选中任务',self._cancel),('打开结果文件夹',self._open_folder)]:
            button=QtWidgets.QPushButton(label); button.clicked.connect(callback); actions.addWidget(button)
        root.addLayout(actions)
        self.note=QtWidgets.QLabel('自动任务仅检查并保留原值。处理方案只用于手动任务；原始CSV不改。接收时钟不等于物理采样/曝光时钟。')
        self.note.setWordWrap(True); root.addWidget(self.note)
        split=QtWidgets.QSplitter(QtCore.Qt.Vertical)
        self.tasks=QtWidgets.QTableWidget(0,3)
        self.tasks.setHorizontalHeaderLabels(['录制/任务','状态','进度/结果目录'])
        self.tasks.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.tasks.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.tasks.horizontalHeader().setStretchLastSection(True)
        self.tasks.cellClicked.connect(self._task_selected)
        split.addWidget(self.tasks)
        self.details=QtWidgets.QPlainTextEdit(); self.details.setReadOnly(True)
        split.addWidget(self.details)
        self.issues=QtWidgets.QTableWidget(0,4)
        self.issues.setHorizontalHeaderLabels(['侧','来源行','接收时间(us)','风险（点击定位）'])
        self.issues.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.issues.horizontalHeader().setStretchLastSection(True)
        self.issues.cellClicked.connect(self._issue_selected)
        split.addWidget(self.issues); root.addWidget(split,1)
        controls=QtWidgets.QHBoxLayout()
        self.side=QtWidgets.QComboBox(); self.side.addItems(['left','right']); self.side.currentTextChanged.connect(self._render)
        self.cell=QtWidgets.QSpinBox(); self.cell.setRange(1,48); self.cell.valueChanged.connect(self._render)
        self.slider=QtWidgets.QSlider(QtCore.Qt.Horizontal); self.slider.setRange(0,0); self.slider.valueChanged.connect(self._show_frame)
        controls.addWidget(QtWidgets.QLabel('回放侧')); controls.addWidget(self.side)
        controls.addWidget(QtWidgets.QLabel('测点')); controls.addWidget(self.cell)
        controls.addWidget(self.slider,1); root.addLayout(controls)
        preview=QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        self.figure=Figure(figsize=(8,3),tight_layout=True)
        self.canvas=FigureCanvasQTAgg(self.figure); preview.addWidget(self.canvas)
        self.raw_heat=PressureMap(); self.processed_heat=PressureMap()
        preview.addWidget(self.raw_heat); preview.addWidget(self.processed_heat)
        root.addWidget(preview,2)
        self.frame_info=QtWidgets.QLabel('选择完成的任务查看：左热图=来源值，右热图=处理值，共用色标。')
        self.frame_info.setWordWrap(True); root.addWidget(self.frame_info)
        self.jobs.changed.connect(self._changed)
        self.jobs.completed.connect(self._completed)
        self.jobs.loaded.connect(self._loaded)
        self.jobs.discovered.connect(self._discovered)
        self.canvas.mpl_connect('button_press_event',self._plot_clicked)

    @staticmethod
    def _parse_cells(text):
        ids=tuple(int(x.strip()) for x in text.split(',') if x.strip())
        Profile(suspect_left=ids).validate()
        return ids

    def _persist(self,*_):
        try:
            left=self._parse_cells(self.left_cells.text()); right=self._parse_cells(self.right_cells.text())
        except (ValueError,AttributeError): return
        self.settings.pressure_quality_auto=self.auto_check.isChecked()
        self.settings.pressure_quality_output_dir=self.output.text().strip()
        self.settings.pressure_processing_method=self.method.currentText()
        self.settings.pressure_suspect_left=list(left); self.settings.pressure_suspect_right=list(right)
        self.settings_changed.emit()

    def _output_root(self):
        if self.output.text().strip(): return Path(self.output.text().strip()).expanduser()
        save=Path(self.settings.save_dir).expanduser().resolve()
        return save.parent/(save.name+'_pressure_analysis')

    def _choose_output(self):
        path=QtWidgets.QFileDialog.getExistingDirectory(self,'选择原始数据目录以外的输出目录')
        if path: self.output.setText(path); self._persist()

    def _submit(self,recording,method='original',output_root=None):
        recording=Path(recording).resolve(); out=(Path(output_root) if output_root else self._output_root()).resolve()
        save=Path(self.settings.save_dir).expanduser().resolve()
        if out==save or save in out.parents:
            raise ValueError('分析输出必须在原始采集根目录之外')
        left=self._parse_cells(self.left_cells.text()); right=self._parse_cells(self.right_cells.text())
        profile=Profile(method,left,right); profile.validate()
        job=self.jobs.submit(recording,out,profile=profile,
            layout_dir=Path(__file__).resolve().parents[3]/'foot_sensor_layout')
        row=self.tasks.rowCount(); self.tasks.insertRow(row); self._job_rows[job]=row
        for col,text in enumerate((recording.name,'queued','')):
            self.tasks.setItem(row,col,QtWidgets.QTableWidgetItem(text))
        self.tasks.item(row,0).setData(QtCore.Qt.UserRole,job)
        return job

    def recording_saved(self,output_dir):
        if self.auto_check.isChecked() and output_dir:
            try: self._submit(output_dir,'original')
            except Exception as exc: self.note.setText(f'采集已保存，自动检查未启动：{exc}')

    def _choose_recording(self):
        path=QtWidgets.QFileDialog.getExistingDirectory(self,'选择已完成保存的录制目录',self.settings.save_dir)
        if not path: return
        try: self._submit(path,self.method.currentText())
        except Exception as exc: QtWidgets.QMessageBox.warning(self,'任务未启动',str(exc))

    def _choose_batch(self):
        path=QtWidgets.QFileDialog.getExistingDirectory(self,'选择历史录制根目录',self.settings.save_dir)
        if not path: return
        try:
            root=Path(path).resolve(); out=self._output_root().resolve()
            if out==root or root in out.parents: raise ValueError('批量输出不能位于输入树内')
            job=self.jobs.discover(root); self._batch_outputs[job]=out
            row=self.tasks.rowCount(); self.tasks.insertRow(row); self._job_rows[job]=row
            for col,text in enumerate(('批量发现 '+root.name,'queued',str(root))):
                self.tasks.setItem(row,col,QtWidgets.QTableWidgetItem(text))
            self.tasks.item(row,0).setData(QtCore.Qt.UserRole,job)
        except Exception as exc: QtWidgets.QMessageBox.warning(self,'批量任务提示',str(exc))

    def _discovered(self,job,paths):
        out=self._batch_outputs.pop(job,None)
        if out is None: return
        for path in paths:
            try: self._submit(path,'original',output_root=out)
            except Exception as exc: self.note.setText(f'批量任务未全部提交：{exc}'); break

    def _changed(self,job,status,detail):
        row=self._job_rows.get(job)
        if row is not None:
            self.tasks.item(row,1).setText(status); self.tasks.item(row,2).setText(detail)

    def _completed(self,job,run):
        self._job_runs[job]=run

    def _loaded(self,job,run,receipt,arrays):
        row=self._job_rows.get(job)
        if row is not None: self.tasks.item(row,1).setText('完成 / '+receipt['data_status'])
        self._display(run,receipt,arrays)

    def _task_selected(self,row,col):
        item=self.tasks.item(row,0)
        job=item.data(QtCore.Qt.UserRole) if item else None
        if job in self._job_runs:
            try:
                load_job=self.jobs.load_run(self._job_runs[job])
                self._job_rows[load_job]=row
                self.tasks.item(row,0).setData(QtCore.Qt.UserRole,load_job)
            except Exception as exc: self.note.setText(f'结果读取失败：{exc}')

    def _display(self,run,receipt,arrays):
        self._current_run=Path(run); self._arrays=arrays
        profile=json.loads((Path(run)/'profile.json').read_text(encoding='utf-8'))
        self._gap_us=profile['rules']['long_gap_us']
        lines=[f"结果：{run}",f"当前结果方案：{profile['profile']['method']}（顶部方案用于新任务）",
               f"数据状态：{receipt['data_status']}；物理同步未认证；模型窗口未评价。"]
        for name in ('recording_quality.csv','review_queue.csv','time_segments.csv','channel_quality.csv'):
            with (Path(run)/name).open(encoding='utf-8-sig',newline='') as f:
                rows=list(csv.DictReader(f))
            if name=='channel_quality.csv': rows=[r for r in rows if r.get('d7_suspect')=='True']
            lines.append(name+'\n'+'\n'.join(str(r) for r in rows[:200]))
        self.details.setPlainText('\n\n'.join(lines))
        with (Path(run)/'time_events.csv').open(encoding='utf-8-sig',newline='') as f:
            events=list(csv.DictReader(f))
        self.issues.setRowCount(0)
        for event in events[:1000]:
            if 'side' not in event: continue
            row=self.issues.rowCount(); self.issues.insertRow(row)
            for col,key in enumerate(('side','row_index','time_us','reasons')):
                self.issues.setItem(row,col,QtWidgets.QTableWidgetItem(event[key]))
        self.note.setText(f"当前结果：{profile['profile']['method']}。时间风险显示前1000条（完整共{len(events)}条见time_events.csv）。点击风险行或曲线定位；原始CSV不改。")
        self._render()

    def _issue_selected(self,row,col):
        side=self.issues.item(row,0).text()
        self.side.setCurrentText(side)
        self.slider.setValue(int(self.issues.item(row,1).text()))

    def _render(self,*_):
        pack=self._arrays.get(self.side.currentText())
        self.figure.clear()
        if pack is not None:
            times=pack['time_us']/1e6; cell=self.cell.value()-1
            for index,label,data in ((1,'Foot total',slice(None)),(2,f'Cell {cell+1}',cell)):
                ax=self.figure.add_subplot(2,1,index)
                for key,color in [('source','#547999'),('processed','#ba8054')]:
                    y=pack[key].sum(axis=1) if index==1 else pack[key][:,data]
                    visible=pack['usable'].copy()
                    visible[1:] &= (np.diff(pack['time_us'])>0)&(np.diff(pack['time_us'])<=self._gap_us)
                    ax.plot(times,np.where(visible,y,np.nan),color=color,label=key,linewidth=.9)
                ax.set_ylabel(label); ax.legend(loc='upper right')
            self.figure.axes[-1].set_xlabel('Host receive time (s)')
            self.slider.setRange(0,max(0,len(times)-1))
            self._heat_max=max(float(np.max(np.where(np.isfinite(pack['source']),pack['source'],0))),
                               float(np.max(np.where(np.isfinite(pack['processed']),pack['processed'],0))),1)
        self.canvas.draw_idle(); self._show_frame()

    def _show_frame(self,*_):
        pack=self._arrays.get(self.side.currentText())
        if pack is None:
            self.raw_heat.clear_values(); self.processed_heat.clear_values()
            self.frame_info.setText('该侧无可回放的读取结果。'); return
        i=min(self.slider.value(),len(pack['source'])-1)
        maximum=self._heat_max
        for heat,key in ((self.raw_heat,'source'),(self.processed_heat,'processed')):
            heat.set_values(self.side.currentText(),np.nan_to_num(pack[key][i],nan=0,posinf=0,neginf=0),maximum,
                            'Source' if key=='source' else 'Processed')
        selected=int(pack['flags'][i,self.cell.value()-1])
        names=[flag.name for flag in Flag if selected & int(flag)]
        self.frame_info.setText(f"来源帧 {pack['frame_idx'][i]} | 接收时间 {pack['time_us'][i]/1e6:.6f}s | "
            f"usable={bool(pack['usable'][i])} | 测点标记 {','.join(names)} | 左=来源，右=处理；无效非有限值仅在热图置0显示")

    def _plot_clicked(self,event):
        pack=self._arrays.get(self.side.currentText())
        if pack is not None and event.xdata is not None:
            distances=np.abs(pack['time_us']/1e6-event.xdata)
            distances[~np.isfinite(distances)]=np.inf
            if np.isfinite(distances).any(): self.slider.setValue(int(np.argmin(distances)))

    def _cancel(self):
        row=self.tasks.currentRow()
        if row>=0: self.jobs.cancel(self.tasks.item(row,0).data(QtCore.Qt.UserRole))

    def _open_run(self):
        path=QtWidgets.QFileDialog.getExistingDirectory(self,'选择含run.json的结果目录',str(self._output_root()))
        if not path: return
        try:
            job=self.jobs.load_run(path)
            row=self.tasks.rowCount(); self.tasks.insertRow(row); self._job_rows[job]=row
            for col,text in enumerate(('历史结果 '+Path(path).name,'queued',path)):
                self.tasks.setItem(row,col,QtWidgets.QTableWidgetItem(text))
            self.tasks.item(row,0).setData(QtCore.Qt.UserRole,job)
        except Exception as exc: QtWidgets.QMessageBox.warning(self,'结果不可用',str(exc))

    def _open_folder(self):
        if self._current_run: QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(self._current_run)))

    def set_capture_busy(self,busy):
        self.jobs.set_capture_busy(busy)

    def shutdown(self):
        return self.jobs.shutdown()
