from __future__ import annotations

from typing import Callable, Dict

from PyQt5 import QtCore, QtWidgets

from ...config import AppSettings
from ...constants import PRESSURE_SENSORS
from ..widgets import FootPressureCanvas, TechPanel


class PressureLivePage(QtWidgets.QWidget):
    def __init__(
        self,
        settings: AppSettings,
        pressure_canvases: Dict[str, list[FootPressureCanvas]],
        on_display_mode_changed: Callable[[int], None],
        on_pressure_max_changed: Callable[[float], None],
        on_pressure_scale_changed: Callable[[], None],
        on_colormap_changed: Callable[[str], None],
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.settings = settings
        self.pressure_canvases = pressure_canvases
        self.on_display_mode_changed = on_display_mode_changed
        self.on_pressure_max_changed = on_pressure_max_changed
        self.on_pressure_scale_changed = on_pressure_scale_changed
        self.on_colormap_changed = on_colormap_changed
        self._build_ui()

    def _build_ui(self) -> None:
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        panel = TechPanel()
        layout = QtWidgets.QVBoxLayout(panel)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        title = QtWidgets.QLabel("触觉实时显示")
        title.setProperty("panelTitle", True)
        layout.addWidget(title)

        canvas_row = QtWidgets.QHBoxLayout()
        canvas_row.setSpacing(12)
        for sensor in PRESSURE_SENSORS:
            label = QtWidgets.QLabel(sensor.label)
            label.setAlignment(QtCore.Qt.AlignCenter)
            label.setProperty("muted", True)
            col = QtWidgets.QVBoxLayout()
            col.setSpacing(6)
            col.addWidget(label)
            canvas = FootPressureCanvas(sensor.key)
            self.pressure_canvases.setdefault(sensor.key, []).append(canvas)
            col.addWidget(canvas, 1)
            canvas_row.addLayout(col, 1)
        layout.addLayout(canvas_row, 1)
        layout.addLayout(self._build_controls())
        root.addWidget(panel, 1)

    def _build_controls(self) -> QtWidgets.QHBoxLayout:
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
            self.on_display_mode_changed
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
        self.pressure_max_spin.setToolTip("热力图按 0-MAX 渲染")
        self.pressure_max_spin.valueChanged.connect(self.on_pressure_max_changed)
        ctrl_row.addWidget(self.pressure_max_spin)

        ctrl_row.addSpacing(6)
        ctrl_row.addWidget(QtWidgets.QLabel("X 倍:"))
        self.pressure_scale_x_spin = QtWidgets.QDoubleSpinBox()
        self.pressure_scale_x_spin.setRange(0.1, 10.0)
        self.pressure_scale_x_spin.setDecimals(2)
        self.pressure_scale_x_spin.setSingleStep(0.05)
        self.pressure_scale_x_spin.setMaximumWidth(70)
        self.pressure_scale_x_spin.setValue(self.settings.pressure_scale_x)
        self.pressure_scale_x_spin.valueChanged.connect(self.on_pressure_scale_changed)
        ctrl_row.addWidget(self.pressure_scale_x_spin)

        ctrl_row.addWidget(QtWidgets.QLabel("Y 倍:"))
        self.pressure_scale_y_spin = QtWidgets.QDoubleSpinBox()
        self.pressure_scale_y_spin.setRange(0.1, 10.0)
        self.pressure_scale_y_spin.setDecimals(2)
        self.pressure_scale_y_spin.setSingleStep(0.05)
        self.pressure_scale_y_spin.setMaximumWidth(70)
        self.pressure_scale_y_spin.setValue(self.settings.pressure_scale_y)
        self.pressure_scale_y_spin.valueChanged.connect(self.on_pressure_scale_changed)
        ctrl_row.addWidget(self.pressure_scale_y_spin)

        ctrl_row.addSpacing(6)
        self.pressure_cmap_combo = QtWidgets.QComboBox()
        self.pressure_cmap_combo.addItems(["viridis", "RdYlBu", "YlOrRd"])
        index = self.pressure_cmap_combo.findText(self.settings.pressure_colormap)
        self.pressure_cmap_combo.setCurrentIndex(max(index, 0))
        self.pressure_cmap_combo.currentTextChanged.connect(self.on_colormap_changed)
        ctrl_row.addWidget(self.pressure_cmap_combo)

        ctrl_row.addStretch(1)
        return ctrl_row
