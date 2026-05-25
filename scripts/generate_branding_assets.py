from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont


ROOT = Path(__file__).resolve().parent.parent
BRANDING_DIR = ROOT / "python_app" / "assets" / "branding"
TITLE = "四机位同步采集系统"


VARIANTS = {
    "dark": {
        "plate_fill": (11, 18, 24, 248),
        "plate_stroke": (102, 216, 255, 255),
        "glow": (102, 216, 255, 72),
        "signal": (247, 182, 90, 255),
        "node": (63, 216, 181, 236),
        "text": (244, 247, 251, 255),
        "muted": (150, 168, 184, 255),
    },
    "light": {
        "plate_fill": (255, 255, 255, 250),
        "plate_stroke": (20, 33, 53, 255),
        "glow": (102, 216, 255, 32),
        "signal": (214, 139, 39, 255),
        "node": (29, 129, 109, 230),
        "text": (21, 34, 55, 255),
        "muted": (89, 105, 123, 255),
    },
}


def load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        ("C:/Windows/Fonts/msyhbd.ttc", True),
        ("C:/Windows/Fonts/msyh.ttc", False),
        ("C:/Windows/Fonts/simhei.ttf", True),
        ("C:/Windows/Fonts/simsun.ttc", False),
    ]
    ordered = sorted(candidates, key=lambda item: item[1] != bold)
    for path, _ in ordered:
        if Path(path).exists():
            return ImageFont.truetype(path, size=size)
    return ImageFont.load_default()


def draw_mark(size: int, variant: str) -> Image.Image:
    colors = VARIANTS[variant]
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    glow_layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow_layer)
    plate_box = (int(size * 0.10), int(size * 0.10), int(size * 0.90), int(size * 0.90))
    radius = int(size * 0.20)
    glow_draw.rounded_rectangle(plate_box, radius=radius, outline=colors["glow"], width=max(8, size // 18))
    glow_layer = glow_layer.filter(ImageFilter.GaussianBlur(size * 0.018))
    image.alpha_composite(glow_layer)

    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle(
        plate_box,
        radius=radius,
        fill=colors["plate_fill"],
        outline=colors["plate_stroke"],
        width=max(4, size // 36),
    )

    ring_box = (int(size * 0.29), int(size * 0.29), int(size * 0.71), int(size * 0.71))
    inner_ring_box = (int(size * 0.38), int(size * 0.38), int(size * 0.62), int(size * 0.62))
    draw.ellipse(ring_box, outline=colors["plate_stroke"], width=max(4, size // 38))
    draw.ellipse(inner_ring_box, outline=colors["text"], width=max(4, size // 42))

    node_size = int(size * 0.11)
    nodes = [
        (int(size * 0.20), int(size * 0.24)),
        (int(size * 0.69), int(size * 0.24)),
        (int(size * 0.20), int(size * 0.65)),
        (int(size * 0.69), int(size * 0.65)),
    ]
    for left, top in nodes:
        draw.rounded_rectangle(
            (left, top, left + node_size, top + node_size),
            radius=int(node_size * 0.32),
            fill=colors["node"],
        )

    pulse = [
        (int(size * 0.17), int(size * 0.56)),
        (int(size * 0.32), int(size * 0.56)),
        (int(size * 0.40), int(size * 0.47)),
        (int(size * 0.49), int(size * 0.66)),
        (int(size * 0.57), int(size * 0.40)),
        (int(size * 0.69), int(size * 0.56)),
        (int(size * 0.83), int(size * 0.56)),
    ]
    draw.line(pulse, fill=colors["signal"], width=max(8, size // 46), joint="curve")

    for x1, y1, x2, y2 in (
        (0.18, 0.18, 0.31, 0.18),
        (0.18, 0.18, 0.18, 0.31),
        (0.69, 0.18, 0.82, 0.18),
        (0.82, 0.18, 0.82, 0.31),
        (0.18, 0.82, 0.18, 0.69),
        (0.18, 0.82, 0.31, 0.82),
        (0.82, 0.82, 0.69, 0.82),
        (0.82, 0.82, 0.82, 0.69),
    ):
        draw.line(
            (int(size * x1), int(size * y1), int(size * x2), int(size * y2)),
            fill=colors["signal"],
            width=max(4, size // 64),
        )
    return image


def draw_wordmark(variant: str) -> Image.Image:
    colors = VARIANTS[variant]
    canvas = Image.new("RGBA", (1680, 460), (0, 0, 0, 0))
    canvas.alpha_composite(draw_mark(280, variant), (60, 90))
    draw = ImageDraw.Draw(canvas)
    title_font = load_font(124, bold=True)
    subtitle_font = load_font(38, bold=False)
    draw.text((390, 120), TITLE, font=title_font, fill=colors["text"])
    draw.line((392, 280, 1080, 280), fill=colors["signal"], width=8)
    draw.text((390, 310), "四路视觉采集 / 串口联动 / 缓存监控", font=subtitle_font, fill=colors["muted"])
    return canvas


def save_assets() -> None:
    BRANDING_DIR.mkdir(parents=True, exist_ok=True)
    dark_mark = draw_mark(1024, "dark")
    light_mark = draw_mark(1024, "light")
    dark_mark.save(BRANDING_DIR / "brand_mark_dark.png")
    light_mark.save(BRANDING_DIR / "brand_mark_light.png")
    draw_wordmark("dark").save(BRANDING_DIR / "wordmark_dark.png")
    draw_wordmark("light").save(BRANDING_DIR / "wordmark_light.png")
    dark_mark.save(
        BRANDING_DIR / "app_icon.ico",
        sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)],
    )


if __name__ == "__main__":
    save_assets()

