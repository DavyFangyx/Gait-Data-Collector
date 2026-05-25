from __future__ import annotations

from typing import Callable, Dict

from PyQt5 import QtCore, QtWidgets

from ...constants import CAMERA_SLOTS, PRESSURE_SENSORS
from ..widgets import MetricBar, TechPanel


class RecordPage(QtWidgets.QWidget):
    def __init__(
        self,
        pressure_summary_rows: Dict[str, Dict[str, QtWidgets.QLabel]],
        on_previous_subject: Callable[[], None],
        on_next_subject: Callable[[], None],
        on_toggle_recording: Callable[[], None],
        on_snapshot: Callable[[], None],
        on_settings: Callable[[], None],
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.pressure_summary_rows = pressure_summary_rows
        self.on_previous_subject = on_previous_subject
        self.on_next_subject = on_next_subject
        self.on_toggle_recording = on_toggle_recording
        self.on_snapshot = on_snapshot
        self.on_settings = on_settings
        self._build_ui()

    def _build_ui(self) -> None:
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        top = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        top.setChildrenCollapsible(False)
        top.setHandleWidth(8)
        top.addWidget(self._build_record_panel())
        top.addWidget(self._build_status_panel())
        top.setStretchFactor(0, 2)
        top.setStretchFactor(1, 2)

        bottom = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        bottom.setChildrenCollapsible(False)
        bottom.setHandleWidth(8)
        bottom.addWidget(self._build_memory_panel())
        bottom.addWidget(self._build_pressure_panel())
        bottom.setStretchFactor(0, 1)
        bottom.setStretchFactor(1, 1)

        root.addWidget(top, 2)
        root.addWidget(bottom, 1)

    def _build_record_panel(self) -> QtWidgets.QWidget:
        panel = TechPanel()
        layout = QtWidgets.QVBoxLayout(panel)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(12)

        title = QtWidgets.QLabel("数据记录")
        title.setProperty("panelTitle", True)
        layout.addWidget(title)

        mode_row = QtWidgets.QHBoxLayout()
        mode_row.addWidget(QtWidgets.QLabel("记录模式"))
        self.record_mode_combo = QtWidgets.QComboBox()
        self.record_mode_combo.addItem("全部记录", "all")
        self.record_mode_combo.addItem("仅触觉", "pressure_only")
        mode_row.addWidget(self.record_mode_combo)
        mode_row.addStretch(1)
        layout.addLayout(mode_row)

        subject_row = QtWidgets.QHBoxLayout()
        subject_row.setSpacing(10)
        subject_row.addWidget(QtWidgets.QLabel("对象"))
        self.prev_subject_button = QtWidgets.QPushButton("上一位")
        self.prev_subject_button.clicked.connect(self.on_previous_subject)
        subject_row.addWidget(self.prev_subject_button)
        self.subject_edit = QtWidgets.QLineEdit()
        self.subject_edit.setPlaceholderText("输入对象名称")
        subject_row.addWidget(self.subject_edit, 1)
        self.next_subject_button = QtWidgets.QPushButton("下一位")
        self.next_subject_button.clicked.connect(self.on_next_subject)
        subject_row.addWidget(self.next_subject_button)
        layout.addLayout(subject_row)

        count_row = QtWidgets.QHBoxLayout()
        count_row.addWidget(QtWidgets.QLabel("次数"))
        self.counter_spin = QtWidgets.QSpinBox()
        self.counter_spin.setRange(1, 99999)
        self.counter_spin.setValue(1)
        self.counter_spin.setPrefix("第 ")
        self.counter_spin.setSuffix(" 次")
        count_row.addWidget(self.counter_spin)
        count_row.addStretch(1)
        layout.addLayout(count_row)

        actions = QtWidgets.QHBoxLayout()
        self.state_badge = QtWidgets.QLabel("待机中")
        self.state_badge.setAlignment(QtCore.Qt.AlignCenter)
        self.state_badge.setProperty("stateBadge", True)
        self.state_badge.setProperty("level", "normal")
        self.record_button = QtWidgets.QPushButton("开始录制")
        self.record_button.setProperty("accent", True)
        self.record_button.clicked.connect(self.on_toggle_recording)
        self.snapshot_button = QtWidgets.QPushButton("立即拍照")
        self.snapshot_button.clicked.connect(self.on_snapshot)
        self.settings_button = QtWidgets.QPushButton("系统设置")
        self.settings_button.clicked.connect(self.on_settings)
        actions.addWidget(self.state_badge)
        actions.addWidget(self.record_button)
        actions.addWidget(self.snapshot_button)
        actions.addWidget(self.settings_button)
        actions.addStretch(1)
        layout.addLayout(actions)
        layout.addStretch(1)
        return panel

    def _build_status_panel(self) -> QtWidgets.QWidget:
        self.status_panel = TechPanel()
        status_layout = QtWidgets.QVBoxLayout(self.status_panel)
        status_layout.setContentsMargins(14, 14, 14, 14)
        status_layout.setSpacing(6)
        title = QtWidgets.QLabel("运行状态")
        title.setProperty("panelTitle", True)
        self.connected_label = QtWidgets.QLabel()
        self.elapsed_label = QtWidgets.QLabel()
        self.output_label = QtWidgets.QLabel()
        self.output_label.setWordWrap(True)
        status_layout.addWidget(title)
        status_layout.addWidget(self.connected_label)
        status_layout.addWidget(self.elapsed_label)
        status_layout.addWidget(self.output_label)
        self.binding_labels = {}
        for slot in CAMERA_SLOTS:
            label = QtWidgets.QLabel()
            label.setWordWrap(True)
            self.binding_labels[slot.key] = label
            status_layout.addWidget(label)
        status_layout.addStretch(1)
        return self.status_panel

    def _build_memory_panel(self) -> QtWidgets.QWidget:
        self.memory_panel = TechPanel()
        memory_layout = QtWidgets.QVBoxLayout(self.memory_panel)
        memory_layout.setContentsMargins(14, 14, 14, 14)
        memory_layout.setSpacing(8)
        memory_head = QtWidgets.QHBoxLayout()
        memory_titles = QtWidgets.QVBoxLayout()
        memory_titles.setContentsMargins(0, 0, 0, 0)
        memory_title = QtWidgets.QLabel("内存监控")
        memory_title.setProperty("panelTitle", True)
        memory_titles.addWidget(memory_title)
        self.memory_level_badge = QtWidgets.QLabel("正常")
        self.memory_level_badge.setProperty("memoryBadge", True)
        self.memory_level_badge.setProperty("level", "normal")
        memory_head.addLayout(memory_titles, 1)
        memory_head.addWidget(self.memory_level_badge, 0, QtCore.Qt.AlignTop)
        self.memory_note_label = QtWidgets.QLabel("缓存占用基于最近一帧的分辨率估算。")
        self.memory_note_label.setProperty("muted", True)
        self.memory_note_label.setWordWrap(True)
        self.system_memory_metric = MetricBar("系统内存")
        self.process_memory_metric = MetricBar("当前进程")
        self.buffer_memory_metric = MetricBar("录制缓存")
        self.camera_buffer_summary = QtWidgets.QLabel("1: 0 帧 | 2: 0 帧 | 3: 0 帧 | 4: 0 帧")
        self.camera_buffer_summary.setProperty("muted", True)
        memory_layout.addLayout(memory_head)
        memory_layout.addWidget(self.memory_note_label)
        memory_layout.addWidget(self.system_memory_metric)
        memory_layout.addWidget(self.process_memory_metric)
        memory_layout.addWidget(self.buffer_memory_metric)
        memory_layout.addWidget(self.camera_buffer_summary)
        memory_layout.addStretch(1)
        return self.memory_panel

    def _build_pressure_panel(self) -> QtWidgets.QWidget:
        panel = TechPanel()
        pressure_layout = QtWidgets.QVBoxLayout(panel)
        pressure_layout.setContentsMargins(14, 14, 14, 14)
        pressure_layout.setSpacing(8)
        pressure_title = QtWidgets.QLabel("脚压传感器")
        pressure_title.setProperty("panelTitle", True)
        pressure_layout.addWidget(pressure_title)
        for sensor in PRESSURE_SENSORS:
            row = QtWidgets.QWidget()
            row_layout = QtWidgets.QVBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(2)
            state_label = QtWidgets.QLabel(f"{sensor.label}：未连接")
            meta_label = QtWidgets.QLabel("未配置端口")
            meta_label.setProperty("muted", True)
            row_layout.addWidget(state_label)
            row_layout.addWidget(meta_label)
            pressure_layout.addWidget(row)
            self.pressure_summary_rows[sensor.key] = {
                "state": state_label,
                "meta": meta_label,
            }
        self.pressure_log_view = QtWidgets.QPlainTextEdit()
        self.pressure_log_view.setReadOnly(True)
        self.pressure_log_view.setMaximumBlockCount(200)
        self.pressure_log_view.setProperty("console", True)
        pressure_layout.addWidget(self.pressure_log_view, 1)
        return panel
