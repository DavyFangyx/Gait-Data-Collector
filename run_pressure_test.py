#!/usr/bin/env python3
"""
压力信息测试程序 - 简单显示两只脚的触觉信息
"""

from __future__ import annotations

import sys
import traceback
import threading as _threading
from pathlib import Path
from typing import Dict, List
import numpy as np

from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from matplotlib.path import Path as MplPath
from scipy.interpolate import RBFInterpolator

from PyQt5 import QtCore, QtGui, QtWidgets

from python_app.branding import APP_ICON_PATH
from python_app.config import SettingsStore, PressureSensorSettings
from python_app.pressure_sensor import PressureSensorManager, PressureSnapshot, available_serial_ports
from python_app.constants import PRESSURE_SENSORS
from python_app.ui.widgets import TechPanel


class PressureSettingsDialog(QtWidgets.QDialog):
    """压力传感器设置对话框"""

    def __init__(self, settings: List[PressureSensorSettings], parent=None):
        super().__init__(parent)
        self.setWindowTitle("压力传感器设置")
        self.setModal(True)
        self.resize(600, 400)
        self._settings = settings.copy()
        self._pressure_rows = {}
        self._build_ui()
        self._load_settings()

    @property
    def settings(self) -> List[PressureSensorSettings]:
        return self._settings

    def _build_ui(self):
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(16)

        hint = QtWidgets.QLabel("左右脚分别配置独立串口。启用后才会连接并接收数据。")
        hint.setProperty("role", "subtitle")
        root.addWidget(hint)

        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        content = QtWidgets.QWidget()
        content_layout = QtWidgets.QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(14)

        port_names = [""] + available_serial_ports()
        for sensor in PRESSURE_SENSORS:
            group = QtWidgets.QGroupBox(sensor.label)
            layout = QtWidgets.QFormLayout(group)
            layout.setLabelAlignment(QtCore.Qt.AlignRight)
            layout.setFormAlignment(QtCore.Qt.AlignTop)
            layout.setSpacing(12)

            enabled = QtWidgets.QCheckBox("启用该传感器")
            port = QtWidgets.QComboBox()
            port.setEditable(True)
            port.addItems(port_names)

            layout.addRow("", enabled)
            layout.addRow("串口号", port)
            content_layout.addWidget(group)
            self._pressure_rows[sensor.key] = {
                "enabled": enabled,
                "port": port,
            }

        content_layout.addStretch(1)
        scroll.setWidget(content)
        root.addWidget(scroll, 1)

        button_box = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Save | QtWidgets.QDialogButtonBox.Cancel
        )
        button_box.accepted.connect(self._accept)
        button_box.rejected.connect(self.reject)
        root.addWidget(button_box)

    def _load_settings(self):
        pressure_map = {item.sensor_id: item for item in self._settings}
        for sensor in PRESSURE_SENSORS:
            item = pressure_map.get(sensor.key, PressureSensorSettings(sensor_id=sensor.key))
            widgets = self._pressure_rows[sensor.key]
            widgets["enabled"].setChecked(item.enabled)
            widgets["port"].setCurrentText(item.port)

    def _accept(self):
        for sensor in PRESSURE_SENSORS:
            widgets = self._pressure_rows[sensor.key]
            self._settings = [
                item if item.sensor_id != sensor.key else PressureSensorSettings(
                    sensor_id=sensor.key,
                    enabled=widgets["enabled"].isChecked(),
                    port=widgets["port"].currentText(),
                )
                for item in self._settings
            ]
        self.accept()


# ---------------------------------------------------------------------------
# 鞋垫布局加载（轮廓 + 传感点，归一化坐标）
# ---------------------------------------------------------------------------
_LAYOUT_DIR = Path(__file__).parent / "foot_sensor_layout"


def _load_foot_layout(sensor_id: str):
    foot = "leftfoot" if "left" in sensor_id else "rightfoot"
    outline = np.genfromtxt(
        _LAYOUT_DIR / f"{foot}_curve.csv", delimiter=",", skip_header=1, usecols=(1, 2)
    ).astype(np.float64)
    dots = np.genfromtxt(
        _LAYOUT_DIR / f"{foot}_dots.csv", delimiter=",", skip_header=1, usecols=(1, 2)
    ).astype(np.float64)
    # 180° 旋转：数据坐标系与实际朝向相反，需翻转
    outline[:, 0] = 1.0 - outline[:, 0]
    outline[:, 1] = 1.0 - outline[:, 1]
    dots[:, 0] = 1.0 - dots[:, 0]
    dots[:, 1] = 1.0 - dots[:, 1]
    return outline, dots


class FootPressureCanvas(FigureCanvasQTAgg):
    """基于 RBF 插值 + 鞋垫轮廓蒙版的压力热力图组件

    架构：RBF 计算在独立线程运行，主线程通过 20 fps 定时器刷新画面，
    避免高帧率输入卡死 Qt 事件循环。
    """

    # 后台线程把插值结果发射回主线程
    _grid_computed = QtCore.pyqtSignal(object)  # payload: np.ndarray

    def __init__(self, sensor_id: str, parent=None):
        self._fig = Figure(facecolor="#1e1e1e", tight_layout=True)
        super().__init__(self._fig)
        self.setParent(parent)
        self.sensor_id = sensor_id
        self.snapshot: PressureSnapshot | None = None
        self.render_enabled = True
        self.pressure_max = 1000.0  # 热力图归一化上限
        self._scale_x = 1.0
        self._scale_y = 1.3
        self._cmap = "viridis"
        self.setMinimumSize(300, 400)

        # 加载布局原始数据（已翻转）
        self._outline_raw, self._sensor_coords_raw = _load_foot_layout(sensor_id)

        # 工作版本号：重建网格时递增，过期的后台结果会被丢弃
        self._layout_version = 0

        self._ax = self._fig.add_subplot(111)
        self._ax.set_facecolor("#1e1e1e")
        self._ax.set_aspect("equal")
        self._ax.axis("off")

        # 后台计算状态
        self._worker_busy = False
        self._pending_snapshot: PressureSnapshot | None = None
        self._latest_grid: np.ndarray | None = None
        self._mesh = None  # 首帧创建，后续复用

        # 主线程接收后台结果
        self._grid_computed.connect(self._on_grid_computed, QtCore.Qt.QueuedConnection)

        # 渲染节拍：20 fps 上限，避免阻塞主线程
        self._render_timer = QtCore.QTimer(self)
        self._render_timer.setInterval(50)
        self._render_timer.timeout.connect(self._tick_render)
        self._render_timer.start()

        # 用当前 scale 初始化网格
        self._rebuild_grid_mesh()

        # 初始占位符
        self._ax.text(0.5, 0.5, "无数据", color="#969696", ha="center", va="center",
                      transform=self._ax.transAxes, fontsize=12)

    def set_snapshot(self, snapshot: PressureSnapshot | None):
        self.snapshot = snapshot
        if snapshot is None or snapshot.display_values is None or not self.render_enabled:
            return
        self._pending_snapshot = snapshot  # 始终保留最新帧
        if not self._worker_busy:
            self._launch_worker()

    def set_render_enabled(self, enabled: bool):
        self.render_enabled = enabled
        if not enabled:
            self._show_placeholder("热力图已关闭")

    def set_pressure_max(self, value: float):
        self.pressure_max = max(1.0, value)
        if self.snapshot is not None and self.snapshot.display_values is not None:
            self._pending_snapshot = self.snapshot
            if not self._worker_busy:
                self._launch_worker()

    def set_scale(self, scale_x: float, scale_y: float):
        """更新坐标缩放倍数并重建插值网格"""
        self._scale_x = max(0.1, scale_x)
        self._scale_y = max(0.1, scale_y)
        self._rebuild_grid_mesh()
        if self.snapshot is not None and self.snapshot.display_values is not None:
            self._pending_snapshot = self.snapshot
            if not self._worker_busy:
                self._launch_worker()

    def set_colormap(self, cmap: str):
        """切换配色方案"""
        self._cmap = cmap
        if self._mesh is not None:
            self._mesh.set_cmap(cmap)
            self.draw_idle()

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _rebuild_grid_mesh(self):
        """根据当前 scale 重新计算 xi/yi/mask 及缩放后的坐标"""
        self._layout_version += 1
        # 应用缩放
        outline = self._outline_raw.copy()
        outline[:, 0] *= self._scale_x
        outline[:, 1] *= self._scale_y
        self._outline = outline

        coords = self._sensor_coords_raw.copy()
        coords[:, 0] *= self._scale_x
        coords[:, 1] *= self._scale_y
        self._sensor_coords = coords

        xs, ys = outline[:, 0], outline[:, 1]
        margin_x = 0.02 * self._scale_x
        margin_y = 0.02 * self._scale_y
        self._xi, self._yi = np.meshgrid(
            np.linspace(xs.min() - margin_x, xs.max() + margin_x, 120),
            np.linspace(ys.min() - margin_y, ys.max() + margin_y, 120),
        )
        pts_flat = np.c_[self._xi.ravel(), self._yi.ravel()]
        foot_path = MplPath(np.vstack([outline, outline[0]]))
        self._mask = ~foot_path.contains_points(pts_flat).reshape(self._xi.shape)
        # 丢弃已有结果，强制重建 artist
        self._latest_grid = None
        self._mesh = None

    def _launch_worker(self):
        """把最新快照提交到后台线程做 RBF 计算"""
        snap = self._pending_snapshot
        if snap is None:
            return
        self._pending_snapshot = None
        self._worker_busy = True

        raw_vals = np.array([float(v) for v in snap.display_values], dtype=np.float64)
        k = min(len(raw_vals), len(self._sensor_coords))
        coords = self._sensor_coords[:k].copy()
        forces = np.clip(raw_vals[:k] / self.pressure_max, 0.0, 1.0)
        xi, yi = self._xi, self._yi  # 只读共享，线程安全
        version = self._layout_version  # 捕获当前版本

        def _rbf(coords=coords, forces=forces, xi=xi, yi=yi):
            try:
                rbf = RBFInterpolator(coords, forces, kernel="thin_plate_spline", smoothing=0.5)
                grid = rbf(np.c_[xi.ravel(), yi.ravel()]).reshape(xi.shape)
                grid = np.clip(grid, 0.0, 1.0)
            except Exception:
                grid = np.zeros_like(xi)
            self._grid_computed.emit((version, grid))

        _threading.Thread(target=_rbf, daemon=True).start()

    def _on_grid_computed(self, payload):
        """Qt 队列信号回调，在主线程调用"""
        version, grid = payload
        if version != self._layout_version:
            # 版本不匹配：scale 已变更，丢弃过期结果
            self._worker_busy = False
            return
        self._latest_grid = grid
        self._worker_busy = False
        # 若期间又来了新帧，立即继续计算
        if self._pending_snapshot is not None:
            self._launch_worker()

    def _tick_render(self):
        """20 fps 渲染节拍"""
        if not self.render_enabled or self._latest_grid is None:
            return
        grid = self._latest_grid
        self._latest_grid = None
        self._paint_grid(grid)

    def _show_placeholder(self, msg: str):
        self._ax.cla()
        self._ax.set_facecolor("#1e1e1e")
        self._ax.set_aspect("equal")
        self._ax.axis("off")
        self._ax.text(0.5, 0.5, msg, color="#969696", ha="center", va="center",
                      transform=self._ax.transAxes, fontsize=12)
        self._mesh = None
        self.draw_idle()

    def _paint_grid(self, grid: np.ndarray):
        """在主线程做 matplotlib 更新（计算已在后台完成）"""
        grid_masked = np.ma.array(grid, mask=self._mask)
        if self._mesh is None:
            # 首次：创建所有 artists
            self._ax.cla()
            self._ax.set_facecolor("#1e1e1e")
            self._ax.set_aspect("equal")
            self._ax.axis("off")
            self._mesh = self._ax.pcolormesh(
                self._xi, self._yi, grid_masked,
                cmap=self._cmap, vmin=0, vmax=1, shading="gouraud",
            )
            self._ax.scatter(
                self._sensor_coords[:, 0], self._sensor_coords[:, 1],
                s=8, c="white", edgecolors="gray", linewidths=0.3, zorder=5,
            )
            outline_closed = np.vstack([self._outline, self._outline[0]])
            self._ax.plot(outline_closed[:, 0], outline_closed[:, 1], "k-", linewidth=1.2)
        else:
            # 快速路径：只更新色值，不重建 artists
            self._mesh.set_array(grid_masked)
        self.draw_idle()


class PressureTestWindow(QtWidgets.QMainWindow):
    """压力测试主窗口"""

    def __init__(self, settings_store: SettingsStore):
        super().__init__()
        self.settings_store = settings_store
        self.settings = settings_store.load()
        self.pressure_manager = PressureSensorManager()
        self.pressure_grids: Dict[str, FootPressureCanvas] = {}

        self._build_ui()
        self._connect_signals()
        self._apply_settings()

    def _apply_settings(self):
        """应用设置"""
        self.pressure_manager.update_settings(self.settings.pressure_sensors)
        pmax = self.settings.pressure_display_max
        sx = self.settings.pressure_scale_x
        sy = self.settings.pressure_scale_y
        cmap = self.settings.pressure_colormap
        for canvas in self.pressure_grids.values():
            canvas.pressure_max = pmax
            canvas._scale_x = sx
            canvas._scale_y = sy
            canvas._cmap = cmap
            canvas._rebuild_grid_mesh()

    def _build_ui(self):
        """构建UI"""
        self.setWindowTitle("压力信息测试")
        self.resize(1200, 800)  # 设置更大的默认窗口大小
        self.setMinimumSize(1000, 700)

        # 菜单栏
        menubar = self.menuBar()
        settings_menu = menubar.addMenu("设置")
        pressure_action = settings_menu.addAction("压力传感器设置")
        pressure_action.triggered.connect(self._open_pressure_settings)

        central = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(central)

        # 标题
        title = QtWidgets.QLabel("两只脚触觉信息显示")
        title.setAlignment(QtCore.Qt.AlignCenter)
        title.setStyleSheet("font-size: 18px; font-weight: bold; margin: 10px;")
        layout.addWidget(title)

        # 网格布局
        grid_layout = QtWidgets.QHBoxLayout()

        # 左脚
        left_group = QtWidgets.QGroupBox("左脚")
        left_layout = QtWidgets.QVBoxLayout(left_group)
        self.pressure_grids["left_foot"] = FootPressureCanvas("left_foot")
        left_layout.addWidget(self.pressure_grids["left_foot"])
        grid_layout.addWidget(left_group)

        # 右脚
        right_group = QtWidgets.QGroupBox("右脚")
        right_layout = QtWidgets.QVBoxLayout(right_group)
        self.pressure_grids["right_foot"] = FootPressureCanvas("right_foot")
        right_layout.addWidget(self.pressure_grids["right_foot"])
        grid_layout.addWidget(right_group)

        layout.addLayout(grid_layout, 1)  # 让网格区域占主要空间

        # 控制面板
        control_layout = QtWidgets.QHBoxLayout()

        self.serial_button = QtWidgets.QPushButton("打开串口")
        self.serial_button.clicked.connect(self._toggle_serial)
        control_layout.addWidget(self.serial_button)

        self.status_label = QtWidgets.QLabel("状态: 未连接")
        control_layout.addWidget(self.status_label)

        control_layout.addStretch()
        layout.addLayout(control_layout)

        # 热力图渲染开关
        render_layout = QtWidgets.QHBoxLayout()
        render_label = QtWidgets.QLabel("热力图渲染：")
        self.render_toggle = QtWidgets.QCheckBox()
        self.render_toggle.setChecked(True)
        self.render_toggle.stateChanged.connect(self._toggle_render)
        render_layout.addWidget(render_label)
        render_layout.addWidget(self.render_toggle)

        # MAX 压力上限
        render_layout.addSpacing(20)
        max_label = QtWidgets.QLabel("压力上限 MAX：")
        self.max_spinbox = QtWidgets.QDoubleSpinBox()
        self.max_spinbox.setRange(1.0, 1e9)
        self.max_spinbox.setDecimals(1)
        self.max_spinbox.setSingleStep(100.0)
        self.max_spinbox.setValue(self.settings.pressure_display_max)
        self.max_spinbox.setToolTip("热力图按照 0 – MAX 渲染，商业值越大则顯示越亮")
        self.max_spinbox.valueChanged.connect(self._on_max_changed)
        render_layout.addWidget(max_label)
        render_layout.addWidget(self.max_spinbox)

        # X/Y 轴缩放
        render_layout.addSpacing(20)
        render_layout.addWidget(QtWidgets.QLabel("X 倍数："))
        self.scale_x_spinbox = QtWidgets.QDoubleSpinBox()
        self.scale_x_spinbox.setRange(0.1, 10.0)
        self.scale_x_spinbox.setDecimals(2)
        self.scale_x_spinbox.setSingleStep(0.05)
        self.scale_x_spinbox.setValue(self.settings.pressure_scale_x)
        self.scale_x_spinbox.setToolTip("水平坐标缩放倍数")
        self.scale_x_spinbox.valueChanged.connect(self._on_scale_changed)
        render_layout.addWidget(self.scale_x_spinbox)

        render_layout.addWidget(QtWidgets.QLabel("Y 倍数："))
        self.scale_y_spinbox = QtWidgets.QDoubleSpinBox()
        self.scale_y_spinbox.setRange(0.1, 10.0)
        self.scale_y_spinbox.setDecimals(2)
        self.scale_y_spinbox.setSingleStep(0.05)
        self.scale_y_spinbox.setValue(self.settings.pressure_scale_y)
        self.scale_y_spinbox.setToolTip("垂直坐标缩放倍数")
        self.scale_y_spinbox.valueChanged.connect(self._on_scale_changed)
        render_layout.addWidget(self.scale_y_spinbox)

        # 配色方案
        render_layout.addSpacing(20)
        render_layout.addWidget(QtWidgets.QLabel("配色："))
        self.cmap_combo = QtWidgets.QComboBox()
        self.cmap_combo.addItems(["viridis", "RdYlBu", "YlOrRd"])
        idx = self.cmap_combo.findText(self.settings.pressure_colormap)
        self.cmap_combo.setCurrentIndex(max(idx, 0))
        self.cmap_combo.currentTextChanged.connect(self._on_colormap_changed)
        render_layout.addWidget(self.cmap_combo)

        render_layout.addStretch()
        layout.addLayout(render_layout)

        self.setCentralWidget(central)

    def _connect_signals(self):
        """连接信号"""
        self.pressure_manager.frame_received.connect(self._handle_pressure_frame)
        self.pressure_manager.connection_changed.connect(self._handle_connection_changed)

    def _handle_pressure_frame(self, sensor_id: str, frame):
        """处理压力帧"""
        if sensor_id in self.pressure_grids:
            # 获取传感器配置
            sensor_settings = None
            for item in self.settings.pressure_sensors:
                if item.sensor_id == sensor_id:
                    sensor_settings = item
                    break
            # 创建快照
            snapshot = PressureSnapshot(
                sensor_id=sensor_id,
                label=getattr(next((s for s in PRESSURE_SENSORS if s.key == sensor_id), None), 'label', sensor_id),
                connected=True,
                port=sensor_settings.port if sensor_settings else "",
                raw_values=frame.raw_values,
                display_values=frame.display_values,
                last_frame_index=frame.frame_index,
                last_received_at=frame.captured_at,
                message="正常"
            )
            self.pressure_grids[sensor_id].set_snapshot(snapshot)

    def _handle_connection_changed(self, sensor_id: str, connected: bool, port: str):
        """处理连接变化"""
        status = f"状态: {sensor_id} - {'已连接' if connected else '未连接'}"
        self.status_label.setText(status)

    def _open_pressure_settings(self):
        """打开压力传感器设置"""
        dialog = PressureSettingsDialog(self.settings.pressure_sensors, self)
        if dialog.exec_() == QtWidgets.QDialog.Accepted:
            self.settings.pressure_sensors = dialog.settings
            self.settings_store.save(self.settings)
            self.pressure_manager.update_settings(self.settings.pressure_sensors)

    def _toggle_serial(self):
        """切换串口连接"""
        if self.serial_button.text() == "打开串口":
            # 启用所有传感器
            enabled_settings = []
            for item in self.settings.pressure_sensors:
                enabled_settings.append(PressureSensorSettings(
                    sensor_id=item.sensor_id,
                    enabled=True,
                    port=item.port,
                ))
            self.settings.pressure_sensors = enabled_settings
            self.settings_store.save(self.settings)
            self.pressure_manager.update_settings(enabled_settings)
            self.serial_button.setText("关闭串口")
        else:
            # 禁用所有传感器
            disabled_settings = []
            for item in self.settings.pressure_sensors:
                disabled_settings.append(PressureSensorSettings(
                    sensor_id=item.sensor_id,
                    enabled=False,
                    port=item.port,
                ))
            self.settings.pressure_sensors = disabled_settings
            self.settings_store.save(self.settings)
            self.pressure_manager.update_settings(disabled_settings)
            self.serial_button.setText("打开串口")

    def _toggle_render(self, state):
        """切换热力图渲染"""
        render_enabled = state == QtCore.Qt.Checked
        for grid in self.pressure_grids.values():
            grid.set_render_enabled(render_enabled)

    def _on_max_changed(self, value: float):
        """MAX 压力上限变化时更新所有 Canvas 并持久化"""
        for canvas in self.pressure_grids.values():
            canvas.set_pressure_max(value)
        self.settings.pressure_display_max = value
        self.settings_store.save(self.settings)

    def _on_scale_changed(self):
        """X/Y 缩放变化时重建网格"""
        sx = self.scale_x_spinbox.value()
        sy = self.scale_y_spinbox.value()
        for canvas in self.pressure_grids.values():
            canvas.set_scale(sx, sy)
        self.settings.pressure_scale_x = sx
        self.settings.pressure_scale_y = sy
        self.settings_store.save(self.settings)

    def _on_colormap_changed(self, cmap: str):
        """配色方案切换"""
        for canvas in self.pressure_grids.values():
            canvas.set_colormap(cmap)
        self.settings.pressure_colormap = cmap
        self.settings_store.save(self.settings)

    def closeEvent(self, event):
        """关闭事件"""
        self.pressure_manager.shutdown()
        super().closeEvent(event)


def run_pressure_test() -> int:
    """运行压力测试程序"""
    try:
        from PyQt5 import QtGui, QtWidgets
    except ImportError as exc:
        raise RuntimeError("缺少 PyQt5，请先执行 `python -m pip install -r requirements.txt`。") from exc

    app = QtWidgets.QApplication(sys.argv)
    app.setApplicationName("压力信息测试")

    if APP_ICON_PATH.exists():
        app.setWindowIcon(QtGui.QIcon(str(APP_ICON_PATH)))

    store = SettingsStore()
    window = PressureTestWindow(store)
    window.show()
    return app.exec_()


def main() -> None:
    try:
        raise SystemExit(run_pressure_test())
    except RuntimeError as exc:
        print(exc, file=sys.stderr)
        raise SystemExit(1) from exc
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc()
        raise SystemExit(1)


if __name__ == "__main__":
    main()
