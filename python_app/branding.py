from pathlib import Path

from .config import app_root


BRANDING_DIR = app_root() / "python_app" / "assets" / "branding"
APP_ICON_PATH = BRANDING_DIR / "app_icon.ico"
MARK_DARK_PATH = BRANDING_DIR / "brand_mark_dark.png"
MARK_LIGHT_PATH = BRANDING_DIR / "brand_mark_light.png"
WORDMARK_DARK_PATH = BRANDING_DIR / "wordmark_dark.png"
WORDMARK_LIGHT_PATH = BRANDING_DIR / "wordmark_light.png"
PROMPT_SPEC_PATH = BRANDING_DIR / "logo_prompt.md"
