"""图片后处理：纯色背景去除、alpha 裁边、左右无缝拼接。"""

from __future__ import annotations

from PIL import Image, ImageChops, ImageDraw

MARKER = (255, 0, 255)


def has_alpha(img: Image.Image) -> bool:
    """判断图片是否真的带有透明像素。"""
    if img.mode not in ("RGBA", "LA"):
        return False
    low, _ = img.getchannel("A").getextrema()
    return low < 255


def remove_solid_background(img: Image.Image, tolerance: int = 18) -> Image.Image:
    """用四角色键去除纯色背景，返回 RGBA 图像。

    从四个角分别做容差洪水填充，只清除与边缘连通的区域，
    因此角色内部的同色像素（例如白色眼白）不会被误删。
    输入已有透明通道时原样返回。
    """
    rgba = img.convert("RGBA")
    if has_alpha(rgba):
        return rgba

    rgb = rgba.convert("RGB")
    corners = [
        (0, 0),
        (rgb.width - 1, 0),
        (0, rgb.height - 1),
        (rgb.width - 1, rgb.height - 1),
    ]
    for xy in corners:
        if rgb.getpixel(xy) == MARKER:
            continue
        ImageDraw.floodfill(rgb, xy, MARKER, thresh=tolerance)

    marker = Image.new("RGB", rgb.size, MARKER)
    alpha = ImageChops.difference(rgb, marker).convert("L").point(lambda v: 0 if v == 0 else 255)
    rgba.putalpha(alpha)
    return rgba


def trim_alpha(img: Image.Image, pad: int = 0, max_side: int = 0, thresh: int = 0) -> Image.Image | None:
    """按 alpha 裁掉空白边界，可选留边与限制最大边长。没有不透明像素时返回 None。"""
    rgba = img.convert("RGBA")
    mask = rgba.getchannel("A").point(lambda v: 255 if v > thresh else 0)
    bbox = mask.getbbox()
    if not bbox:
        return None
    left = max(0, bbox[0] - pad)
    top = max(0, bbox[1] - pad)
    right = min(rgba.width, bbox[2] + pad)
    bottom = min(rgba.height, bbox[3] + pad)
    out = rgba.crop((left, top, right, bottom))
    if max_side and max(out.size) > max_side:
        scale = max_side / max(out.size)
        out = out.resize((max(1, round(out.width * scale)), max(1, round(out.height * scale))), Image.LANCZOS)
    return out


def make_seamless(img: Image.Image, out_size: tuple[int, int] | None = None, top: int = 0) -> Image.Image:
    """左半部分与其镜像拼接成左右可平铺的图，可选缩放到目标尺寸并在顶部留空。"""
    rgba = img.convert("RGBA")
    w, h = rgba.size
    half = rgba.crop((0, 0, w // 2, h))
    mirrored = Image.new("RGBA", (w, h))
    mirrored.paste(half, (0, 0))
    mirrored.paste(half.transpose(Image.FLIP_LEFT_RIGHT), (w // 2, 0))

    if not out_size:
        return mirrored
    out_w, out_h = out_size
    body = mirrored.resize((out_w, max(1, out_h - top)), Image.LANCZOS)
    canvas = Image.new("RGBA", (out_w, out_h), (0, 0, 0, 0))
    canvas.paste(body, (0, top))
    return canvas


def flatten(img: Image.Image, color: tuple[int, int, int, int] = (255, 255, 255, 255)) -> Image.Image:
    """把透明图像压到纯色底上，返回 RGB，供 GIF 等不支持 alpha 的格式使用。"""
    canvas = Image.new("RGBA", img.size, color)
    canvas.alpha_composite(img.convert("RGBA"))
    return canvas.convert("RGB")
