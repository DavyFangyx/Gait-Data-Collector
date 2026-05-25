from __future__ import annotations

import threading as _threading
from pathlib import Path

import numpy as np
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from matplotlib.path import Path as MplPath
from scipy.interpolate import RBFInterpolator
from PyQt5 import QtCore, QtGui, QtWidgets

from ..pressure_sensor import PressureSnapshot


def repolish(widget: QtWidgets.QWidget) -> None:
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()


class TechPanel(QtWidgets.QFrame):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setProperty("card", True)

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        rect = self.rect().adjusted(1, 1, -1, -1)
        gradient = QtGui.QLinearGradient(rect.topLeft(), rect.bottomRight())
        gradient.setColorAt(0.0, QtGui.QColor(24, 32, 40, 242))
        gradient.setColorAt(0.4, QtGui.QColor(14, 20, 28, 247))
        gradient.setColorAt(1.0, QtGui.QColor(7, 11, 16, 250))
        path = QtGui.QPainterPath()
        path.addRoundedRect(QtCore.QRectF(rect), 16, 16)
        painter.fillPath(path, QtGui.QBrush(gradient))

        painter.setClipPath(path)
        painter.setPen(QtGui.QPen(QtGui.QColor(102, 216, 255, 14), 1))
        step = 26
        for x in range(rect.left(), rect.right(), step):
            painter.drawLine(x, rect.top(), x, rect.bottom())
        for y in range(rect.top(), rect.bottom(), step):
            painter.drawLine(rect.left(), y, rect.right(), y)


class MetricBar(QtWidgets.QWidget):
    def __init__(self, title: str, parent=None) -> None:
        super().__init__(parent)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        header = QtWidgets.QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(10)
        self.title_label = QtWidgets.QLabel(title)
        self.title_label.setProperty("metricTitle", True)
        self.value_label = QtWidgets.QLabel("--")
        self.value_label.setProperty("metricValue", True)
        header.addWidget(self.title_label)
        header.addStretch(1)
        header.addWidget(self.value_label)

        self.progress = QtWidgets.QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setTextVisible(False)
        self.progress.setProperty("level", "normal")

        layout.addLayout(header)
        layout.addWidget(self.progress)

    def set_state(self, value_text: str, percent: float, level: str = "normal") -> None:
        clamped = max(0.0, min(percent, 100.0))
        self.value_label.setText(value_text)
        self.progress.setValue(int(clamped * 10))
        self.progress.setProperty("level", level)
        repolish(self.progress)


class PreviewTile(TechPanel):
    def __init__(self, title: str, parent=None) -> None:
        super().__init__(parent)
        self._source_pixmap = QtGui.QPixmap()
        self._identity_text = "等待设备绑定"
        self._meta_text = "未连接"
        self._full_info_text = ""
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        self.setMinimumSize(0, 0)

        self.badge_label = QtWidgets.QLabel(title)
        self.badge_label.setAlignment(QtCore.Qt.AlignCenter)
        self.badge_label.setFixedSize(30, 30)
        self.badge_label.setProperty("previewBadge", True)

        self.info_label = QtWidgets.QLabel()
        self.info_label.setProperty("previewInfo", True)
        self.info_label.setTextInteractionFlags(QtCore.Qt.NoTextInteraction)
        self.info_label.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred)
        self.info_label.setMinimumWidth(0)
        self.info_label.setWordWrap(False)

        self.status_chip = QtWidgets.QLabel("未连接")
        self.status_chip.setAlignment(QtCore.Qt.AlignCenter)
        self.status_chip.setProperty("chipRole", "status")
        self.status_chip.setProperty("level", "warning")

        self.image_label = QtWidgets.QLabel("等待画面接入")
        self.image_label.setAlignment(QtCore.Qt.AlignCenter)
        self.image_label.setMinimumSize(0, 120)
        self.image_label.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        self.image_label.setProperty("previewCanvas", True)

        header_layout = QtWidgets.QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(10)
        header_layout.addWidget(self.badge_label, 0, QtCore.Qt.AlignTop)
        header_layout.addWidget(self.info_label, 1)
        header_layout.addWidget(self.status_chip, 0, QtCore.Qt.AlignTop)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)
        layout.addLayout(header_layout)
        layout.addWidget(self.image_label, 1)
        self._refresh_info()

    def set_identity(self, text: str) -> None:
        self._identity_text = text or "未绑定设备"
        self._refresh_info()

    def set_meta(self, text: str) -> None:
        self._meta_text = text or "未连接"
        self._refresh_info()

    def _refresh_info(self) -> None:
        self._full_info_text = self._compose_info_text()
        self.info_label.setToolTip(self._full_info_text if len(self._full_info_text) > 18 else "")
        available_width = self.info_label.width() if self.info_label.width() > 0 else 240
        elided = self.info_label.fontMetrics().elidedText(
            self._full_info_text,
            QtCore.Qt.ElideRight,
            max(40, available_width - 4),
        )
        self.info_label.setText(elided)

    def _compose_info_text(self) -> str:
        identity = (self._identity_text or "").strip()
        meta = (self._meta_text or "").strip()
        redundant_meta = {"", "未连接", "未绑定", "未绑定设备", "等待设备绑定"}

        if not identity:
            return meta or "未绑定设备"
        if not meta or identity == meta:
            return identity
        if identity == "未绑定设备" and meta in redundant_meta:
            return identity
        return f"{identity} | {meta}"

    def set_status(self, text: str, level: str = "normal") -> None:
        self.status_chip.setText(text)
        self.status_chip.setProperty("level", level)
        repolish(self.status_chip)

    def set_image(self, image: QtGui.QImage | None) -> None:
        if image is None:
            self._source_pixmap = QtGui.QPixmap()
            self.image_label.setPixmap(QtGui.QPixmap())
            self.image_label.setText("等待画面接入")
            return
        self._source_pixmap = QtGui.QPixmap.fromImage(image)
        self._refresh_pixmap()

    def _refresh_pixmap(self) -> None:
        if self._source_pixmap.isNull():
            return
        self.image_label.setText("")
        self.image_label.setPixmap(
            self._source_pixmap.scaled(
                self.image_label.size(),
                QtCore.Qt.KeepAspectRatio,
                QtCore.Qt.SmoothTransformation,
            )
        )

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._refresh_pixmap()
        self._refresh_info()


# ---------------------------------------------------------------------------
# FootPressureCanvas – RBF 插値热力图（RBF 后台线程 + 20 fps 定时器）
# ---------------------------------------------------------------------------

_FOOT_LAYOUT_DIR = Path(__file__).resolve().parent.parent.parent / "foot_sensor_layout"


def _load_foot_layout(sensor_id: str):
    """  加载布局数据并对坐标进行 180° 翻转（x=1-x, y=1-y）"""
    foot = "leftfoot" if "left" in sensor_id else "rightfoot"
    outline = np.genfromtxt(
        _FOOT_LAYOUT_DIR / f"{foot}_curve.csv", delimiter=",", skip_header=1, usecols=(1, 2)
    ).astype(np.float64)
    dots = np.genfromtxt(
        _FOOT_LAYOUT_DIR / f"{foot}_dots.csv", delimiter=",", skip_header=1, usecols=(1, 2)
    ).astype(np.float64)
    outline[:, 0] = 1.0 - outline[:, 0]
    outline[:, 1] = 1.0 - outline[:, 1]
    dots[:, 0] = 1.0 - dots[:, 0]
    dots[:, 1] = 1.0 - dots[:, 1]
    return outline, dots


class FootPressureCanvas(FigureCanvasQTAgg):
    """基于鞋底轮廓的压力可视化组件

    热力图模式下 RBF 计算在独立线程运行，主线程通过 20 fps 定时器刺新画面，
    避免高帧率输入卡死 Qt 事件循环。
    """

    _grid_computed = QtCore.pyqtSignal(object)  # payload: (version, np.ndarray)

    def __init__(self, sensor_id: str, parent=None):
        fig = Figure(facecolor="#1e1e1e", tight_layout=True)
        super().__init__(fig)
        self._fig = fig
        self.setParent(parent)
        self.sensor_id = sensor_id
        self.snapshot: PressureSnapshot | None = None
        self.render_enabled = True
        self.pressure_max = 1000.0
        self._scale_x = 1.0
        self._scale_y = 2.7
        self._cmap = "viridis"
        self._display_mode = "heatmap"
        self.setMinimumSize(120, 200)

        self._outline_raw, self._sensor_coords_raw = _load_foot_layout(sensor_id)
        self._layout_version = 0

        self._ax = self._fig.add_subplot(111)
        self._ax.set_facecolor("#1e1e1e")
        self._ax.set_aspect("equal")
        self._ax.axis("off")

        self._worker_busy = False
        self._pending_snapshot: PressureSnapshot | None = None
        self._latest_grid: np.ndarray | None = None
        self._mesh = None

        self._grid_computed.connect(self._on_grid_computed, QtCore.Qt.QueuedConnection)

        self._render_timer = QtCore.QTimer(self)
        self._render_timer.setInterval(50)
        self._render_timer.timeout.connect(self._tick_render)
        self._render_timer.start()

        self._rebuild_grid_mesh()
        self._paint_idle_view()

    # ------------------------------------------------------------------ public

    def set_snapshot(self, snapshot: PressureSnapshot | None):
        self.snapshot = snapshot
        if (
            snapshot is None
            or not self.render_enabled
            or not snapshot.connected
            or snapshot.display_values is None
        ):
            self._pending_snapshot = None
            self._latest_grid = None
            self._paint_idle_view(active=bool(snapshot and snapshot.connected))
            return
        if self._display_mode == "outline":
            self._pending_snapshot = None
            self._latest_grid = None
            self._paint_outline_points(snapshot)
            return
        self._pending_snapshot = snapshot
        if not self._worker_busy:
            self._launch_worker()

    def set_render_enabled(self, enabled: bool):
        self.render_enabled = enabled
        if not enabled:
            self._paint_idle_view()

    def set_pressure_max(self, value: float):
        self.pressure_max = max(1.0, value)
        if (
            self.snapshot is not None
            and self.snapshot.connected
            and self.snapshot.display_values is not None
        ):
            if self._display_mode == "outline":
                self._paint_outline_points(self.snapshot)
                return
            self._pending_snapshot = self.snapshot
            if not self._worker_busy:
                self._launch_worker()
        else:
            self._paint_idle_view()

    def set_scale(self, scale_x: float, scale_y: float):
        self._scale_x = max(0.1, scale_x)
        self._scale_y = max(0.1, scale_y)
        self._rebuild_grid_mesh()
        if (
            self.snapshot is not None
            and self.snapshot.connected
            and self.snapshot.display_values is not None
        ):
            if self._display_mode == "outline":
                self._paint_outline_points(self.snapshot)
                return
            self._pending_snapshot = self.snapshot
            if not self._worker_busy:
                self._launch_worker()
        else:
            self._paint_idle_view()

    def set_colormap(self, cmap: str):
        self._cmap = cmap
        if (
            self._display_mode == "outline"
            and self.snapshot is not None
            and self.snapshot.connected
            and self.snapshot.display_values is not None
        ):
            self._paint_outline_points(self.snapshot)
            return
        if self._mesh is not None:
            self._mesh.set_cmap(cmap)
            self.draw_idle()

    def set_display_mode(self, mode: str) -> None:
        normalized = mode if mode in {"heatmap", "outline"} else "heatmap"
        if normalized == self._display_mode:
            return
        self._display_mode = normalized
        self._pending_snapshot = None
        self._latest_grid = None
        self._mesh = None
        if (
            self.snapshot is None
            or not self.render_enabled
            or not self.snapshot.connected
            or self.snapshot.display_values is None
        ):
            self._paint_idle_view(active=bool(self.snapshot and self.snapshot.connected))
            return
        if self._display_mode == "outline":
            self._paint_outline_points(self.snapshot)
            return
        self._pending_snapshot = self.snapshot
        if not self._worker_busy:
            self._launch_worker()

    # ----------------------------------------------------------------- private

    def _rebuild_grid_mesh(self):
        self._layout_version += 1
        outline = self._outline_raw.copy()
        outline[:, 0] *= self._scale_x
        outline[:, 1] *= self._scale_y
        self._outline = outline
        coords = self._sensor_coords_raw.copy()
        coords[:, 0] *= self._scale_x
        coords[:, 1] *= self._scale_y
        self._sensor_coords = coords
        xs, ys = outline[:, 0], outline[:, 1]
        mx, my = 0.02 * self._scale_x, 0.02 * self._scale_y
        self._x_bounds = (xs.min() - mx, xs.max() + mx)
        self._y_bounds = (ys.min() - my, ys.max() + my)
        self._xi, self._yi = np.meshgrid(
            np.linspace(self._x_bounds[0], self._x_bounds[1], 120),
            np.linspace(self._y_bounds[0], self._y_bounds[1], 120),
        )
        pts = np.c_[self._xi.ravel(), self._yi.ravel()]
        self._mask = ~MplPath(np.vstack([outline, outline[0]])).contains_points(pts).reshape(self._xi.shape)
        self._latest_grid = None
        self._mesh = None

    def _launch_worker(self):
        snap = self._pending_snapshot
        if snap is None:
            return
        self._pending_snapshot = None
        self._worker_busy = True
        raw_vals = np.array([float(v) for v in snap.display_values], dtype=np.float64)
        k = min(len(raw_vals), len(self._sensor_coords))
        coords = self._sensor_coords[:k].copy()
        forces = np.clip(raw_vals[:k] / self.pressure_max, 0.0, 1.0)
        xi, yi = self._xi, self._yi
        version = self._layout_version

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
        version, grid = payload
        if self._display_mode != "heatmap":
            self._worker_busy = False
            return
        if version != self._layout_version:
            self._worker_busy = False
            return
        self._latest_grid = grid
        self._worker_busy = False
        if self._pending_snapshot is not None:
            self._launch_worker()

    def _tick_render(self):
        if self._display_mode != "heatmap":
            self._latest_grid = None
            return
        if not self.render_enabled or self._latest_grid is None:
            return
        grid = self._latest_grid
        self._latest_grid = None
        self._paint_grid(grid)

    def _reset_canvas(self) -> None:
        self._ax.cla()
        self._ax.set_facecolor("#171c22")
        self._ax.set_aspect("equal")
        self._ax.axis("off")
        if hasattr(self, "_x_bounds") and hasattr(self, "_y_bounds"):
            self._ax.set_xlim(*self._x_bounds)
            self._ax.set_ylim(*self._y_bounds)

    def _draw_outline_shell(self, active: bool, overlay_mode: bool = False, with_fill: bool = True) -> None:
        outline_closed = np.vstack([self._outline, self._outline[0]])
        fill_color = "#183240" if overlay_mode else ("#1c3745" if active else "#182630")
        fill_alpha = 0.22 if overlay_mode else (0.20 if active else 0.14)
        glow_color = "#4ecbff" if active else "#3f7f99"
        edge_color = "#9fe3ff" if active else "#7aa7bf"
        crisp_color = "#edfaff" if active else "#bdd7e7"

        if with_fill:
            self._ax.fill(
                self._outline[:, 0],
                self._outline[:, 1],
                color=fill_color,
                alpha=fill_alpha,
                zorder=1,
            )
        self._ax.plot(
            outline_closed[:, 0],
            outline_closed[:, 1],
            color=glow_color,
            linewidth=6.0 if overlay_mode else 5.0,
            alpha=0.16 if overlay_mode else 0.11,
            zorder=2,
        )
        self._ax.plot(
            outline_closed[:, 0],
            outline_closed[:, 1],
            color=edge_color,
            linewidth=2.3 if overlay_mode else 2.0,
            alpha=0.78 if active else 0.62,
            zorder=3,
        )
        self._ax.plot(
            outline_closed[:, 0],
            outline_closed[:, 1],
            color=crisp_color,
            linewidth=0.8,
            alpha=0.72 if active else 0.46,
            zorder=4,
        )

    def _draw_sensor_points(self, active: bool, overlay_mode: bool = False) -> None:
        self._ax.scatter(
            self._sensor_coords[:, 0],
            self._sensor_coords[:, 1],
            s=10 if overlay_mode else 8,
            c="#e8fbff" if active else "#d3e4ef",
            edgecolors="none",
            alpha=0.32 if overlay_mode else (0.26 if active else 0.16),
            zorder=5,
        )
        self._ax.scatter(
            self._sensor_coords[:, 0],
            self._sensor_coords[:, 1],
            s=20 if overlay_mode else 16,
            c="#72d7ff" if active else "#7e9eb2",
            edgecolors="none",
            alpha=0.07 if overlay_mode else (0.05 if active else 0.03),
            zorder=4,
        )

    def _paint_idle_view(self, active: bool = False):
        self._reset_canvas()
        self._draw_outline_shell(active=active, overlay_mode=False)
        self._draw_sensor_points(active=active, overlay_mode=False)
        self._mesh = None
        self.draw_idle()

    def _paint_outline_points(self, snapshot: PressureSnapshot) -> None:
        display_values = snapshot.display_values
        if display_values is None:
            display_values = []
        raw_vals = np.array([float(v) for v in display_values], dtype=np.float64)
        values = np.zeros(len(self._sensor_coords), dtype=np.float64)
        k = min(len(raw_vals), len(values))
        if k:
            values[:k] = np.clip(raw_vals[:k] / self.pressure_max, 0.0, 1.0)

        self._reset_canvas()
        self._draw_outline_shell(active=True, overlay_mode=False)
        colors = values[: len(self._sensor_coords)]
        self._ax.scatter(
            self._sensor_coords[:, 0],
            self._sensor_coords[:, 1],
            s=150,
            c=colors,
            cmap=self._cmap,
            vmin=0,
            vmax=1,
            edgecolors="#f5fbff",
            linewidths=1.0,
            alpha=0.26,
            zorder=5,
        )
        self._ax.scatter(
            self._sensor_coords[:, 0],
            self._sensor_coords[:, 1],
            s=46,
            c=colors,
            cmap=self._cmap,
            vmin=0,
            vmax=1,
            edgecolors="#1b252d",
            linewidths=0.7,
            zorder=6,
        )
        self._draw_outline_shell(active=True, overlay_mode=False, with_fill=False)
        self._mesh = None
        self.draw_idle()

    def _paint_grid(self, grid: np.ndarray):
        grid_masked = np.ma.array(grid, mask=self._mask)
        if self._mesh is None:
            self._reset_canvas()
            self._draw_outline_shell(active=True, overlay_mode=True, with_fill=True)
            self._mesh = self._ax.pcolormesh(
                self._xi, self._yi, grid_masked,
                cmap=self._cmap, vmin=0, vmax=1, shading="gouraud", alpha=0.95,
            )
            self._draw_sensor_points(active=True, overlay_mode=True)
            self._draw_outline_shell(active=True, overlay_mode=True, with_fill=False)
        else:
            self._mesh.set_array(grid_masked)
        self.draw_idle()

