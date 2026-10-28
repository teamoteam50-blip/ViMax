"""片头/片尾字卡（PIL 绘制）：近黑底、米白字、细分割线，与正片调色衔接。"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def _font(font_path: str | Path, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(font_path), size)


def make_card(out_path: str | Path, *, width: int, height: int, font_path: str | Path,
              title: str, subtitle: str = "", title_size: int = 120, subtitle_size: int = 40) -> Path:
    img = Image.new("RGB", (width, height), (7, 10, 14))
    draw = ImageDraw.Draw(img)
    # 极弱的中央暖色光晕，呼应灯塔光束
    glow = Image.new("RGB", (width, height), (0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse([width / 2 - 520, height / 2 - 300, width / 2 + 520, height / 2 + 300], fill=(28, 20, 8))
    from PIL import ImageFilter
    img = Image.blend(img, Image.blend(img, glow.filter(ImageFilter.GaussianBlur(180)), 0.9), 0.85)
    draw = ImageDraw.Draw(img)

    tf = _font(font_path, title_size)
    tb = draw.textbbox((0, 0), title, font=tf)
    tx, ty = (width - (tb[2] - tb[0])) / 2 - tb[0], height / 2 - (tb[3] - tb[1]) / 2 - tb[1] - (30 if subtitle else 0)
    draw.text((tx, ty), title, font=tf, fill=(240, 233, 218))

    line_y = height / 2 + (70 if subtitle else 96)
    draw.line([width / 2 - 120, line_y, width / 2 + 120, line_y], fill=(176, 141, 87), width=2)

    if subtitle:
        sf = _font(font_path, subtitle_size)
        sb = draw.textbbox((0, 0), subtitle, font=sf)
        draw.text(((width - (sb[2] - sb[0])) / 2 - sb[0], line_y + 34), subtitle, font=sf, fill=(188, 192, 196))

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, quality=95)
    return out
