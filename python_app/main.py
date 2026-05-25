from __future__ import annotations

import sys
import traceback

from .branding import APP_ICON_PATH
from .config import SettingsStore


def run() -> int:
    try:
        from PyQt5 import QtGui, QtWidgets
    except ImportError as exc:  # pragma: no cover - 依赖缺失时给出清晰提示
        raise RuntimeError(
            f"缺少 PyQt5，请用当前解释器安装依赖：`{sys.executable} -m pip install -r requirements.txt`。"
        ) from exc

    from .ui.main_window import MainWindow
    from .ui.style import app_stylesheet, create_palette

    app = QtWidgets.QApplication(sys.argv)
    app.setApplicationName("四机位同步采集系统")
    if APP_ICON_PATH.exists():
        app.setWindowIcon(QtGui.QIcon(str(APP_ICON_PATH)))
    app.setPalette(create_palette())
    app.setStyleSheet(app_stylesheet())

    store = SettingsStore()
    settings = store.load()
    window = MainWindow(store, settings)
    window.show()
    return app.exec_()


def main() -> None:
    try:
        raise SystemExit(run())
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

