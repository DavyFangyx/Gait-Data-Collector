from __future__ import annotations

from dataclasses import replace
from typing import Callable, Dict, List, Optional

from PyQt5 import QtCore, QtWidgets

from ..camera_discovery import CameraDescriptor
from ..config import AppSettings, CameraBinding, PressureSensorSettings
from ..constants import CAMERA_SLOTS, PRESSURE_SENSORS
from ..mvs import CameraParameterProfile
from ..pressure_sensor import available_serial_ports
from .widgets import TechPanel


PARAMETER_HELP = {
    "exposure": "曝光时间越长，画面越亮，但运动拖影越明显，且会直接压低可达到的最高帧率。",
    "fps": "采集帧率决定单位时间内采样次数，但实际帧率会受到曝光时间和相机节点上限共同限制。",
    "gain": "增益会放大传感器信号，能提亮画面，但噪声也会一起放大。",
    "gamma": "Gamma 会调整中间灰度的明暗分布，主要影响观感和细节层次。",
    "black_level": "黑电平会整体抬高或压低暗部基线，过高会让黑色发灰，过低会吞掉暗部细节。",
}


class SettingsDialog(QtWidgets.QDialog):
    def __init__(
        self,
        settings: AppSettings,
        devices: List[CameraDescriptor],
        camera_profiles: Optional[Dict[str, CameraParameterProfile]] = None,
        camera_profile_provider: Optional[Callable[[str], CameraParameterProfile | None]] = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("系统设置")
        self.setModal(True)
        self.resize(980, 760)
        self._settings = replace(settings)
        self._devices = devices
        self._camera_profiles = dict(camera_profiles or {})
        self._camera_profile_provider = camera_profile_provider
        self._subject_edit = QtWidgets.QPlainTextEdit()
        self._camera_rows = {}
        self._camera_parameter_rows = {}
        self._pressure_rows = {}
        self._build_ui()
        self._load_settings()

    @property
    def settings(self) -> AppSettings:
        return self._settings

    def _build_ui(self) -> None:
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(16)

        tabs = QtWidgets.QTabWidget()
        tabs.addTab(self._create_general_tab(), "采集参数")
        tabs.addTab(self._create_camera_tab(), "机位映射")
        tabs.addTab(self._create_camera_parameter_tab(), "工业相机参数")
        tabs.addTab(self._create_pressure_tab(), "脚压传感器")
        tabs.addTab(self._create_subject_tab(), "对象列表")
        root.addWidget(tabs)

        button_box = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Save | QtWidgets.QDialogButtonBox.Cancel
        )
        button_box.accepted.connect(self._accept)
        button_box.rejected.connect(self.reject)
        root.addWidget(button_box)

    def _create_general_tab(self) -> QtWidgets.QWidget:
        panel = TechPanel()
        layout = QtWidgets.QFormLayout(panel)
        layout.setLabelAlignment(QtCore.Qt.AlignRight)
        layout.setFormAlignment(QtCore.Qt.AlignTop)
        layout.setSpacing(14)

        self.output_dir_edit = QtWidgets.QLineEdit()
        browse_button = QtWidgets.QPushButton("选择目录")
        browse_button.clicked.connect(self._choose_output_dir)
        output_wrap = QtWidgets.QHBoxLayout()
        output_wrap.addWidget(self.output_dir_edit, 1)
        output_wrap.addWidget(browse_button)

        self.image_type_combo = QtWidgets.QComboBox()
        self.image_type_combo.addItems(["jpg", "bmp", "tif"])
        self.frame_type_combo = QtWidgets.QComboBox()
        self.frame_type_combo.addItems(["jpg", "bmp", "tif"])
        self.movie_rate_spin = QtWidgets.QSpinBox()
        self.movie_rate_spin.setRange(500, 50000)
        self.movie_rate_spin.setSuffix(" kbps")
        self.record_limit_spin = QtWidgets.QDoubleSpinBox()
        self.record_limit_spin.setRange(1.0, 3600.0)
        self.record_limit_spin.setDecimals(1)
        self.record_limit_spin.setSuffix(" 秒")
        self.trigger_check = QtWidgets.QCheckBox("启用软件触发")
        self.trigger_fps_spin = QtWidgets.QDoubleSpinBox()
        self.trigger_fps_spin.setRange(1.0, 240.0)
        self.trigger_fps_spin.setDecimals(1)
        self.trigger_fps_spin.setSuffix(" FPS")
        self.trigger_check.toggled.connect(self.trigger_fps_spin.setEnabled)

        layout.addRow("保存目录", output_wrap)
        layout.addRow("拍照图片格式", self.image_type_combo)
        layout.addRow("逐帧图片格式", self.frame_type_combo)
        layout.addRow("视频码率", self.movie_rate_spin)
        layout.addRow("录制时长上限", self.record_limit_spin)
        layout.addRow("", self.trigger_check)
        layout.addRow("软件触发频率", self.trigger_fps_spin)
        return panel

    def _create_camera_tab(self) -> QtWidgets.QWidget:
        panel = TechPanel()
        layout = QtWidgets.QVBoxLayout(panel)
        layout.setSpacing(12)
        hint = QtWidgets.QLabel("给 1-4 号机位绑定工业相机序列号。界面显示型号、自定义名和 IP，配置只保存序列号。")
        hint.setProperty("role", "subtitle")
        layout.addWidget(hint)

        table = QtWidgets.QTableWidget(len(CAMERA_SLOTS), 5)
        table.setHorizontalHeaderLabels(["编号", "设备（型号 / 名称 / 序列号 / IP）", "输出 ID", "参与录像", "参与拍照"])
        header = table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QtWidgets.QHeaderView.Fixed)
        header.setSectionResizeMode(1, QtWidgets.QHeaderView.Stretch)
        header.setSectionResizeMode(2, QtWidgets.QHeaderView.Fixed)
        header.setSectionResizeMode(3, QtWidgets.QHeaderView.Fixed)
        header.setSectionResizeMode(4, QtWidgets.QHeaderView.Fixed)
        table.setColumnWidth(0, 56)
        table.setColumnWidth(2, 96)
        table.setColumnWidth(3, 84)
        table.setColumnWidth(4, 84)
        table.verticalHeader().setVisible(False)
        table.setAlternatingRowColors(True)
        table.setSelectionMode(QtWidgets.QAbstractItemView.NoSelection)
        table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.camera_table = table

        for row, slot in enumerate(CAMERA_SLOTS):
            table.setItem(row, 0, QtWidgets.QTableWidgetItem(slot.label))
            combo = QtWidgets.QComboBox()
            combo.addItem("未绑定", "")
            for item in self._devices:
                combo.addItem(item.summary, item.serial_number)
            combo.currentIndexChanged.connect(
                lambda _index, slot_key=slot.key: self._sync_camera_parameter_device(slot_key)
            )
            output_id = QtWidgets.QLineEdit(slot.default_output_id)
            record_check = QtWidgets.QCheckBox()
            record_check.setChecked(True)
            snapshot_check = QtWidgets.QCheckBox()
            snapshot_check.setChecked(True)
            record_box = QtWidgets.QWidget()
            record_layout = QtWidgets.QHBoxLayout(record_box)
            record_layout.setContentsMargins(0, 0, 0, 0)
            record_layout.addWidget(record_check, alignment=QtCore.Qt.AlignCenter)
            snapshot_box = QtWidgets.QWidget()
            snapshot_layout = QtWidgets.QHBoxLayout(snapshot_box)
            snapshot_layout.setContentsMargins(0, 0, 0, 0)
            snapshot_layout.addWidget(snapshot_check, alignment=QtCore.Qt.AlignCenter)
            table.setCellWidget(row, 1, combo)
            table.setCellWidget(row, 2, output_id)
            table.setCellWidget(row, 3, record_box)
            table.setCellWidget(row, 4, snapshot_box)
            self._camera_rows[slot.key] = {
                "combo": combo,
                "output_id": output_id,
                "record": record_check,
                "snapshot": snapshot_check,
            }
        layout.addWidget(table)
        return panel

    def _create_camera_parameter_tab(self) -> QtWidgets.QWidget:
        panel = TechPanel()
        layout = QtWidgets.QVBoxLayout(panel)
        layout.setSpacing(12)

        hint = QtWidgets.QLabel("为每个机位设置工业相机成像参数。保存后会重启相机流并重新下发参数。实际帧率会受曝光时间限制，例如 70000 us 的曝光理论上限约为 14.3 FPS。")
        hint.setProperty("role", "subtitle")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        table = QtWidgets.QTableWidget(len(CAMERA_SLOTS), 7)
        table.setHorizontalHeaderLabels(["编号", "当前绑定设备", "曝光时间 ⓘ", "采集帧率 ⓘ", "增益 ⓘ", "Gamma ⓘ", "黑电平 ⓘ"])
        parameter_header = table.horizontalHeader()
        parameter_header.setStretchLastSection(False)
        parameter_header.setSectionResizeMode(0, QtWidgets.QHeaderView.Fixed)
        parameter_header.setSectionResizeMode(1, QtWidgets.QHeaderView.Stretch)
        for index in range(2, 7):
            parameter_header.setSectionResizeMode(index, QtWidgets.QHeaderView.Fixed)
        table.setColumnWidth(0, 56)
        table.setColumnWidth(2, 150)
        table.setColumnWidth(3, 150)
        table.setColumnWidth(4, 120)
        table.setColumnWidth(5, 120)
        table.setColumnWidth(6, 120)
        table.verticalHeader().setVisible(False)
        table.setAlternatingRowColors(True)
        table.setSelectionMode(QtWidgets.QAbstractItemView.NoSelection)
        table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.camera_parameter_table = table

        for row, slot in enumerate(CAMERA_SLOTS):
            table.setItem(row, 0, QtWidgets.QTableWidgetItem(slot.label))

            device_label = QtWidgets.QLabel("未绑定设备")
            device_label.setWordWrap(False)
            device_label.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred)

            exposure_spin = QtWidgets.QDoubleSpinBox()
            exposure_spin.setRange(1.0, 1_000_000.0)
            exposure_spin.setDecimals(1)
            exposure_spin.setSuffix(" us")
            exposure_spin.setSingleStep(100.0)

            fps_spin = QtWidgets.QDoubleSpinBox()
            fps_spin.setRange(0.1, 240.0)
            fps_spin.setDecimals(1)
            fps_spin.setSuffix(" FPS")
            fps_spin.setSingleStep(1.0)

            gain_spin = QtWidgets.QDoubleSpinBox()
            gain_spin.setRange(0.0, 48.0)
            gain_spin.setDecimals(1)
            gain_spin.setSuffix(" dB")
            gain_spin.setSingleStep(0.5)

            gamma_spin = QtWidgets.QDoubleSpinBox()
            gamma_spin.setRange(0.1, 4.0)
            gamma_spin.setDecimals(2)
            gamma_spin.setSingleStep(0.05)

            black_level_spin = QtWidgets.QDoubleSpinBox()
            black_level_spin.setRange(0.0, 255.0)
            black_level_spin.setDecimals(1)
            black_level_spin.setSingleStep(0.5)

            exposure_spin.valueChanged.connect(lambda _value, slot_key=slot.key: self._sync_linked_parameter_limits(slot_key, "exposure"))
            fps_spin.valueChanged.connect(lambda _value, slot_key=slot.key: self._sync_linked_parameter_limits(slot_key, "fps"))

            table.setCellWidget(row, 1, device_label)
            table.setCellWidget(row, 2, exposure_spin)
            table.setCellWidget(row, 3, fps_spin)
            table.setCellWidget(row, 4, gain_spin)
            table.setCellWidget(row, 5, gamma_spin)
            table.setCellWidget(row, 6, black_level_spin)

            self._camera_parameter_rows[slot.key] = {
                "device": device_label,
                "exposure": exposure_spin,
                "fps": fps_spin,
                "gain": gain_spin,
                "gamma": gamma_spin,
                "black_level": black_level_spin,
            }

        header_tooltips = {
            2: PARAMETER_HELP["exposure"],
            3: PARAMETER_HELP["fps"],
            4: PARAMETER_HELP["gain"],
            5: PARAMETER_HELP["gamma"],
            6: PARAMETER_HELP["black_level"],
        }
        for column, text in header_tooltips.items():
            item = table.horizontalHeaderItem(column)
            if item is not None:
                item.setToolTip(text)

        layout.addWidget(table)
        return panel

    def _create_pressure_tab(self) -> QtWidgets.QWidget:
        panel = TechPanel()
        outer = QtWidgets.QVBoxLayout(panel)
        outer.setSpacing(14)
        hint = QtWidgets.QLabel("左右脚分别配置独立串口。启用后才会连接并参与录制保存。")
        hint.setProperty("role", "subtitle")
        outer.addWidget(hint)

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
        outer.addWidget(scroll, 1)
        return panel

    def _create_subject_tab(self) -> QtWidgets.QWidget:
        panel = TechPanel()
        layout = QtWidgets.QVBoxLayout(panel)
        layout.setSpacing(12)
        tip = QtWidgets.QLabel("每行一个对象名称，会同步保存到 SubjectList.txt。")
        tip.setProperty("role", "subtitle")
        layout.addWidget(tip)
        layout.addWidget(self._subject_edit, 1)
        return panel

    def _load_settings(self) -> None:
        self.output_dir_edit.setText(self._settings.save_dir)
        self.image_type_combo.setCurrentText(self._settings.image_type)
        self.frame_type_combo.setCurrentText(self._settings.frame_type)
        self.movie_rate_spin.setValue(self._settings.movie_rate_kbps)
        self.record_limit_spin.setValue(self._settings.record_limit_sec)
        self.trigger_check.setChecked(self._settings.software_trigger_enabled)
        self.trigger_fps_spin.setValue(self._settings.software_trigger_fps)
        self.trigger_fps_spin.setEnabled(self._settings.software_trigger_enabled)

        binding_map = {item.slot: item for item in self._settings.camera_bindings}
        for slot in CAMERA_SLOTS:
            binding = binding_map.get(slot.key, CameraBinding(slot=slot.key, output_id=slot.default_output_id))
            widgets = self._camera_rows[slot.key]
            index = widgets["combo"].findData(binding.device_serial)
            if index >= 0:
                widgets["combo"].setCurrentIndex(index)
            elif binding.device_serial:
                widgets["combo"].addItem(f"未发现设备：{binding.device_serial}", binding.device_serial)
                widgets["combo"].setCurrentIndex(widgets["combo"].count() - 1)
            elif binding.device_name:
                widgets["combo"].addItem(f"旧配置待迁移：{binding.device_name}", "")
                widgets["combo"].setCurrentIndex(widgets["combo"].count() - 1)
            else:
                widgets["combo"].setCurrentIndex(0)
            widgets["output_id"].setText(binding.output_id or slot.default_output_id)
            widgets["record"].setChecked(binding.record_enabled)
            widgets["snapshot"].setChecked(binding.snapshot_enabled)
            parameter_widgets = self._camera_parameter_rows[slot.key]
            parameter_widgets["exposure"].setValue(binding.exposure_time_us)
            parameter_widgets["fps"].setValue(binding.acquisition_frame_rate_fps)
            parameter_widgets["gain"].setValue(binding.gain_db)
            parameter_widgets["gamma"].setValue(binding.gamma_value)
            parameter_widgets["black_level"].setValue(binding.black_level)

        self._refresh_all_camera_parameter_devices()

        pressure_map = {item.sensor_id: item for item in self._settings.pressure_sensors}
        for sensor in PRESSURE_SENSORS:
            item = pressure_map.get(sensor.key, PressureSensorSettings(sensor_id=sensor.key))
            widgets = self._pressure_rows[sensor.key]
            widgets["enabled"].setChecked(item.enabled)
            widgets["port"].setCurrentText(item.port)

        self._subject_edit.setPlainText("\n".join(self._settings.subjects))

    def _choose_output_dir(self) -> None:
        target = QtWidgets.QFileDialog.getExistingDirectory(self, "选择保存目录", self.output_dir_edit.text())
        if target:
            self.output_dir_edit.setText(target)

    def _ensure_camera_profile(self, serial_number: str) -> CameraParameterProfile | None:
        if not serial_number:
            return None
        profile = self._camera_profiles.get(serial_number)
        if profile is not None:
            return profile
        if self._camera_profile_provider is None:
            return None
        profile = self._camera_profile_provider(serial_number)
        if profile is not None:
            self._camera_profiles[serial_number] = profile
        return profile

    def _apply_parameter_range(self, spin: QtWidgets.QDoubleSpinBox, minimum: float, maximum: float) -> None:
        old_state = spin.blockSignals(True)
        spin.setRange(minimum, maximum)
        spin.setValue(min(max(spin.value(), minimum), maximum))
        spin.blockSignals(old_state)

    def _apply_camera_profile_to_slot(self, slot_key: str) -> None:
        widgets = self._camera_parameter_rows.get(slot_key)
        mapping_widgets = self._camera_rows.get(slot_key)
        if widgets is None or mapping_widgets is None:
            return
        serial = (mapping_widgets["combo"].currentData() or "").strip()
        profile = self._ensure_camera_profile(serial)

        exposure_spin = widgets["exposure"]
        fps_spin = widgets["fps"]
        gain_spin = widgets["gain"]
        gamma_spin = widgets["gamma"]
        black_level_spin = widgets["black_level"]

        if profile is None:
            exposure_spin.setToolTip("未读取到当前相机范围，使用默认区间。")
            fps_spin.setToolTip("未读取到当前相机范围，使用默认区间。")
            gain_spin.setToolTip("未读取到当前相机范围，使用默认区间。")
            gamma_spin.setToolTip("未读取到当前相机范围，使用默认区间。")
            black_level_spin.setToolTip("未读取到当前相机范围，使用默认区间。")
            self._sync_linked_parameter_limits(slot_key, "exposure")
            return

        exposure = profile.get("ExposureTime")
        fps = profile.get("AcquisitionFrameRate")
        gain = profile.get("Gain")
        gamma = profile.get("Gamma")
        black_level = profile.get("BlackLevel")

        if exposure is not None:
            self._apply_parameter_range(exposure_spin, exposure.minimum, exposure.maximum)
            exposure_spin.setToolTip(f"当前相机曝光范围：{exposure.minimum:.1f} - {exposure.maximum:.1f} us")
        if fps is not None:
            self._apply_parameter_range(fps_spin, fps.minimum, fps.maximum)
            fps_spin.setToolTip(f"当前相机帧率范围：{fps.minimum:.1f} - {fps.maximum:.1f} FPS")
        if gain is not None:
            self._apply_parameter_range(gain_spin, gain.minimum, gain.maximum)
            gain_spin.setToolTip(f"当前相机增益范围：{gain.minimum:.1f} - {gain.maximum:.1f} dB")
        if gamma is not None:
            self._apply_parameter_range(gamma_spin, gamma.minimum, gamma.maximum)
            gamma_spin.setToolTip(f"当前相机 Gamma 范围：{gamma.minimum:.2f} - {gamma.maximum:.2f}")
        if black_level is not None:
            self._apply_parameter_range(black_level_spin, black_level.minimum, black_level.maximum)
            black_level_spin.setToolTip(f"当前相机黑电平范围：{black_level.minimum:.1f} - {black_level.maximum:.1f}")

        self._sync_linked_parameter_limits(slot_key, "exposure")

    def _sync_linked_parameter_limits(self, slot_key: str, source: str) -> None:
        widgets = self._camera_parameter_rows.get(slot_key)
        mapping_widgets = self._camera_rows.get(slot_key)
        if widgets is None or mapping_widgets is None:
            return
        serial = (mapping_widgets["combo"].currentData() or "").strip()
        profile = self._ensure_camera_profile(serial)
        if profile is None:
            return

        exposure_meta = profile.get("ExposureTime")
        fps_meta = profile.get("AcquisitionFrameRate")
        if exposure_meta is None or fps_meta is None:
            return

        exposure_spin = widgets["exposure"]
        fps_spin = widgets["fps"]

        device_exposure_min = exposure_meta.minimum
        device_exposure_max = exposure_meta.maximum
        device_fps_min = fps_meta.minimum
        device_fps_max = fps_meta.maximum

        if source == "fps" and fps_spin.value() > 0:
            max_exposure_for_fps = min(device_exposure_max, 1_000_000.0 / fps_spin.value())
            self._apply_parameter_range(exposure_spin, device_exposure_min, max(device_exposure_min, max_exposure_for_fps))
        else:
            self._apply_parameter_range(exposure_spin, device_exposure_min, device_exposure_max)

        theoretical_fps_max = 1_000_000.0 / max(exposure_spin.value(), 1.0)
        effective_fps_max = min(device_fps_max, theoretical_fps_max)
        self._apply_parameter_range(fps_spin, device_fps_min, max(device_fps_min, effective_fps_max))
        fps_spin.setToolTip(
            f"当前相机帧率范围：{device_fps_min:.1f} - {device_fps_max:.1f} FPS\n"
            f"按当前曝光时间理论上限：{effective_fps_max:.1f} FPS"
        )

    def _refresh_all_camera_parameter_devices(self) -> None:
        for slot in CAMERA_SLOTS:
            self._sync_camera_parameter_device(slot.key)

    def _sync_camera_parameter_device(self, slot_key: str) -> None:
        mapping_widgets = self._camera_rows.get(slot_key)
        parameter_widgets = self._camera_parameter_rows.get(slot_key)
        if mapping_widgets is None or parameter_widgets is None:
            return
        combo = mapping_widgets["combo"]
        serial = (combo.currentData() or "").strip()
        label = parameter_widgets["device"]
        if not serial:
            label.setText("未绑定设备")
            label.setToolTip("")
            self._apply_camera_profile_to_slot(slot_key)
            return
        descriptor = next((item for item in self._devices if item.serial_number == serial), None)
        if descriptor is not None:
            compact_text = f"{descriptor.display_name} | SN {descriptor.serial_tail}"
            full_text = descriptor.summary
        else:
            compact_text = f"序列号 {serial}"
            full_text = combo.currentText().strip() or compact_text
        metrics = label.fontMetrics()
        label.setText(metrics.elidedText(compact_text, QtCore.Qt.ElideRight, 260))
        label.setToolTip(full_text)
        self._apply_camera_profile_to_slot(slot_key)

    def _accept(self) -> None:
        bindings = []
        used_serials = set()
        output_ids = set()
        for slot in CAMERA_SLOTS:
            widgets = self._camera_rows[slot.key]
            device_serial = (widgets["combo"].currentData() or "").strip()
            output_id = widgets["output_id"].text().strip()
            if not output_id:
                QtWidgets.QMessageBox.warning(self, "设置有误", f"{slot.label} 的输出 ID 不能为空。")
                return
            if output_id in output_ids:
                QtWidgets.QMessageBox.warning(self, "设置有误", "输出 ID 不能重复。")
                return
            output_ids.add(output_id)
            if device_serial:
                if device_serial in used_serials:
                    QtWidgets.QMessageBox.warning(self, "设置有误", "机位绑定的相机序列号不能重复。")
                    return
                used_serials.add(device_serial)
            bindings.append(
                CameraBinding(
                    slot=slot.key,
                    device_serial=device_serial,
                    output_id=output_id,
                    record_enabled=widgets["record"].isChecked(),
                    snapshot_enabled=widgets["snapshot"].isChecked(),
                    exposure_time_us=self._camera_parameter_rows[slot.key]["exposure"].value(),
                    acquisition_frame_rate_fps=self._camera_parameter_rows[slot.key]["fps"].value(),
                    gain_db=self._camera_parameter_rows[slot.key]["gain"].value(),
                    gamma_value=self._camera_parameter_rows[slot.key]["gamma"].value(),
                    black_level=self._camera_parameter_rows[slot.key]["black_level"].value(),
                )
            )
        if not any(item.record_enabled for item in bindings):
            QtWidgets.QMessageBox.warning(self, "设置有误", "至少需要一个参与录像的机位。")
            return

        sensors = []
        used_ports = set()
        for sensor in PRESSURE_SENSORS:
            widgets = self._pressure_rows[sensor.key]
            enabled = widgets["enabled"].isChecked()
            port = widgets["port"].currentText().strip()

            if enabled and not port:
                QtWidgets.QMessageBox.warning(self, "设置有误", f"{sensor.label} 已启用，但未配置串口。")
                return
            if enabled and port in used_ports:
                QtWidgets.QMessageBox.warning(self, "设置有误", "左右脚不能使用同一个串口。")
                return
            if port:
                used_ports.add(port)

            sensors.append(
                PressureSensorSettings(
                    sensor_id=sensor.key,
                    enabled=enabled,
                    port=port,
                )
            )

        subjects = [item.strip() for item in self._subject_edit.toPlainText().splitlines() if item.strip()]
        if not subjects:
            subjects = ["测试"]

        self._settings = AppSettings(
            save_dir=self.output_dir_edit.text().strip(),
            image_type=self.image_type_combo.currentText().lower(),
            frame_type=self.frame_type_combo.currentText().lower(),
            movie_rate_kbps=self.movie_rate_spin.value(),
            record_limit_sec=self.record_limit_spin.value(),
            software_trigger_enabled=self.trigger_check.isChecked(),
            software_trigger_fps=self.trigger_fps_spin.value(),
            camera_bindings=bindings,
            pressure_sensors=sensors,
            subjects=subjects,
            pressure_quality_auto=self._settings.pressure_quality_auto,
            pressure_quality_output_dir=self._settings.pressure_quality_output_dir,
            pressure_processing_method=self._settings.pressure_processing_method,
            pressure_suspect_left=self._settings.pressure_suspect_left,
            pressure_suspect_right=self._settings.pressure_suspect_right,
        )
        self.accept()
