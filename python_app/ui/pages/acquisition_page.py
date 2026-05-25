from __future__ import annotations

from typing import Dict

from PyQt5 import QtCore, QtWidgets

from ...constants import CAMERA_SLOTS, PRESSURE_SENSORS
from ..widgets import FootPressureCanvas, PreviewTile, TechPanel


class AcquisitionPage(QtWidgets.QWidget):
    def __init__(
        self,
        preview_tiles: Dict[str, PreviewTile],
        pressure_canvases: Dict[str, list[FootPressureCanvas]],
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.preview_tiles = preview_tiles
        self.pressure_canvases = pressure_canvases
        self.pressure_summary_rows: Dict[str, Dict[str, QtWidgets.QLabel]] = {}
        self.pressure_stats_rows: Dict[str, Dict[str, QtWidgets.QLabel]] = {}
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

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

        bottom_splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        bottom_splitter.setChildrenCollapsible(False)
        bottom_splitter.setHandleWidth(8)
        bottom_splitter.addWidget(self._build_status_panel())
        bottom_splitter.addWidget(self._build_pressure_panel())
        bottom_splitter.setStretchFactor(0, 2)
        bottom_splitter.setStretchFactor(1, 3)
        bottom_splitter.setMinimumHeight(230)

        body_splitter.addWidget(main_splitter)
        body_splitter.addWidget(bottom_splitter)
        body_splitter.setStretchFactor(0, 4)
        body_splitter.setStretchFactor(1, 1)
        body_splitter.setSizes([660, 260])
        layout.addWidget(body_splitter, 1)

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

        title = QtWidgets.QLabel("双脚压力可视化")
        title.setProperty("panelTitle", True)
        layout.addWidget(title)

        canvas_row = QtWidgets.QHBoxLayout()
        canvas_row.setSpacing(8)
        for sensor in PRESSURE_SENSORS:
            label = QtWidgets.QLabel(sensor.label)
            label.setAlignment(QtCore.Qt.AlignCenter)
            label.setProperty("muted", True)
            col = QtWidgets.QVBoxLayout()
            col.setSpacing(4)
            col.addWidget(label)
            canvas = FootPressureCanvas(sensor.key)
            self.pressure_canvases.setdefault(sensor.key, []).append(canvas)
            col.addWidget(canvas, 1)
            canvas_row.addLayout(col, 1)
        layout.addLayout(canvas_row, 1)
        return panel

    def _build_status_panel(self) -> QtWidgets.QWidget:
        panel = TechPanel()
        layout = QtWidgets.QVBoxLayout(panel)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(7)
        title = QtWidgets.QLabel("运行状态")
        title.setProperty("panelTitle", True)
        self.connected_label = QtWidgets.QLabel()
        self.elapsed_label = QtWidgets.QLabel()
        self.output_label = QtWidgets.QLabel()
        self.output_label.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(self.connected_label)
        layout.addWidget(self.elapsed_label)
        layout.addWidget(self.output_label)
        self.binding_labels = {}
        for slot in CAMERA_SLOTS:
            label = QtWidgets.QLabel()
            label.setWordWrap(True)
            self.binding_labels[slot.key] = label
            layout.addWidget(label)
        layout.addStretch(1)
        return panel

    def _build_pressure_panel(self) -> QtWidgets.QWidget:
        panel = TechPanel()
        layout = QtWidgets.QVBoxLayout(panel)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(8)
        title = QtWidgets.QLabel("触觉串口与解析")
        title.setProperty("panelTitle", True)
        layout.addWidget(title)
        for sensor in PRESSURE_SENSORS:
            row = QtWidgets.QWidget()
            row_layout = QtWidgets.QGridLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setHorizontalSpacing(12)
            row_layout.setVerticalSpacing(4)
            state_label = QtWidgets.QLabel(f"{sensor.label}：未连接")
            meta_label = QtWidgets.QLabel("未配置端口")
            meta_label.setProperty("muted", True)
            frame_label = QtWidgets.QLabel("收到 0 | 成功 0 | 失败 0")
            frame_label.setProperty("muted", True)
            error_label = QtWidgets.QLabel("")
            error_label.setProperty("muted", True)
            error_label.setWordWrap(True)
            row_layout.addWidget(state_label, 0, 0)
            row_layout.addWidget(frame_label, 0, 1)
            row_layout.addWidget(meta_label, 1, 0)
            row_layout.addWidget(error_label, 1, 1)
            row_layout.setColumnStretch(0, 1)
            row_layout.setColumnStretch(1, 2)
            layout.addWidget(row)
            self.pressure_summary_rows[sensor.key] = {
                "state": state_label,
                "meta": meta_label,
            }
            self.pressure_stats_rows[sensor.key] = {
                "frames": frame_label,
                "error": error_label,
            }
        self.pressure_log_view = QtWidgets.QPlainTextEdit()
        self.pressure_log_view.setReadOnly(True)
        self.pressure_log_view.setMaximumBlockCount(300)
        self.pressure_log_view.setProperty("console", True)
        layout.addWidget(self.pressure_log_view, 1)
        return panel
