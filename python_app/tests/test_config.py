import unittest
from datetime import datetime

from python_app.config import AppSettings, build_session_name, sanitize_subject_name


class ConfigTests(unittest.TestCase):
    def test_subject_name_is_sanitized(self):
        self.assertEqual(sanitize_subject_name('A/B:C*D?"E"'), "A_B_C_D_E_")

    def test_session_name_matches_legacy_pattern(self):
        name = build_session_name(datetime(2026, 3, 25, 18, 30, 5), "测试对象", 3)
        self.assertEqual(name, "rec20260325_183005_测试对象_3")

    def test_settings_from_dict_uses_pressure_sensor_interface(self):
        settings = AppSettings.from_dict(
            {
                "save_dir": "D:/Data",
                "image_type": "bmp",
                "frame_type": "jpg",
                "movie_rate_kbps": 3000,
                "record_limit_sec": 15,
                "software_trigger_enabled": True,
                "software_trigger_fps": 50,
                "camera_bindings": [
                    {
                        "slot": "top",
                        "device_serial": "MV-0001",
                        "device_name": "Camera A",
                    "output_id": "Cam16",
                    "record_enabled": True,
                    "snapshot_enabled": False,
                    "exposure_time_us": 18500.0,
                    "acquisition_frame_rate_fps": 42.0,
                    "gain_db": 6.5,
                    "gamma_value": 1.25,
                    "black_level": 3.0,
                }
            ],
                "pressure_sensors": [
                    {
                        "sensor_id": "left_foot",
                        "enabled": True,
                        "port": "COM3",
                    }
                ],
                "serial_sync": {
                    "port": "COM9",
                    "baud_rate": 115200,
                    "start_token": "START",
                    "stop_token": "STOP",
                    "outbound_start_line": "REC_START",
                    "outbound_stop_line": "REC_STOP",
                },
                "subjects": ["甲", "乙"],
            }
        )
        self.assertTrue(settings.software_trigger_enabled)
        self.assertEqual(settings.camera_bindings[0].device_serial, "MV-0001")
        self.assertEqual(settings.camera_bindings[0].device_name, "Camera A")
        self.assertEqual(settings.camera_bindings[0].output_id, "1")
        self.assertEqual(settings.camera_bindings[0].exposure_time_us, 18500.0)
        self.assertEqual(settings.camera_bindings[0].acquisition_frame_rate_fps, 42.0)
        self.assertEqual(settings.camera_bindings[0].gain_db, 6.5)
        self.assertEqual(settings.camera_bindings[0].gamma_value, 1.25)
        self.assertEqual(settings.camera_bindings[0].black_level, 3.0)
        self.assertEqual(settings.pressure_sensors[0].port, "COM3")
        self.assertEqual(settings.subjects, ["甲", "乙"])
        self.assertEqual(settings.to_dict()["pressure_sensors"][0]["sensor_id"], "left_foot")
        self.assertEqual(
            set(settings.to_dict()["pressure_sensors"][0]),
            {"sensor_id", "enabled", "port"},
        )
        self.assertNotIn("serial_sync", settings.to_dict())
        self.assertEqual(settings.to_dict()["camera_bindings"][0]["device_serial"], "MV-0001")
        self.assertNotIn("device_name", settings.to_dict()["camera_bindings"][0])
        self.assertEqual(settings.to_dict()["camera_bindings"][0]["exposure_time_us"], 18500.0)
        self.assertEqual(settings.to_dict()["camera_bindings"][0]["acquisition_frame_rate_fps"], 42.0)
        self.assertEqual(settings.to_dict()["camera_bindings"][0]["gain_db"], 6.5)
        self.assertEqual(settings.to_dict()["camera_bindings"][0]["gamma_value"], 1.25)
        self.assertEqual(settings.to_dict()["camera_bindings"][0]["black_level"], 3.0)

    def test_legacy_camera_name_is_read_for_runtime_migration(self):
        settings = AppSettings.from_dict(
            {
                "camera_bindings": [
                    {
                        "slot": "top",
                        "device_name": "Legacy Camera",
                        "output_id": "1",
                    }
                ]
            }
        )
        self.assertEqual(settings.camera_bindings[0].device_serial, "")
        self.assertEqual(settings.camera_bindings[0].device_name, "Legacy Camera")
        self.assertEqual(settings.camera_bindings[0].exposure_time_us, 20000.0)
        self.assertEqual(settings.camera_bindings[0].acquisition_frame_rate_fps, 30.0)
        self.assertEqual(settings.camera_bindings[0].gamma_value, 1.0)


if __name__ == "__main__":
    unittest.main()
