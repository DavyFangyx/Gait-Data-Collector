from __future__ import annotations

from pathlib import Path
from typing import Callable, List

from PyQt5 import QtCore, QtWidgets

from ...config import AppSettings
from ...pressure_sensor import PressureSnapshot
from ..widgets import FootPressureCanvas, TechPanel


class ReplayPage(QtWidgets.QWidget):
    def __init__(
        self,
        settings: AppSettings,
        load_frames: Callable[[Path], List[PressureSnapshot]],
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.settings = settings
        self.load_frames = load_frames
        self.frames: List[PressureSnapshot] = []
        self.current_index = 0
        self._build_ui()

    def _build_ui(self) -> None:
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        panel = TechPanel()
        layout = QtWidgets.QVBoxLayout(panel)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        header = QtWidgets.QHBoxLayout()
        title = QtWidgets.QLabel("数据回放")
        title.setProperty("panelTitle", True)
        self.open_button = QtWidgets.QPushButton("选择触觉 CSV")
        self.open_button.clicked.connect(self._open_file)
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(self.open_button)
        layout.addLayout(header)

        self.canvas = FootPressureCanvas("left_foot")
        self._apply_canvas_settings()
        layout.addWidget(self.canvas, 1)

        self.info_label = QtWidgets.QLabel("未加载文件")
        self.info_label.setAlignment(QtCore.Qt.AlignCenter)
        layout.addWidget(self.info_label)

        self.slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.slider.setEnabled(False)
        self.slider.valueChanged.connect(self._show_frame)
        layout.addWidget(self.slider)

        controls = QtWidgets.QHBoxLayout()
        self.prev_button = QtWidgets.QPushButton("上一帧")
        self.prev_button.clicked.connect(self._show_previous)
        self.next_button = QtWidgets.QPushButton("下一帧")
        self.next_button.clicked.connect(self._show_next)
        self.play_button = QtWidgets.QPushButton("播放")
        self.play_button.clicked.connect(self._toggle_playback)
        controls.addWidget(self.prev_button)
        controls.addWidget(self.play_button)
        controls.addWidget(self.next_button)
        controls.addStretch(1)
        layout.addLayout(controls)
        root.addWidget(panel, 1)

        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(50)
        self.timer.timeout.connect(self._advance_playback)
        self._sync_controls()

    def update_settings(self, settings: AppSettings) -> None:
        self.settings = settings
        self._apply_canvas_settings()

    def _apply_canvas_settings(self) -> None:
        self.canvas.pressure_max = self.settings.pressure_display_max
        self.canvas.set_scale(self.settings.pressure_scale_x, self.settings.pressure_scale_y)
        self.canvas.set_colormap(self.settings.pressure_colormap)
        self.canvas.set_display_mode(self.settings.pressure_display_mode)

    def _open_file(self) -> None:
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
            frames = self.load_frames(path)
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "脚压回放失败", str(exc))
            return
        if not frames:
            QtWidgets.QMessageBox.information(self, "脚压回放", "文件中没有可回放的脚压帧。")
            return
        if frames[0].sensor_id != self.canvas.sensor_id:
            layout = self.layout().itemAt(0).widget().layout()
            old_canvas = self.canvas
            new_canvas = FootPressureCanvas(frames[0].sensor_id)
            self.canvas = new_canvas
            self._apply_canvas_settings()
            layout.replaceWidget(old_canvas, new_canvas)
            old_canvas.deleteLater()
        self.frames = frames
        self.current_index = 0
        self.slider.setRange(0, len(frames) - 1)
        self.slider.setEnabled(True)
        self._show_frame(0)
        self._sync_controls()

    def _show_frame(self, index: int) -> None:
        if not self.frames:
            return
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
        self._sync_controls()

    def _show_previous(self) -> None:
        self._show_frame(self.current_index - 1)

    def _show_next(self) -> None:
        self._show_frame(self.current_index + 1)

    def _toggle_playback(self) -> None:
        if not self.frames:
            return
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

    def _sync_controls(self) -> None:
        has_frames = bool(self.frames)
        self.prev_button.setEnabled(has_frames and self.current_index > 0)
        self.next_button.setEnabled(has_frames and self.current_index < len(self.frames) - 1)
        self.play_button.setEnabled(has_frames)
