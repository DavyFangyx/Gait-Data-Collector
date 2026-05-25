from __future__ import annotations

import csv
from pathlib import Path
from typing import Iterable

from PIL import Image, ImageDraw, ImageFilter, ImageFont


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "docs" / "screenshots"
BRANDING_DIR = ROOT / "python_app" / "assets" / "branding"
FOOT_LAYOUT_DIR = ROOT / "foot_sensor_layout"

FONT_REGULAR = Path(r"C:\Windows\Fonts\msyh.ttc")
FONT_BOLD = Path(r"C:\Windows\Fonts\msyhbd.ttc")

BG = (7, 11, 16)
PANEL = (17, 24, 33)
PANEL_ALT = (11, 17, 24)
TEXT = (244, 247, 251)
MUTED = (150, 168, 184)
AMBER = (247, 182, 90)
CYAN = (102, 216, 255)
TEAL = (63, 216, 181)
RED = (255, 123, 123)
GRID = (34, 48, 60)


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONT_BOLD if bold else FONT_REGULAR), size=size)


def rounded_panel(draw: ImageDraw.ImageDraw, box, fill, outline=None, radius=18):
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=1 if outline else 0)


def draw_badge(draw: ImageDraw.ImageDraw, xy, text: str, fill, text_fill=(8, 18, 24), size=(132, 36)):
    x, y = xy
    draw.rounded_rectangle((x, y, x + size[0], y + size[1]), radius=16, fill=fill)
    width = draw.textlength(text, font=font(18, True))
    draw.text((x + (size[0] - width) / 2, y + 7), text, font=font(18, True), fill=text_fill)


def draw_grid(draw: ImageDraw.ImageDraw, box, color=GRID, step=28):
    x1, y1, x2, y2 = box
    for x in range(int(x1), int(x2), step):
        draw.line((x, y1, x, y2), fill=color)
    for y in range(int(y1), int(y2), step):
        draw.line((x1, y, x2, y), fill=color)


def load_points(path: Path) -> list[tuple[float, float]]:
    with path.open("r", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        return [(float(row["norm_x"]), float(row["norm_y"])) for row in reader]


def fit_points(points: Iterable[tuple[float, float]], box: tuple[int, int, int, int], padding: int = 16) -> list[tuple[float, float]]:
    x1, y1, x2, y2 = box
    width = max(1, x2 - x1 - padding * 2)
    height = max(1, y2 - y1 - padding * 2)
    return [(x1 + padding + px * width, y1 + padding + py * height) for px, py in points]


def make_sole_mask(size: tuple[int, int], polygon: list[tuple[float, float]]) -> Image.Image:
    mask = Image.new("L", size, 0)
    mask_draw = ImageDraw.Draw(mask)
    mask_draw.polygon(polygon, fill=255)
    return mask


def add_hotspots(base: Image.Image, polygon: list[tuple[float, float]], hotspots: list[tuple[float, float, float, tuple[int, int, int]]]):
    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    for cx, cy, radius, color in hotspots:
        draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), fill=color + (150,))
    overlay = overlay.filter(ImageFilter.GaussianBlur(26))
    mask = make_sole_mask(base.size, polygon)
    clipped = Image.new("RGBA", base.size, (0, 0, 0, 0))
    clipped.alpha_composite(overlay)
    clipped.putalpha(mask)
    base.alpha_composite(clipped)


def draw_sole_panel(
    base: Image.Image,
    box: tuple[int, int, int, int],
    title: str,
    foot_prefix: str,
    accent_rgb: tuple[int, int, int],
) -> None:
    draw = ImageDraw.Draw(base)
    rounded_panel(draw, box, fill=PANEL_ALT, outline=(29, 50, 69), radius=16)

    title_width = draw.textlength(title, font=font(18, True))
    draw.text((box[0] + (box[2] - box[0] - title_width) / 2, box[1] + 12), title, font=font(18, True), fill=(205, 229, 245))

    sole_box = (box[0] + 18, box[1] + 48, box[2] - 18, box[3] - 24)
    draw_grid(draw, sole_box, color=(20, 31, 42), step=32)

    polygon = fit_points(load_points(FOOT_LAYOUT_DIR / f"{foot_prefix}_curve.csv"), sole_box, padding=8)
    dots = fit_points(load_points(FOOT_LAYOUT_DIR / f"{foot_prefix}_dots.csv"), sole_box, padding=8)

    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    layer_draw = ImageDraw.Draw(layer)
    layer_draw.polygon(polygon, fill=(23, 44, 58, 196))
    layer_draw.line(polygon + [polygon[0]], fill=(88, 189, 235, 186), width=5)
    layer_draw.line(polygon + [polygon[0]], fill=(216, 244, 255, 144), width=1)
    layer = layer.filter(ImageFilter.GaussianBlur(0.4))
    base.alpha_composite(layer)

    hotspot_data = [
        (dots[2][0], dots[2][1], 34, (255, 70, 70)),
        (dots[6][0], dots[6][1], 30, (255, 166, 0)),
        (dots[-3][0], dots[-3][1], 28, accent_rgb),
        (dots[-1][0], dots[-1][1], 26, (240, 255, 72)),
    ]
    add_hotspots(base, polygon, hotspot_data)
    draw = ImageDraw.Draw(base)

    for x, y in dots:
        draw.ellipse((x - 3, y - 3, x + 3, y + 3), fill=(226, 247, 255))
        draw.ellipse((x - 8, y - 8, x + 8, y + 8), outline=(87, 189, 235, 55), width=1)


def draw_pressure_workspace(base: Image.Image, box: tuple[int, int, int, int], language: str):
    draw = ImageDraw.Draw(base)
    rounded_panel(draw, box, fill=(12, 17, 24), outline=(37, 58, 77), radius=22)
    title = "Dual-Foot Pressure Visualization" if language == "en" else "双脚压力可视化"
    draw.text((box[0] + 24, box[1] + 18), title, font=font(24, True), fill=(255, 241, 212))

    left_box = (box[0] + 16, box[1] + 56, box[0] + (box[2] - box[0]) // 2 - 10, box[3] - 76)
    right_box = (box[0] + (box[2] - box[0]) // 2 + 10, box[1] + 56, box[2] - 16, box[3] - 76)
    draw_sole_panel(base, left_box, "Left Foot" if language == "en" else "左脚", "leftfoot", (255, 196, 0))
    draw_sole_panel(base, right_box, "Right Foot" if language == "en" else "右脚", "rightfoot", (255, 128, 0))

    ctrl_y = box[3] - 58
    controls = [
        "MAX 1000.0",
        "X 1.00",
        "Y 2.80",
        "viridis",
    ]
    x = box[0] + 22
    for item in controls:
        w = int(ImageDraw.Draw(base).textlength(item, font=font(16, True)) + 40)
        rounded_panel(draw, (x, ctrl_y, x + w, ctrl_y + 34), fill=(14, 23, 35), outline=(43, 74, 102), radius=12)
        draw.text((x + 16, ctrl_y + 8), item, font=font(16, True), fill=TEXT)
        x += w + 10


def draw_preview_tile(base: Image.Image, box, label: str, status: str, subtitle: str):
    draw = ImageDraw.Draw(base)
    rounded_panel(draw, box, fill=PANEL, outline=(37, 58, 77), radius=20)
    x1, y1, x2, y2 = box
    draw.rounded_rectangle((x1 + 16, y1 + 16, x1 + 48, y1 + 48), radius=16, fill=AMBER)
    num_w = draw.textlength(label, font=font(22, True))
    draw.text((x1 + 32 - num_w / 2, y1 + 18), label, font=font(22, True), fill=(8, 18, 24))
    draw.text((x1 + 64, y1 + 18), subtitle, font=font(18, True), fill=TEXT)
    chip_color = TEAL if status == "ONLINE" else AMBER
    draw.rounded_rectangle((x2 - 108, y1 + 16, x2 - 16, y1 + 48), radius=14, fill=chip_color)
    status_w = draw.textlength(status, font=font(15, True))
    draw.text((x2 - 62 - status_w / 2, y1 + 22), status, font=font(15, True), fill=(8, 18, 24))

    canvas = (x1 + 16, y1 + 64, x2 - 16, y2 - 16)
    rounded_panel(draw, canvas, fill=PANEL_ALT, outline=(29, 50, 69), radius=16)
    draw_grid(draw, canvas, color=(17, 29, 39), step=28)

    gradient = Image.new("RGBA", (canvas[2] - canvas[0], canvas[3] - canvas[1]), (0, 0, 0, 0))
    grad_draw = ImageDraw.Draw(gradient)
    for idx in range(gradient.size[1]):
        ratio = idx / max(1, gradient.size[1] - 1)
        color = (
            int(12 + 24 * ratio),
            int(20 + 34 * ratio),
            int(28 + 48 * ratio),
            255,
        )
        grad_draw.line((0, idx, gradient.size[0], idx), fill=color)
    base.alpha_composite(gradient, dest=(canvas[0], canvas[1]))

    overlay = Image.new("RGBA", (canvas[2] - canvas[0], canvas[3] - canvas[1]), (0, 0, 0, 0))
    overlay_draw = ImageDraw.Draw(overlay)
    overlay_draw.ellipse((25, 25, 220, 180), fill=(0, 186, 255, 56))
    overlay_draw.ellipse((160, 90, 320, 240), fill=(255, 92, 92, 80))
    overlay_draw.ellipse((300, 40, 430, 170), fill=(255, 218, 44, 62))
    overlay = overlay.filter(ImageFilter.GaussianBlur(18))
    base.alpha_composite(overlay, dest=(canvas[0], canvas[1]))
    draw = ImageDraw.Draw(base)
    draw.text((canvas[0] + 24, canvas[1] + 20), "1920x1080 | 29.8 FPS", font=font(16), fill=(210, 228, 241))


def generate_overview(language: str):
    title = "ISCamRecorder Main Workspace" if language == "en" else "主界面布局示意"
    toolbar_text = "Subject  Test / Trial  2" if language == "en" else "对象  测试 / 次数  第 2 次"
    image = Image.new("RGBA", (1600, 980), BG + (255,))
    draw = ImageDraw.Draw(image)
    draw.text((60, 34), title, font=font(30, True), fill=TEXT)
    rounded_panel(draw, (40, 80, 1560, 165), fill=(15, 22, 30), outline=(41, 65, 86), radius=22)

    mark = Image.open(BRANDING_DIR / "brand_mark_dark.png").convert("RGBA").resize((44, 44))
    image.alpha_composite(mark, dest=(66, 102))
    app_title = "Four-Camera Synchronous Acquisition System" if language == "en" else "四机位同步采集系统"
    draw.text((128, 104), app_title, font=font(24, True), fill=(255, 244, 228))
    draw.text((650, 112), toolbar_text, font=font(18), fill=TEXT)
    draw_badge(draw, (1146, 104), "Recording (12.6s)" if language == "en" else "录制中（12.6 秒）", RED, size=(192, 38))
    draw_badge(draw, (1352, 104), "Settings" if language == "en" else "系统设置", AMBER, size=(138, 38))

    left = (40, 190, 1120, 710)
    right = (1140, 190, 1560, 710)
    bottom = (40, 730, 1560, 940)
    rounded_panel(draw, left, fill=(10, 15, 22), outline=(37, 58, 77), radius=22)
    rounded_panel(draw, right, fill=(12, 17, 24), outline=(37, 58, 77), radius=22)
    rounded_panel(draw, bottom, fill=(12, 17, 24), outline=(37, 58, 77), radius=22)

    draw_preview_tile(image, (58, 208, 570, 444), "1", "ONLINE", "Cam A | 192.168.1.10" if language == "en" else "相机 A | 192.168.1.10")
    draw_preview_tile(image, (590, 208, 1102, 444), "2", "ONLINE", "Cam B | 192.168.1.11" if language == "en" else "相机 B | 192.168.1.11")
    draw_preview_tile(image, (58, 464, 570, 692), "3", "ONLINE", "Cam C | 192.168.1.12" if language == "en" else "相机 C | 192.168.1.12")
    draw_preview_tile(image, (590, 464, 1102, 692), "4", "ONLINE", "Cam D | 192.168.1.13" if language == "en" else "相机 D | 192.168.1.13")

    draw_pressure_workspace(image, right, language)

    cards = (
        [
            ("Runtime Status", "4 / 4 cameras online\nSession directory ready"),
            ("Memory Monitor", "System  38%\nProcess  1.4 GB\nBuffer  482 MB"),
            ("Pressure Sensors", "Left foot online\nCOM11 | frame 128\nRight foot online"),
        ]
        if language == "en"
        else [
            ("运行状态", "相机在线 4 / 4\n会话目录已就绪"),
            ("内存监控", "系统 38%\n进程 1.4 GB\n缓存 482 MB"),
            ("脚压传感器", "左脚在线\nCOM11 | 最近帧 128\n右脚在线"),
        ]
    )
    for idx, card in enumerate(cards):
        cx1 = 64 + idx * 500
        cx2 = cx1 + 456
        rounded_panel(draw, (cx1, 752, cx2, 916), fill=PANEL, outline=(37, 58, 77), radius=18)
        draw.text((cx1 + 22, 772), card[0], font=font(20, True), fill=(255, 241, 212))
        for line_idx, line in enumerate(card[1].split("\n")):
            draw.text((cx1 + 22, 814 + line_idx * 28), line, font=font(18), fill=TEXT)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    image.convert("RGB").save(OUTPUT_DIR / f"overview-{language}.png", quality=95)


def generate_settings(language: str):
    title = "Settings Preview" if language == "en" else "系统设置示意"
    image = Image.new("RGBA", (1500, 980), BG + (255,))
    draw = ImageDraw.Draw(image)
    draw.text((54, 30), title, font=font(30, True), fill=TEXT)

    rounded_panel(draw, (40, 80, 1460, 930), fill=(14, 21, 29), outline=(41, 65, 86), radius=26)
    tabs = (
        ["Capture", "Mapping", "Industrial Camera Parameters", "Pressure", "Subjects"]
        if language == "en"
        else ["采集参数", "机位映射", "工业相机参数", "脚压传感器", "对象列表"]
    )
    x = 66
    for idx, tab in enumerate(tabs):
        fill = (31, 40, 50) if idx == 2 else (18, 25, 34)
        draw.rounded_rectangle((x, 112, x + 208, 156), radius=14, fill=fill)
        draw.text((x + 22, 123), tab, font=font(18, True), fill=(255, 244, 228))
        x += 220

    helper = (
        "Industrial camera ranges are read from the active device when available."
        if language == "en"
        else "工业相机参数会尽量读取当前在线设备的真实范围，并自动限制可调区间。"
    )
    draw.text((76, 196), helper, font=font(18), fill=MUTED)

    headers = (
        ["Slot", "Bound Device", "Exposure ⓘ", "FPS ⓘ", "Gain ⓘ", "Gamma ⓘ", "Black Level ⓘ"]
        if language == "en"
        else ["编号", "当前绑定设备", "曝光时间 ⓘ", "采集帧率 ⓘ", "增益 ⓘ", "Gamma ⓘ", "黑电平 ⓘ"]
    )
    table_box = (64, 240, 1434, 856)
    rounded_panel(draw, table_box, fill=PANEL, outline=(37, 58, 77), radius=20)

    col_x = [84, 160, 470, 690, 862, 1014, 1166]
    col_w = [54, 280, 180, 150, 130, 130, 180]
    for idx, header in enumerate(headers):
        draw.rounded_rectangle((col_x[idx], 264, col_x[idx] + col_w[idx], 310), radius=10, fill=(26, 36, 48))
        draw.text((col_x[idx] + 12, 278), header, font=font(16, True), fill=(255, 241, 212))

    rows = [
        ["1", "Cam A | SN 179345", "24000.0 us", "40.0 FPS", "17.0 dB", "1.00", "0.0"],
        ["2", "Cam B | SN 179364", "24000.0 us", "40.0 FPS", "17.0 dB", "1.00", "0.0"],
        ["3", "Cam C | SN 179386", "24000.0 us", "40.0 FPS", "17.0 dB", "1.00", "0.0"],
        ["4", "Cam D | SN 179393", "24000.0 us", "40.0 FPS", "17.0 dB", "1.00", "0.0"],
    ]
    if language == "zh":
        rows = [
            ["1", "相机 A | SN 179345", "24000.0 us", "40.0 FPS", "17.0 dB", "1.00", "0.0"],
            ["2", "相机 B | SN 179364", "24000.0 us", "40.0 FPS", "17.0 dB", "1.00", "0.0"],
            ["3", "相机 C | SN 179386", "24000.0 us", "40.0 FPS", "17.0 dB", "1.00", "0.0"],
            ["4", "相机 D | SN 179393", "24000.0 us", "40.0 FPS", "17.0 dB", "1.00", "0.0"],
        ]

    for row_idx, row in enumerate(rows):
        y = 336 + row_idx * 116
        for col_idx, cell in enumerate(row):
            rounded_panel(draw, (col_x[col_idx], y, col_x[col_idx] + col_w[col_idx], y + 78), fill=(13, 19, 27), outline=(31, 45, 58), radius=12)
            draw.text((col_x[col_idx] + 12, y + 24), cell, font=font(16, bold=col_idx <= 1), fill=TEXT if col_idx != 1 else (231, 240, 247))
        note = "Detected range: 5.0 - 50000.0" if language == "en" else "检测范围：5.0 - 50000.0"
        draw.text((470, y + 86), note, font=font(13), fill=MUTED)

    footer = (
        "ⓘ Tooltips in the real application explain parameter effects and FPS / exposure linkage."
        if language == "en"
        else "ⓘ 实际程序中，表头提示会解释参数作用，以及 FPS 与曝光时间的联动关系。"
    )
    draw.text((76, 886), footer, font=font(16), fill=(190, 204, 216))
    image.convert("RGB").save(OUTPUT_DIR / f"settings-{language}.png", quality=95)


def main():
    generate_overview("en")
    generate_overview("zh")
    generate_settings("en")
    generate_settings("zh")


if __name__ == "__main__":
    main()
