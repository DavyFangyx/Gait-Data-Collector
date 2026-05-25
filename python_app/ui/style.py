from PyQt5 import QtGui


AMBER = "#f7b65a"
TEAL = "#3fd8b5"
CYAN = "#66d8ff"
RED = "#ff7b7b"
BG = "#070b10"
PANEL = "#111821"
TEXT = "#f4f7fb"
MUTED = "#96a8b8"


def create_palette() -> QtGui.QPalette:
    palette = QtGui.QPalette()
    palette.setColor(QtGui.QPalette.Window, QtGui.QColor(BG))
    palette.setColor(QtGui.QPalette.WindowText, QtGui.QColor(TEXT))
    palette.setColor(QtGui.QPalette.Base, QtGui.QColor("#10161d"))
    palette.setColor(QtGui.QPalette.AlternateBase, QtGui.QColor("#182029"))
    palette.setColor(QtGui.QPalette.ToolTipBase, QtGui.QColor(TEXT))
    palette.setColor(QtGui.QPalette.ToolTipText, QtGui.QColor(BG))
    palette.setColor(QtGui.QPalette.Text, QtGui.QColor(TEXT))
    palette.setColor(QtGui.QPalette.Button, QtGui.QColor(PANEL))
    palette.setColor(QtGui.QPalette.ButtonText, QtGui.QColor(TEXT))
    palette.setColor(QtGui.QPalette.Highlight, QtGui.QColor(AMBER))
    palette.setColor(QtGui.QPalette.HighlightedText, QtGui.QColor(BG))
    palette.setColor(QtGui.QPalette.Link, QtGui.QColor(CYAN))
    return palette


def app_stylesheet() -> str:
    return f"""
    QWidget {{
        background-color: {BG};
        color: {TEXT};
        font-family: "Microsoft YaHei", "Segoe UI";
        font-size: 19px;
    }}
    QMainWindow, QDialog {{
        background-color: {BG};
    }}
    QLabel {{
        background: transparent;
        color: {TEXT};
    }}
    QLabel[role="title"] {{
        font-size: 32px;
        font-weight: 800;
        color: #fff7ec;
        letter-spacing: 1px;
    }}
    QLabel[role="subtitle"], QLabel[muted="true"] {{
        color: {MUTED};
    }}
    QLabel[panelTitle="true"] {{
        font-size: 24px;
        font-weight: 700;
        color: #fff1d4;
    }}
    QLabel[panelAccent="true"] {{
        color: {CYAN};
        font-size: 13px;
        font-weight: 700;
        letter-spacing: 1px;
    }}
    QLabel[stateBadge="true"] {{
        border-radius: 14px;
        padding: 11px 22px;
        font-weight: 700;
        min-width: 108px;
        qproperty-alignment: AlignCenter;
        font-size: 18px;
        color: #071017;
        background: rgba(247, 182, 90, 0.95);
        border: 1px solid rgba(255, 235, 202, 0.75);
    }}
    QLabel[stateBadge="true"][level="normal"] {{
        color: #081218;
        background: rgba(63, 216, 181, 0.9);
        border: 1px solid rgba(176, 255, 237, 0.65);
    }}
    QLabel[stateBadge="true"][level="warning"] {{
        color: #120d03;
        background: rgba(247, 182, 90, 0.95);
        border: 1px solid rgba(255, 235, 202, 0.75);
    }}
    QLabel[stateBadge="true"][level="danger"] {{
        color: #170809;
        background: rgba(255, 123, 123, 0.95);
        border: 1px solid rgba(255, 210, 210, 0.75);
    }}
    QLabel[chipRole="status"] {{
        border-radius: 11px;
        padding: 7px 14px;
        font-size: 16px;
        font-weight: 700;
        background: rgba(247, 182, 90, 0.16);
        border: 1px solid rgba(247, 182, 90, 0.45);
        color: #ffe4b5;
    }}
    QLabel[chipRole="status"][level="normal"] {{
        background: rgba(63, 216, 181, 0.14);
        border: 1px solid rgba(63, 216, 181, 0.45);
        color: #dcfff7;
    }}
    QLabel[chipRole="status"][level="warning"] {{
        background: rgba(247, 182, 90, 0.16);
        border: 1px solid rgba(247, 182, 90, 0.45);
        color: #ffe4b5;
    }}
    QLabel[chipRole="status"][level="danger"] {{
        background: rgba(255, 123, 123, 0.16);
        border: 1px solid rgba(255, 123, 123, 0.48);
        color: #ffe5e5;
    }}
    QLabel[memoryBadge="true"] {{
        border-radius: 11px;
        padding: 7px 14px;
        font-size: 16px;
        font-weight: 700;
        background: rgba(63, 216, 181, 0.14);
        border: 1px solid rgba(63, 216, 181, 0.35);
        color: #dffdf6;
    }}
    QLabel[memoryBadge="true"][level="warning"] {{
        background: rgba(247, 182, 90, 0.16);
        border: 1px solid rgba(247, 182, 90, 0.38);
        color: #ffe4b5;
    }}
    QLabel[memoryBadge="true"][level="danger"] {{
        background: rgba(255, 123, 123, 0.16);
        border: 1px solid rgba(255, 123, 123, 0.38);
        color: #ffe5e5;
    }}
    QLabel[previewBadge="true"] {{
        border-radius: 16px;
        background: rgba(247, 182, 90, 0.92);
        color: #081218;
        font-size: 21px;
        font-weight: 800;
    }}
    QLabel[previewInfo="true"] {{
        font-size: 18px;
        font-weight: 600;
        color: #edf3f8;
    }}
    QLabel[previewCanvas="true"] {{
        border-radius: 14px;
        background-color: rgba(4, 8, 12, 0.96);
        border: 1px solid rgba(102, 216, 255, 0.18);
        color: #ced9e3;
    }}
    QLabel[metricTitle="true"] {{
        color: #d4e2ee;
        font-weight: 600;
        font-size: 16px;
    }}
    QLabel[metricValue="true"] {{
        color: #fff7ec;
        font-weight: 700;
        font-size: 16px;
    }}
    QFrame[card="true"] {{
        background-color: rgba(17, 24, 33, 0.96);
        border: 1px solid rgba(102, 216, 255, 0.16);
        border-radius: 16px;
    }}
    QPushButton {{
        background-color: #161e27;
        color: {TEXT};
        border: 1px solid rgba(102, 216, 255, 0.24);
        border-radius: 12px;
        padding: 12px 22px;
        font-weight: 700;
        font-size: 18px;
        min-height: 28px;
    }}
    QPushButton:hover {{
        background-color: #1f2a36;
        border-color: rgba(102, 216, 255, 0.65);
    }}
    QPushButton:disabled {{
        color: #708292;
        border-color: #273440;
        background-color: #10161c;
    }}
    QPushButton[accent="true"] {{
        background-color: {AMBER};
        color: #081218;
        border-color: #ffd79a;
    }}
    QPushButton[accent="true"]:hover {{
        background-color: #ffc870;
    }}
    QPushButton[danger="true"] {{
        background-color: {RED};
        color: #12080a;
        border-color: #ffb1b1;
    }}
    QPushButton[danger="true"]:hover {{
        background-color: #ff9393;
    }}
    QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QTextEdit, QPlainTextEdit {{
        background-color: #121922;
        color: #f9fbfe;
        border: 1px solid rgba(150, 168, 184, 0.28);
        border-radius: 12px;
        padding: 11px 14px;
        selection-background-color: {AMBER};
        selection-color: #081218;
        font-size: 18px;
        min-height: 30px;
    }}
    QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QTextEdit:focus, QPlainTextEdit:focus {{
        border: 1px solid rgba(247, 182, 90, 0.85);
        background-color: #161f28;
    }}
    QComboBox QAbstractItemView {{
        background-color: #121922;
        color: #f9fbfe;
        selection-background-color: {AMBER};
        selection-color: #081218;
        border: 1px solid rgba(102, 216, 255, 0.28);
    }}
    QTabWidget::pane {{
        border: 1px solid rgba(102, 216, 255, 0.16);
        border-radius: 12px;
        top: -2px;
        background: #0d131a;
    }}
    QTabBar::tab {{
        background: #131922;
        color: #d9e4ee;
        border: 1px solid rgba(150, 168, 184, 0.18);
        padding: 15px 28px;
        margin-right: 8px;
        border-top-left-radius: 12px;
        border-top-right-radius: 12px;
        font-weight: 600;
        font-size: 19px;
        min-width: 150px;
    }}
    QTabBar::tab:selected {{
        background: #1e2832;
        color: #fff7ec;
        border-color: rgba(247, 182, 90, 0.45);
    }}
    QGroupBox {{
        border: 1px solid rgba(150, 168, 184, 0.22);
        border-radius: 14px;
        margin-top: 12px;
        padding-top: 18px;
        font-weight: 700;
        color: #fff1d4;
    }}
    QGroupBox::title {{
        subcontrol-origin: margin;
        left: 12px;
        padding: 0 6px;
        color: {CYAN};
    }}
    QHeaderView::section, QTableCornerButton::section {{
        background-color: #1a2430;
        color: #fff1d4;
        padding: 13px;
        border: none;
        border-right: 1px solid rgba(102, 216, 255, 0.16);
        border-bottom: 1px solid rgba(102, 216, 255, 0.16);
        font-weight: 700;
        font-size: 17px;
    }}
    QTableWidget {{
        background-color: #0f151d;
        alternate-background-color: #141c24;
        gridline-color: rgba(150, 168, 184, 0.12);
        color: {TEXT};
        border: 1px solid rgba(150, 168, 184, 0.22);
        border-radius: 12px;
    }}
    QPlainTextEdit[console="true"] {{
        background-color: #091018;
        border: 1px solid rgba(102, 216, 255, 0.18);
        color: #e9f2fb;
        font-family: Consolas, "Cascadia Mono", monospace;
        font-size: 16px;
        line-height: 1.3;
    }}
    QProgressBar {{
        min-height: 8px;
        max-height: 8px;
        background-color: #0d141b;
        border: 1px solid rgba(102, 216, 255, 0.12);
        border-radius: 4px;
    }}
    QProgressBar::chunk {{
        border-radius: 3px;
        background-color: {TEAL};
    }}
    QProgressBar[level="warning"]::chunk {{
        background-color: {AMBER};
    }}
    QProgressBar[level="danger"]::chunk {{
        background-color: {RED};
    }}
    QScrollBar:vertical {{
        background: transparent;
        width: 10px;
        margin: 6px 0;
    }}
    QScrollBar::handle:vertical {{
        background: rgba(102, 216, 255, 0.32);
        border-radius: 5px;
    }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
        background: transparent;
        height: 0px;
    }}
    QSplitter::handle {{
        background: rgba(102, 216, 255, 0.08);
    }}
    QSplitter::handle:hover {{
        background: rgba(102, 216, 255, 0.18);
    }}
    QCheckBox, QRadioButton {{
        spacing: 8px;
        color: {TEXT};
    }}
    """
