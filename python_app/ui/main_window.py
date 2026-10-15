from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path
from typing import Dict, List

import numpy as np
from PyQt5 import QtCore, QtGui, QtWidgets

from ..branding import APP_ICON_PATH, MARK_DARK_PATH
from ..camera_service import CameraController
from ..config import AppSettings, SettingsStore
from ..constants import (
    APP_TITLE,
    CAMERA_SLOTS,
    PRESSURE_SENSORS,
    SLOT_LABELS,
    STATE_IDLE,
    STATE_RECORDING,
    STATE_SAVING,
    STATUS_REFRESH_MS,
)
from ..pressure_sensor import PressureFrame, PressureSensorManager, PressureSnapshot
from ..pressure_recording import PressureOnlyRecorder
from ..recording import MemorySnapshot, RecordingCoordinator
from .pages.acquisition_page import AcquisitionPage
from .pages.packet_monitor_page import PacketMonitorPage
from .pages.pressure_page import PressureLivePage
from .pages.record_page import RecordPage
from .pages.replay_page import ReplayPage
from .pages.quality_page import QualityPage
from .settings_dialog import SettingsDialog
from .widgets import FootPressureCanvas, MetricBar, PreviewTile, TechPanel, repolish


class PressureReplayDialog(QtWidgets.QDialog):
    def __init__(
        self,
        path: Path,
        frames: List[PressureSnapshot],
        settings: AppSettings,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.frames = frames
        self.current_index = 0
        self.setWindowTitle(f"脚压回放 - {path.name}")
        self.resize(520, 760)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        self.canvas = FootPressureCanvas(frames[0].sensor_id)
        self.canvas.pressure_max = settings.pressure_display_max
        self.canvas.set_scale(settings.pressure_scale_x, settings.pressure_scale_y)
        self.canvas.set_colormap(settings.pressure_colormap)
        self.canvas.set_display_mode(settings.pressure_display_mode)
        layout.addWidget(self.canvas, 1)

        self.info_label = QtWidgets.QLabel()
        self.info_label.setAlignment(QtCore.Qt.AlignCenter)
        layout.addWidget(self.info_label)

        self.slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.slider.setRange(0, len(frames) - 1)
        self.slider.valueChanged.connect(self._show_frame)
        layout.addWidget(self.slider)

        controls = QtWidgets.QHBoxLayout()
        controls.setSpacing(8)
        self.prev_button = QtWidgets.QPushButton("上一帧")
        self.prev_button.clicked.connect(self._show_previous)
        self.next_button = QtWidgets.QPushButton("下一帧")
        self.next_button.clicked.connect(self._show_next)
        self.play_button = QtWidgets.QPushButton("播放")
        self.play_button.clicked.connect(self._toggle_playback)
        controls.addWidget(self.prev_button)
        controls.addWidget(self.play_button)
        controls.addWidget(self.next_button)
        layout.addLayout(controls)

        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(50)
        self.timer.timeout.connect(self._advance_playback)
        self._show_frame(0)

    def _show_frame(self, index: int) -> None:
        self.current_index = max(0, min(index, len(self.frames) - 1))
        if self.slider.value() != self.current_index:
            self.slider.blockSignals(True)
            self.slider.setValue(self.current_index)
            self.slider.blockSignals(False)
        snapshot = self.frames[self.current_index]
        self.canvas.set_snapshot(snapshot)
        self.info_label.setText(
            f"{snapshot.label} | 帧 {snapshot.last_frame_index} | {self.current_index + 1} / {len(self.frames)}"
        )

    def _show_previous(self) -> None:
        self._show_frame(self.current_index - 1)

    def _show_next(self) -> None:
        self._show_frame(self.current_index + 1)

    def _toggle_playback(self) -> None:
        if self.timer.isActive():
            self.timer.stop()
            self.play_button.setText("播放")
            return
        self.timer.start()
        self.play_button.setText("暂停")

    def _advance_playback(self) -> None:
        next_index = self.current_index + 1
        if next_index >= len(self.frames):
            self.timer.stop()
            self.play_button.setText("播放")
            return
        self._show_frame(next_index)


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, settings_store: SettingsStore, settings: AppSettings) -> None:
        super().__init__()
        self.settings_store = settings_store
        self.settings = settings
        self.camera_controller = CameraController()
        self.pressure_manager = PressureSensorManager()
        self.recording = RecordingCoordinator(settings, self.camera_controller)
        self.pressure_only_recording = PressureOnlyRecorder(settings)
        self.camera_controller.set_frame_consumer(self.recording.append_live_frame)
        self.pressure_manager.set_frame_consumer(self._consume_pressure_frame)

        self.preview_tiles: Dict[str, PreviewTile] = {}
        self.pressure_canvases: Dict[str, list[FootPressureCanvas]] = {}
        self.pressure_summary_rows: Dict[str, list[Dict[str, QtWidgets.QLabel]]] = {}
        self.pressure_stats_rows: Dict[str, list[Dict[str, QtWidgets.QLabel]]] = {}
        self.pressure_log_views: list[QtWidgets.QPlainTextEdit] = []
        self.binding_label_groups: list[Dict[str, QtWidgets.QLabel]] = []
        self._camera_warning_shown = False
        self._pressure_visualization_enabled = True
        self._active_record_mode = "all"
        self.state_texts = {
            STATE_IDLE: "待机中",
            STATE_RECORDING: "录制中",
            STATE_SAVING: "保存中",
        }
        self._build_ui()
        self._connect_signals()
        self._apply_settings_to_runtime()

        self.refresh_timer = QtCore.QTimer(self)
        self.refresh_timer.timeout.connect(self._refresh_runtime_info)
        self.refresh_timer.start(STATUS_REFRESH_MS)

    def _build_ui(self) -> None:
        self.setWindowTitle(APP_TITLE)
        if APP_ICON_PATH.exists():
            self.setWindowIcon(QtGui.QIcon(str(APP_ICON_PATH)))
        self.setMinimumSize(1440, 920)
        central = QtWidgets.QWidget()
        root = QtWidgets.QVBoxLayout(central)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(14)
        root.addWidget(self._build_title_header())
        root.addWidget(self._build_pages(), 1)
        self.setCentralWidget(central)

    def _build_title_header(self) -> QtWidgets.QWidget:
        panel = TechPanel()
        layout = QtWidgets.QHBoxLayout(panel)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(12)

        mark_label = QtWidgets.QLabel()
        mark_label.setFixedSize(42, 42)
        mark = self._load_branding_pixmap(MARK_DARK_PATH, 42)
        if mark is not None:
            mark_label.setPixmap(mark)
        else:
            mark_label.setText("4")
            mark_label.setAlignment(QtCore.Qt.AlignCenter)
            mark_label.setProperty("previewBadge", True)
        layout.addWidget(mark_label, 0, QtCore.Qt.AlignVCenter)

        title_label = QtWidgets.QLabel(APP_TITLE)
        title_label.setProperty("role", "title")
        layout.addWidget(title_label, 0, QtCore.Qt.AlignVCenter)
        layout.addStretch(1)
        self.header_settings_button = QtWidgets.QPushButton("系统设置")
        self.header_settings_button.setMinimumHeight(38)
        self.header_settings_button.clicked.connect(self._open_settings)
        layout.addWidget(self.header_settings_button, 0, QtCore.Qt.AlignVCenter)
        return panel

    def _build_pages(self) -> QtWidgets.QTabWidget:
        self.tabs = QtWidgets.QTabWidget()
        self.acquisition_page = AcquisitionPage(self.preview_tiles, self.pressure_canvases)
        self.record_page = RecordPage(
            {},
            self._select_previous_subject,
            self._select_next_subject,
            self._toggle_recording,
            self._take_snapshot,
            self._open_settings,
        )
        self.pressure_live_page = PressureLivePage(
            self.settings,
            self.pressure_canvases,
            self._on_pressure_display_mode_changed,
            self._on_pressure_max_changed,
            self._on_pressure_scale_changed,
            self._on_pressure_colormap_changed,
        )
        self.packet_monitor_page = PacketMonitorPage(self.settings)
        self.packet_monitor_page.settings_saved.connect(self._save_packet_monitor_settings)
        self.replay_tabs = QtWidgets.QTabWidget()
        self.pressure_replay_page = ReplayPage(self.settings, self._load_pressure_replay_frames)
        self.camera_replay_placeholder = self._build_camera_replay_placeholder()
        self.replay_tabs.addTab(self.pressure_replay_page, "触觉数据回放")
        self.replay_tabs.addTab(self.camera_replay_placeholder, "相机数据回放")

        self._bind_page_widgets()
        self.tabs.addTab(self.acquisition_page, "摄像机加上触觉（实时显示）")
        self.tabs.addTab(self.record_page, "数据记录")
        self.tabs.addTab(self.pressure_live_page, "触觉实时显示")
        self.tabs.addTab(self.packet_monitor_page, "压力包监控")
        self.tabs.addTab(self.replay_tabs, "数据回放")
        self.quality_page = QualityPage(self.settings)
        self.quality_page.settings_changed.connect(lambda: self.settings_store.save(self.settings))
        self.tabs.addTab(self.quality_page, "数据检查与处理")
        return self.tabs

    def _build_camera_replay_placeholder(self) -> QtWidgets.QWidget:
        panel = TechPanel()
        layout = QtWidgets.QVBoxLayout(panel)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(8)
        title = QtWidgets.QLabel("相机数据回放")
        title.setProperty("panelTitle", True)
        note = QtWidgets.QLabel("相机录像与逐帧图片仍按当前保存结构生成，可从会话目录直接查看。")
        note.setProperty("muted", True)
        note.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(note)
        layout.addStretch(1)
        return panel

    def _bind_page_widgets(self) -> None:
        self.state_badge = self.record_page.state_badge
        self.record_button = self.record_page.record_button
        self.snapshot_button = self.record_page.snapshot_button
        self.settings_button = self.record_page.settings_button
        self.prev_subject_button = self.record_page.prev_subject_button
        self.next_subject_button = self.record_page.next_subject_button
        self.subject_edit = self.record_page.subject_edit
        self.counter_spin = self.record_page.counter_spin
        self.record_mode_combo = self.record_page.record_mode_combo
        self.connected_label = self.record_page.connected_label
        self.elapsed_label = self.record_page.elapsed_label
        self.output_label = self.record_page.output_label
        self.binding_labels = self.record_page.binding_labels
        self.memory_level_badge = self.record_page.memory_level_badge
        self.memory_note_label = self.record_page.memory_note_label
        self.system_memory_metric = self.record_page.system_memory_metric
        self.process_memory_metric = self.record_page.process_memory_metric
        self.buffer_memory_metric = self.record_page.buffer_memory_metric
        self.camera_buffer_summary = self.record_page.camera_buffer_summary
        self.pressure_log_views = [
            self.acquisition_page.pressure_log_view,
            self.record_page.pressure_log_view,
        ]
        self.pressure_log_view = self.record_page.pressure_log_view
        self.pressure_summary_rows = {}
        for page_rows in (
            self.acquisition_page.pressure_summary_rows,
            self.record_page.pressure_summary_rows,
        ):
            for sensor_id, row in page_rows.items():
                self.pressure_summary_rows.setdefault(sensor_id, []).append(row)
        self.pressure_stats_rows = {}
        for sensor_id, row in self.acquisition_page.pressure_stats_rows.items():
            self.pressure_stats_rows.setdefault(sensor_id, []).append(row)
        self.binding_label_groups = [
            self.acquisition_page.binding_labels,
            self.record_page.binding_labels,
        ]
        self.acquisition_connected_label = self.acquisition_page.connected_label
        self.acquisition_elapsed_label = self.acquisition_page.elapsed_label
        self.acquisition_output_label = self.acquisition_page.output_label
        self.pressure_display_combo = self.pressure_live_page.pressure_display_combo
        self.pressure_max_spin = self.pressure_live_page.pressure_max_spin
        self.pressure_scale_x_spin = self.pressure_live_page.pressure_scale_x_spin
        self.pressure_scale_y_spin = self.pressure_live_page.pressure_scale_y_spin
        self.pressure_cmap_combo = self.pressure_live_page.pressure_cmap_combo

    def _save_packet_monitor_settings(self, settings: AppSettings) -> None:
        self.settings = settings
        self.settings_store.save(self.settings)
        self.recording.update_settings(self.settings)
        self.pressure_only_recording.update_settings(self.settings)
        self.pressure_manager.update_settings(self.settings.pressure_sensors)
        self._append_pressure_log("压力包监控页已更新左右脚串口设置。")

    def _build_header(self) -> QtWidgets.QWidget:
        panel = TechPanel()
        layout = QtWidgets.QHBoxLayout(panel)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(12)

        mark_label = QtWidgets.QLabel()
        mark_label.setFixedSize(42, 42)
        mark = self._load_branding_pixmap(MARK_DARK_PATH, 42)
        if mark is not None:
            mark_label.setPixmap(mark)
        else:
            mark_label.setText("4")
            mark_label.setAlignment(QtCore.Qt.AlignCenter)
            mark_label.setProperty("previewBadge", True)
        layout.addWidget(mark_label, 0, QtCore.Qt.AlignVCenter)

        title_label = QtWidgets.QLabel(APP_TITLE)
        title_label.setProperty("role", "title")
        layout.addWidget(title_label, 0, QtCore.Qt.AlignVCenter)

        layout.addStretch(1)

        subject_widget = QtWidgets.QWidget()
        subject_layout = QtWidgets.QHBoxLayout(subject_widget)
        subject_layout.setContentsMargins(0, 0, 0, 0)
        subject_layout.setSpacing(10)
        subject_layout.addWidget(QtWidgets.QLabel("对象"), 0, QtCore.Qt.AlignVCenter)
        self.prev_subject_button = QtWidgets.QPushButton("上一位")
        self.prev_subject_button.clicked.connect(self._select_previous_subject)
        subject_layout.addWidget(self.prev_subject_button)
        self.subject_edit = QtWidgets.QLineEdit()
        self.subject_edit.setPlaceholderText("输入对象名称")
        self.subject_edit.setMinimumWidth(220)
        self.subject_edit.setMaximumWidth(280)
        subject_layout.addWidget(self.subject_edit)
        self.next_subject_button = QtWidgets.QPushButton("下一位")
        self.next_subject_button.clicked.connect(self._select_next_subject)
        subject_layout.addWidget(self.next_subject_button)
        subject_layout.addWidget(QtWidgets.QLabel("次数"), 0, QtCore.Qt.AlignVCenter)
        self.counter_spin = QtWidgets.QSpinBox()
        self.counter_spin.setRange(1, 99999)
        self.counter_spin.setValue(1)
        self.counter_spin.setPrefix("第 ")
        self.counter_spin.setSuffix(" 次")
        subject_layout.addWidget(self.counter_spin)
        layout.addWidget(subject_widget, 0, QtCore.Qt.AlignCenter)

        layout.addStretch(1)

        self.state_badge = QtWidgets.QLabel("待机中")
        self.state_badge.setAlignment(QtCore.Qt.AlignCenter)
        self.state_badge.setProperty("stateBadge", True)
        self.state_badge.setProperty("level", "normal")
        self.record_button = QtWidgets.QPushButton("开始录制")
        self.record_button.setProperty("accent", True)
        self.record_button.setMinimumHeight(38)
        self.record_button.clicked.connect(self._toggle_recording)
        self.snapshot_button = QtWidgets.QPushButton("立即拍照")
        self.snapshot_button.setMinimumHeight(38)
        self.snapshot_button.clicked.connect(self._take_snapshot)
        self.settings_button = QtWidgets.QPushButton("系统设置")
        self.settings_button.setMinimumHeight(38)
        self.settings_button.clicked.connect(self._open_settings)
        self.pressure_replay_button = QtWidgets.QPushButton("脚压回放")
        self.pressure_replay_button.setMinimumHeight(38)
        self.pressure_replay_button.clicked.connect(self._open_pressure_replay)
        layout.addWidget(self.state_badge, 0, QtCore.Qt.AlignVCenter)
        layout.addWidget(self.record_button)
        layout.addWidget(self.snapshot_button)
        layout.addWidget(self.pressure_replay_button)
        layout.addWidget(self.settings_button)
        return panel

    def _build_body_splitter(self) -> QtWidgets.QSplitter:
        body_splitter = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        body_splitter.setChildrenCollapsible(False)
        body_splitter.setHandleWidth(8)

        main_splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        main_splitter.setChildrenCollapsible(False)
        main_splitter.setHandleWidth(8)
        camera_workspace = self._build_camera_splitter()
        pressure_workspace = self._build_pressure_workspace()
        pressure_workspace.setMinimumWidth(360)
        main_splitter.addWidget(camera_workspace)
        main_splitter.addWidget(pressure_workspace)
        main_splitter.setStretchFactor(0, 4)
        main_splitter.setStretchFactor(1, 1)
        main_splitter.setSizes([980, 460])

        bottom_splitter = self._build_bottom_panel()
        bottom_splitter.setMinimumHeight(220)

        body_splitter.addWidget(main_splitter)
        body_splitter.addWidget(bottom_splitter)
        body_splitter.setStretchFactor(0, 4)
        body_splitter.setStretchFactor(1, 1)
        body_splitter.setSizes([670, 250])
        return body_splitter

    def _build_camera_splitter(self) -> QtWidgets.QSplitter:
        left_column = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        right_column = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        for splitter in (left_column, right_column):
            splitter.setChildrenCollapsible(False)
            splitter.setHandleWidth(8)

        tile_order = {
            "top": left_column,
            "left": left_column,
            "front": right_column,
            "right": right_column,
        }
        for slot in CAMERA_SLOTS:
            tile = PreviewTile(slot.label)
            self.preview_tiles[slot.key] = tile
            tile_order[slot.key].addWidget(tile)

        left_column.setStretchFactor(0, 1)
        left_column.setStretchFactor(1, 1)
        right_column.setStretchFactor(0, 1)
        right_column.setStretchFactor(1, 1)

        splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(8)
        splitter.addWidget(left_column)
        splitter.addWidget(right_column)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        return splitter

    def _build_pressure_workspace(self) -> QtWidgets.QWidget:
        panel = TechPanel()
        layout = QtWidgets.QVBoxLayout(panel)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        heading_row = QtWidgets.QHBoxLayout()
        heading_row.setContentsMargins(0, 0, 0, 0)
        heading_row.setSpacing(8)
        title = QtWidgets.QLabel("双脚压力可视化")
        title.setProperty("panelTitle", True)
        heading_row.addWidget(title)
        heading_row.addStretch(1)
        layout.addLayout(heading_row)

        # 两个脚的 RBF 热力图画布
        canvas_row = QtWidgets.QHBoxLayout()
        canvas_row.setSpacing(8)
        for sensor in PRESSURE_SENSORS:
            lbl = QtWidgets.QLabel(sensor.label)
            lbl.setAlignment(QtCore.Qt.AlignCenter)
            lbl.setProperty("muted", True)
            col = QtWidgets.QVBoxLayout()
            col.setSpacing(4)
            col.addWidget(lbl)
            canvas = FootPressureCanvas(sensor.key)
            self.pressure_canvases[sensor.key] = canvas
            col.addWidget(canvas, 1)
            canvas_row.addLayout(col, 1)
        layout.addLayout(canvas_row, 1)

        # 控制行：display / MAX / 缩放 / 配色
        ctrl_row = QtWidgets.QHBoxLayout()
        ctrl_row.setSpacing(6)
        ctrl_row.addWidget(QtWidgets.QLabel("display:"))
        self.pressure_display_combo = QtWidgets.QComboBox()
        self.pressure_display_combo.addItem("热力图", "heatmap")
        self.pressure_display_combo.addItem("轮廓点阵", "outline")
        display_index = self.pressure_display_combo.findData(
            self.settings.pressure_display_mode
        )
        self.pressure_display_combo.setCurrentIndex(max(display_index, 0))
        self.pressure_display_combo.currentIndexChanged.connect(
            self._on_pressure_display_mode_changed
        )
        ctrl_row.addWidget(self.pressure_display_combo)

        ctrl_row.addSpacing(6)
        ctrl_row.addWidget(QtWidgets.QLabel("上限 MAX:"))
        self.pressure_max_spin = QtWidgets.QDoubleSpinBox()
        self.pressure_max_spin.setRange(1.0, 1e9)
        self.pressure_max_spin.setDecimals(1)
        self.pressure_max_spin.setSingleStep(100.0)
        self.pressure_max_spin.setMaximumWidth(110)
        self.pressure_max_spin.setValue(self.settings.pressure_display_max)
        self.pressure_max_spin.setToolTip("热力图按 0–MAX 渲染")
        self.pressure_max_spin.valueChanged.connect(self._on_pressure_max_changed)
        ctrl_row.addWidget(self.pressure_max_spin)

        ctrl_row.addSpacing(6)
        ctrl_row.addWidget(QtWidgets.QLabel("X 倍:"))
        self.pressure_scale_x_spin = QtWidgets.QDoubleSpinBox()
        self.pressure_scale_x_spin.setRange(0.1, 10.0)
        self.pressure_scale_x_spin.setDecimals(2)
        self.pressure_scale_x_spin.setSingleStep(0.05)
        self.pressure_scale_x_spin.setMaximumWidth(70)
        self.pressure_scale_x_spin.setValue(self.settings.pressure_scale_x)
        self.pressure_scale_x_spin.valueChanged.connect(self._on_pressure_scale_changed)
        ctrl_row.addWidget(self.pressure_scale_x_spin)

        ctrl_row.addWidget(QtWidgets.QLabel("Y 倍:"))
        self.pressure_scale_y_spin = QtWidgets.QDoubleSpinBox()
        self.pressure_scale_y_spin.setRange(0.1, 10.0)
        self.pressure_scale_y_spin.setDecimals(2)
        self.pressure_scale_y_spin.setSingleStep(0.05)
        self.pressure_scale_y_spin.setMaximumWidth(70)
        self.pressure_scale_y_spin.setValue(self.settings.pressure_scale_y)
        self.pressure_scale_y_spin.valueChanged.connect(self._on_pressure_scale_changed)
        ctrl_row.addWidget(self.pressure_scale_y_spin)

        ctrl_row.addSpacing(6)
        self.pressure_cmap_combo = QtWidgets.QComboBox()
        self.pressure_cmap_combo.addItems(["viridis", "RdYlBu", "YlOrRd"])
        idx = self.pressure_cmap_combo.findText(self.settings.pressure_colormap)
        self.pressure_cmap_combo.setCurrentIndex(max(idx, 0))
        self.pressure_cmap_combo.currentTextChanged.connect(self._on_pressure_colormap_changed)
        ctrl_row.addWidget(self.pressure_cmap_combo)

        ctrl_row.addStretch(1)
        layout.addLayout(ctrl_row)
        return panel

    def _build_bottom_panel(self) -> QtWidgets.QSplitter:
        splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(8)

        status_panel = self._build_status_panel()
        memory_panel = self._build_memory_panel()
        pressure_panel = self._build_pressure_panel()

        status_panel.setMinimumWidth(420)
        memory_panel.setMinimumWidth(300)
        pressure_panel.setMinimumWidth(360)

        splitter.addWidget(status_panel)
        splitter.addWidget(memory_panel)
        splitter.addWidget(pressure_panel)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 1)
        splitter.setSizes([540, 360, 420])
        return splitter

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
        memory_head.setContentsMargins(0, 0, 0, 0)
        memory_head.setSpacing(8)
        memory_titles = QtWidgets.QVBoxLayout()
        memory_titles.setContentsMargins(0, 0, 0, 0)
        memory_titles.setSpacing(2)
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
        self.pressure_panel = TechPanel()
        pressure_layout = QtWidgets.QVBoxLayout(self.pressure_panel)
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
        self.pressure_log_view.setMaximumHeight(120)
        self.pressure_log_view.setProperty("console", True)
        pressure_layout.addWidget(self.pressure_log_view)
        pressure_layout.addStretch(1)
        return self.pressure_panel

    def _connect_signals(self) -> None:
        self.camera_controller.preview_updated.connect(self._update_preview)
        self.camera_controller.devices_changed.connect(self._handle_devices_changed)
        self.recording.state_changed.connect(self._handle_state_changed)
        self.recording.save_completed.connect(self._handle_save_completed)
        self.recording.save_failed.connect(self._handle_save_failed)
        self.pressure_manager.connection_changed.connect(self._handle_pressure_connection)
        self.pressure_manager.log_message.connect(self._append_pressure_log)
        self.pressure_manager.frame_received.connect(self._handle_pressure_frame)

    def _apply_settings_to_runtime(self) -> None:
        self.subject_edit.setText(self.settings.subjects[0] if self.settings.subjects else "测试")
        self.camera_controller.refresh_devices()
        migrated = self.camera_controller.set_binding_hints(self.settings.camera_bindings)
        self.camera_controller.start(self.settings.camera_bindings)
        self.pressure_manager.update_settings(self.settings.pressure_sensors)
        self.recording.update_settings(self.settings)
        self.pressure_only_recording.update_settings(self.settings)
        self._apply_pressure_settings_to_canvases()
        for line in migrated:
            self._append_pressure_log(line)
        self._append_legacy_binding_warnings()
        self._show_camera_backend_warning()
        self._update_binding_labels()
        self._refresh_runtime_info()

    def _update_binding_labels(self) -> None:
        descriptor_map = {item.serial_number: item for item in self.camera_controller.descriptors}
        for binding in self.settings.camera_bindings:
            descriptor = descriptor_map.get(binding.device_serial)
            if descriptor is not None:
                device_text = descriptor.summary
            elif binding.device_serial:
                device_text = f"序列号 {binding.device_serial}"
            elif binding.device_name:
                device_text = f"旧配置待迁移：{binding.device_name}"
            else:
                device_text = "未绑定"
            text = (
                f"{SLOT_LABELS[binding.slot]} 号机位：{device_text}"
                f" | 输出 {binding.output_id}"
                f" | 录像 {'开' if binding.record_enabled else '关'}"
                f" | 拍照 {'开' if binding.snapshot_enabled else '关'}"
            )
            for labels in self.binding_label_groups:
                label = labels.get(binding.slot)
                if label is not None:
                    label.setText(text)

    def _toggle_recording(self) -> None:
        if self.recording.state == STATE_RECORDING:
            self._stop_recording("手动停止")
            return
        if self.pressure_only_recording.is_recording:
            self._stop_pressure_only_recording("手动停止")
            return
        if self.recording.state != STATE_IDLE:
            return
        mode = self.record_mode_combo.currentData() or "all"
        if mode == "pressure_only":
            self._start_pressure_only_recording("手动开始")
        else:
            self._start_recording("手动开始")

    def _start_recording(self, source: str) -> None:
        ok, message = self.recording.begin_recording(self.subject_edit.text().strip(), self.counter_spin.value())
        if not ok:
            QtWidgets.QMessageBox.warning(self, "无法开始录制", message)
            return
        self._active_record_mode = "all"
        self._append_pressure_log(f"{source}，会话目录：{message}")

    def _start_pressure_only_recording(self, source: str) -> None:
        ok, message = self.pressure_only_recording.begin_recording(
            self.subject_edit.text().strip(),
            self.counter_spin.value(),
        )
        if not ok:
            QtWidgets.QMessageBox.warning(self, "无法开始仅触觉记录", message)
            return
        self._active_record_mode = "pressure_only"
        self._append_pressure_log(f"{source}仅触觉记录，会话目录：{message}")
        self._sync_control_states()

    def _stop_recording(self, source: str) -> None:
        if self.recording.state != STATE_RECORDING:
            return
        self._append_pressure_log(source)
        self.recording.stop_recording()

    def _stop_pressure_only_recording(self, source: str) -> None:
        if not self.pressure_only_recording.is_recording:
            return
        count, output_dir = self.pressure_only_recording.stop_recording()
        self.counter_spin.setValue(self.counter_spin.value() + 1)
        self._append_pressure_log(f"{source}，仅触觉记录完成：{count} 帧，{output_dir}")
        self.quality_page.recording_saved(output_dir)
        self._active_record_mode = "all"
        self._sync_control_states()

    def _take_snapshot(self) -> None:
        try:
            files = self.recording.take_snapshots()
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "拍照失败", str(exc))
            return
        if files:
            self._append_pressure_log(f"已保存 {len(files)} 张图片。")
        else:
            QtWidgets.QMessageBox.information(self, "拍照提示", "当前没有可用画面，未生成图片。")

    def _consume_pressure_frame(self, frame: PressureFrame) -> None:
        self.recording.append_live_pressure_frame(frame)
        self.pressure_only_recording.append_frame(frame)

    def _open_settings(self) -> None:
        dialog = SettingsDialog(
            self.settings,
            self.camera_controller.descriptors,
            camera_profiles=self.camera_controller.camera_profiles(),
            camera_profile_provider=self.camera_controller.probe_camera_profile,
            parent=self,
        )
        if dialog.exec_() != QtWidgets.QDialog.Accepted:
            return
        self.settings = dialog.settings
        self.quality_page.settings = self.settings
        self.settings_store.save(self.settings)
        self.recording.update_settings(self.settings)
        self.pressure_only_recording.update_settings(self.settings)
        self.camera_controller.refresh_devices()
        migrated = self.camera_controller.set_binding_hints(self.settings.camera_bindings)
        self.camera_controller.start(self.settings.camera_bindings)
        self.pressure_manager.update_settings(self.settings.pressure_sensors)
        self._apply_pressure_settings_to_canvases()
        self.pressure_replay_page.update_settings(self.settings)
        self.packet_monitor_page.update_settings(self.settings)
        for line in migrated:
            self._append_pressure_log(line)
        self._append_legacy_binding_warnings()
        self._show_camera_backend_warning()
        self._update_binding_labels()
        self._append_pressure_log("设置已更新。")

    def _select_previous_subject(self) -> None:
        subjects = self.settings.subjects or [self.subject_edit.text().strip() or "测试"]
        current = self.subject_edit.text().strip()
        if current not in subjects:
            self.subject_edit.setText(subjects[0])
            return
        index = max(0, subjects.index(current) - 1)
        self.subject_edit.setText(subjects[index])
        self.counter_spin.setValue(1)

    def _select_next_subject(self) -> None:
        subjects = self.settings.subjects or [self.subject_edit.text().strip() or "测试"]
        current = self.subject_edit.text().strip()
        if current not in subjects:
            self.subject_edit.setText(subjects[0])
            return
        index = min(len(subjects) - 1, subjects.index(current) + 1)
        self.subject_edit.setText(subjects[index])
        self.counter_spin.setValue(1)

    def _pressure_display_range(self) -> tuple[float, float]:
        # 新架构：display_values 范围基于 12-bit 数据（0-4095）
        return 0.0, 4095.0

    def _refresh_runtime_info(self) -> None:
        statuses = self.camera_controller.statuses()
        binding_map = {item.slot: item for item in self.settings.camera_bindings}
        descriptor_map = {item.serial_number: item for item in self.camera_controller.descriptors}
        connected = 0
        for slot in CAMERA_SLOTS:
            status = statuses.get(slot.key)
            binding = binding_map.get(slot.key)
            tile = self.preview_tiles[slot.key]
            descriptor = descriptor_map.get(binding.device_serial) if binding else None
            if descriptor is not None:
                bound_name = descriptor.display_name
                bound_serial = descriptor.serial_tail
                bound_ip = descriptor.ip_address or "-"
                bound_model = descriptor.model_name or "未知型号"
            elif binding and binding.device_serial:
                bound_name = f"序列号 {binding.device_serial}"
                bound_serial = binding.device_serial[-6:]
                bound_ip = "-"
                bound_model = "未知型号"
            elif binding and binding.device_name:
                bound_name = f"旧配置：{binding.device_name}"
                bound_serial = "-"
                bound_ip = "-"
                bound_model = "待迁移"
            else:
                bound_name = "未绑定设备"
                bound_serial = "-"
                bound_ip = "-"
                bound_model = "未绑定"

            tile.set_identity(bound_name if not status or not status.connected else (status.device_name or bound_name))
            if status and status.connected:
                connected += 1
                tile.set_meta(f"{status.resolution} | {status.fps:.1f} FPS")
                tile.set_status("在线", "normal")
            else:
                reason = status.last_error if status else "未连接"
                if not binding or (not binding.device_serial and not binding.device_name):
                    tile.set_meta("")
                elif reason in {"未绑定设备", "未连接"}:
                    tile.set_meta("")
                else:
                    tile.set_meta(reason or "未连接")
                if reason == "未绑定设备":
                    tile.set_status("未绑定", "warning")
                elif reason == "未找到对应序列号设备":
                    tile.set_status("设备缺失", "danger")
                elif reason == "设备被占用":
                    tile.set_status("占用中", "danger")
                elif reason and ("MVS" in reason or "DLL" in reason or "64 位" in reason or "Windows" in reason):
                    tile.set_status("SDK异常", "danger")
                else:
                    tile.set_status("离线", "warning")

        connected_text = f"相机在线：{connected} / 4"
        self.connected_label.setText(connected_text)
        self.acquisition_connected_label.setText(connected_text)
        if self.pressure_only_recording.is_recording:
            elapsed_text = "录制时长：仅触觉记录中"
        elif self.recording.state == STATE_RECORDING:
            elapsed = self.recording.elapsed_seconds()
            elapsed_text = f"录制时长：{elapsed:.1f} / {self.settings.record_limit_sec:.1f} 秒"
            if elapsed >= self.settings.record_limit_sec:
                self._stop_recording("达到录制时长上限")
        elif self.recording.state == STATE_SAVING:
            elapsed_text = "录制时长：正在保存本次会话"
        else:
            elapsed_text = "录制时长：0.0 秒"
        self.elapsed_label.setText(elapsed_text)
        self.acquisition_elapsed_label.setText(elapsed_text)
        output_text = f"输出根目录：{self.settings.save_dir}"
        self.output_label.setText(output_text)
        self.acquisition_output_label.setText(output_text)
        self._update_pressure_snapshots()
        self._update_pressure_parse_stats()
        self._update_memory_panel(self.recording.memory_snapshot())
        self._sync_control_states()

    def _sync_control_states(self) -> None:
        state = self.recording.state
        pressure_only = self.pressure_only_recording.is_recording
        self.quality_page.set_capture_busy(pressure_only or state in {STATE_RECORDING, STATE_SAVING})
        self.state_badge.setText("仅触觉记录中" if pressure_only else self.state_texts.get(state, "待机中"))
        if pressure_only:
            self.state_badge.setText("仅触觉记录中")
        elif state == STATE_RECORDING:
            self.state_badge.setText(f"录制中（{self.recording.elapsed_seconds():.1f} 秒）")
        elif state == STATE_SAVING:
            self.state_badge.setText("保存中")
        level = "normal"
        if pressure_only or state == STATE_RECORDING:
            level = "danger"
        elif state == STATE_SAVING:
            level = "warning"
        self.state_badge.setProperty("level", level)
        repolish(self.state_badge)

        is_idle = state == STATE_IDLE and not pressure_only
        is_recording = state == STATE_RECORDING or pressure_only
        self.record_button.setText("停止录制" if is_recording else "开始录制")
        self.record_button.setProperty("accent", is_idle)
        self.record_button.setProperty("danger", is_recording)
        repolish(self.record_button)
        self.snapshot_button.setEnabled(is_idle)
        self.settings_button.setEnabled(is_idle)
        self.header_settings_button.setEnabled(is_idle)
        self.prev_subject_button.setEnabled(is_idle)
        self.next_subject_button.setEnabled(is_idle)
        self.subject_edit.setReadOnly(not is_idle)
        self.counter_spin.setReadOnly(not is_idle)
        self.record_mode_combo.setEnabled(is_idle)
        self._set_pressure_visualization_enabled(not is_recording)

    def _set_pressure_visualization_enabled(self, enabled: bool) -> None:
        if self._pressure_visualization_enabled == enabled:
            return
        self._pressure_visualization_enabled = enabled
        self.pressure_manager.set_visualization_enabled(enabled)
        for canvases in self.pressure_canvases.values():
            for canvas in canvases:
                canvas.set_render_enabled(enabled)

    def _update_preview(self, slot: str, image: QtGui.QImage) -> None:
        tile = self.preview_tiles.get(slot)
        if tile is not None:
            tile.set_image(image)

    def _handle_devices_changed(self, devices) -> None:
        if not devices:
            if self.camera_controller.backend_warning():
                self._append_pressure_log(self.camera_controller.backend_warning())
            else:
                self._append_pressure_log("未探测到可用工业相机。")
            return
        summary = ", ".join(item.summary for item in devices)
        self._append_pressure_log(f"已探测设备：{summary}")

    def _handle_state_changed(self, state: str) -> None:
        self._sync_control_states()
        if state == STATE_SAVING:
            self._append_pressure_log("开始保存录像与逐帧图片及脚压数据。")

    def _handle_save_completed(self, output_dir: str) -> None:
        self.counter_spin.setValue(self.counter_spin.value() + 1)
        self._active_record_mode = "all"
        self._append_pressure_log(f"保存完成：{output_dir}")
        self.quality_page.recording_saved(output_dir)
        QtWidgets.QMessageBox.information(self, "保存完成", f"本次采集已保存到：\n{output_dir}")

    def _handle_save_failed(self, message: str) -> None:
        self._active_record_mode = "all"
        self._append_pressure_log(f"保存失败：{message}")
        QtWidgets.QMessageBox.warning(self, "保存失败", message)

    def _handle_pressure_connection(self, sensor_id: str, connected: bool, message: str) -> None:
        snapshot = self.pressure_manager.snapshots().get(sensor_id)
        if snapshot is not None:
            self._render_pressure_snapshot(snapshot)

    def _handle_pressure_frame(self, sensor_id: str, frame: PressureFrame) -> None:
        _ = frame
        if self.recording.state == STATE_RECORDING:
            return
        snapshot = self.pressure_manager.snapshots().get(sensor_id)
        if snapshot is not None:
            self._render_pressure_snapshot(snapshot)

    def _update_pressure_snapshots(self) -> None:
        snapshots = self.pressure_manager.snapshots()
        for sensor in PRESSURE_SENSORS:
            snapshot = snapshots.get(sensor.key)
            if snapshot is not None:
                self._render_pressure_snapshot(snapshot)

    def _render_pressure_snapshot(self, snapshot: PressureSnapshot) -> None:
        if self._pressure_visualization_enabled:
            for canvas in self.pressure_canvases.get(snapshot.sensor_id, []):
                canvas.set_snapshot(snapshot)
        for row in self.pressure_summary_rows.get(snapshot.sensor_id, []):
            row["state"].setText(f"{snapshot.label}：{'在线' if snapshot.connected else '离线'}")
            if snapshot.connected and snapshot.last_received_at is not None:
                row["meta"].setText(
                    f"{snapshot.port or '未配置端口'} | 最近帧 {snapshot.last_frame_index} | {snapshot.last_received_at.strftime('%H:%M:%S')}"
                )
            else:
                row["meta"].setText(self._compact_pressure_meta(snapshot))

    def _update_pressure_parse_stats(self) -> None:
        stats = self.pressure_manager.parse_stats()
        for sensor_id, rows in self.pressure_stats_rows.items():
            stat = stats.get(sensor_id)
            if stat is None:
                continue
            frame_text = (
                f"收到 {stat.received_frames} | "
                f"成功 {stat.parsed_frames} | "
                f"失败 {stat.failed_frames}"
            )
            error_text = f"最近错误：{stat.last_error}" if stat.last_error else "最近错误：无"
            for row in rows:
                row["frames"].setText(frame_text)
                row["error"].setText(error_text)

    def _compact_pressure_meta(self, snapshot: PressureSnapshot) -> str:
        port = (snapshot.port or "").strip()
        message = (snapshot.message or "").strip()
        if "未启用" in message:
            return "未启用"
        if not port:
            return "未配置端口"
        if "连接中" in message:
            return f"{port} | 连接中"
        if "连接失败" in message or "could not open port" in message:
            return f"{port} | 打开失败"
        if "读取失败" in message:
            return f"{port} | 读取失败"
        if "已断开" in message:
            return f"{port} | 已断开"
        return f"{port} | 离线"

    def _append_pressure_log(self, line: str) -> None:
        if not line:
            return
        now = datetime.now().strftime("%H:%M:%S")
        text = f"[{now}] {line}"
        for view in self.pressure_log_views:
            view.appendPlainText(text)

    def _update_memory_panel(self, snapshot: MemorySnapshot) -> None:
        level_text = {
            "normal": "正常",
            "warning": "警告",
            "danger": "危险",
        }
        self.memory_level_badge.setText(level_text.get(snapshot.level, "正常"))
        self.memory_level_badge.setProperty("level", snapshot.level)
        repolish(self.memory_level_badge)
        self.memory_note_label.setText(snapshot.note)
        self.system_memory_metric.set_state(
            f"{self._format_bytes(snapshot.system_used_bytes)} / {self._format_bytes(snapshot.system_total_bytes)} ({snapshot.system_used_percent:.0f}%)",
            snapshot.system_used_percent,
            snapshot.level,
        )
        self.process_memory_metric.set_state(
            f"{self._format_bytes(snapshot.process_rss_bytes)} ({snapshot.process_percent:.1f}%)",
            snapshot.process_percent,
            "warning" if snapshot.process_percent >= 18.0 else "normal",
        )
        buffer_level = snapshot.level if snapshot.buffer_total_bytes else "normal"
        self.buffer_memory_metric.set_state(
            f"{self._format_bytes(snapshot.buffer_total_bytes)} | 脚压 {self._format_bytes(snapshot.pressure_buffer_bytes)}",
            snapshot.buffer_percent,
            buffer_level,
        )
        parts = [
            f"{item.label}: {item.frame_count} 帧 {self._format_bytes(item.estimated_bytes)}"
            for item in snapshot.by_camera
        ]
        self.camera_buffer_summary.setText(" | ".join(parts))

    def _on_pressure_max_changed(self, value: float) -> None:
        for canvases in self.pressure_canvases.values():
            for canvas in canvases:
                canvas.set_pressure_max(value)
        self.settings.pressure_display_max = value
        self.settings_store.save(self.settings)
        self.pressure_replay_page.update_settings(self.settings)

    def _on_pressure_scale_changed(self) -> None:
        sx = self.pressure_scale_x_spin.value()
        sy = self.pressure_scale_y_spin.value()
        for canvases in self.pressure_canvases.values():
            for canvas in canvases:
                canvas.set_scale(sx, sy)
        self.settings.pressure_scale_x = sx
        self.settings.pressure_scale_y = sy
        self.settings_store.save(self.settings)
        self.pressure_replay_page.update_settings(self.settings)

    def _on_pressure_colormap_changed(self, cmap: str) -> None:
        for canvases in self.pressure_canvases.values():
            for canvas in canvases:
                canvas.set_colormap(cmap)
        self.settings.pressure_colormap = cmap
        self.settings_store.save(self.settings)
        self.pressure_replay_page.update_settings(self.settings)

    def _on_pressure_display_mode_changed(self, index: int = 0) -> None:
        _ = index
        mode = self.pressure_display_combo.currentData() or "heatmap"
        for canvases in self.pressure_canvases.values():
            for canvas in canvases:
                canvas.set_display_mode(str(mode))
        self.settings.pressure_display_mode = str(mode)
        self.settings_store.save(self.settings)
        self.pressure_replay_page.update_settings(self.settings)

    def _open_pressure_replay(self) -> None:
        start_dir = Path(self.settings.save_dir)
        filename, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "选择脚压 CSV",
            str(start_dir if start_dir.exists() else Path.cwd()),
            "Pressure CSV (pressure_*.csv Pressure_*.csv PressureOnly_*.csv *.csv);;CSV 文件 (*.csv);;所有文件 (*.*)",
        )
        if not filename:
            return
        path = Path(filename)
        try:
            frames = self._load_pressure_replay_frames(path)
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "脚压回放失败", str(exc))
            return
        if not frames:
            QtWidgets.QMessageBox.information(self, "脚压回放", "文件中没有可回放的脚压帧。")
            return
        dialog = PressureReplayDialog(path, frames, self.settings, self)
        dialog.exec_()

    def _load_pressure_replay_frames(self, path: Path) -> list[PressureSnapshot]:
        name = path.name.lower()
        sensor_id = "left_foot" if "left" in name else "right_foot"
        sensor = next((item for item in PRESSURE_SENSORS if item.key == sensor_id), None)
        label = sensor.label if sensor is not None else sensor_id
        frames: list[PressureSnapshot] = []
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.reader(handle)
            header = next(reader, None)
            if not header:
                return frames
            point_start = 2
            if "1" in header:
                point_start = header.index("1")
            for row_number, row in enumerate(reader):
                if not row:
                    continue
                values: list[int] = []
                for item in row[point_start:point_start + 48]:
                    try:
                        values.append(int(float(item)))
                    except ValueError:
                        values.append(0)
                if len(values) < 48:
                    values.extend([0] * (48 - len(values)))
                frame_index = row_number
                try:
                    if "frame_idx" in header:
                        frame_index = int(row[header.index("frame_idx")])
                    else:
                        frame_index = int(row[0])
                except (ValueError, IndexError):
                    pass
                raw_values = np.array(values[:48], dtype=np.uint16)
                frames.append(
                    PressureSnapshot(
                        sensor_id=sensor_id,
                        label=label,
                        connected=True,
                        port=path.name,
                        last_frame_index=frame_index,
                        last_received_at=None,
                        message="回放帧",
                        raw_values=raw_values,
                        display_values=raw_values.astype(np.float32),
                    )
                )
        return frames

    def _apply_pressure_settings_to_canvases(self) -> None:
        """将 AppSettings 中的压力可视化参数同步到所有 Canvas 和 UI 控件"""
        pmax = self.settings.pressure_display_max
        sx = self.settings.pressure_scale_x
        sy = self.settings.pressure_scale_y
        cmap = self.settings.pressure_colormap
        mode = self.settings.pressure_display_mode
        for canvases in self.pressure_canvases.values():
            for canvas in canvases:
                canvas.pressure_max = pmax
                canvas._cmap = cmap
                canvas.set_scale(sx, sy)
                canvas.set_display_mode(mode)
        # 同步 UI 控件，防止重入
        for attr, val in (
            ("pressure_max_spin", pmax),
            ("pressure_scale_x_spin", sx),
            ("pressure_scale_y_spin", sy),
        ):
            w = getattr(self, attr, None)
            if w is not None:
                w.blockSignals(True)
                w.setValue(val)
                w.blockSignals(False)
        combo = getattr(self, "pressure_cmap_combo", None)
        if combo is not None:
            combo.blockSignals(True)
            combo.setCurrentIndex(max(combo.findText(cmap), 0))
            combo.blockSignals(False)
        display_combo = getattr(self, "pressure_display_combo", None)
        if display_combo is not None:
            display_combo.blockSignals(True)
            display_combo.setCurrentIndex(max(display_combo.findData(mode), 0))
            display_combo.blockSignals(False)

    def _load_branding_pixmap(self, path: Path, height: int) -> QtGui.QPixmap | None:
        if not path.exists():
            return None
        pixmap = QtGui.QPixmap(str(path))
        if pixmap.isNull():
            return None
        return pixmap.scaledToHeight(height, QtCore.Qt.SmoothTransformation)

    def _format_bytes(self, value: int) -> str:
        size = float(max(0, value))
        units = ["B", "KB", "MB", "GB", "TB"]
        for unit in units:
            if size < 1024.0 or unit == units[-1]:
                if unit == "B":
                    return f"{int(size)} {unit}"
                return f"{size:.1f} {unit}"
            size /= 1024.0
        return "0 B"

    def _show_camera_backend_warning(self) -> None:
        warning = self.camera_controller.backend_warning()
        if not warning or self._camera_warning_shown:
            return
        self._camera_warning_shown = True
        QtWidgets.QMessageBox.warning(self, "工业相机 SDK 检查", warning)

    def _append_legacy_binding_warnings(self) -> None:
        for binding in self.settings.camera_bindings:
            if binding.device_serial or not binding.device_name:
                continue
            self._append_pressure_log(
                f"{SLOT_LABELS[binding.slot]} 号机位仍是旧设备名配置“{binding.device_name}”，请在系统设置中重新绑定序列号。"
            )

    def closeEvent(self, event) -> None:  # noqa: N802
        if self.recording.state == STATE_RECORDING:
            self._stop_recording("关闭窗口，停止录制")
        if self.pressure_only_recording.is_recording:
            self._stop_pressure_only_recording("关闭窗口，停止仅触觉记录")
        self.quality_page.shutdown()
        self.recording.wait_for_background_tasks()
        self.camera_controller.shutdown()
        self.pressure_manager.shutdown()
        super().closeEvent(event)
