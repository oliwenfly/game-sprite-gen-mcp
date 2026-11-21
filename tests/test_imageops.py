"""图片后处理测试。"""

from PIL import Image, ImageDraw

from spritegen import imageops


def make_sheet() -> Image.Image:
    """白底上画两个红圆，模拟网格序列帧。"""
    img = Image.new("RGB", (200, 100), "white")
    draw = ImageDraw.Draw(img)
    draw.ellipse((20, 20, 80, 80), fill="red")
    draw.ellipse((120, 20, 180, 80), fill="red")
    return img


def test_remove_solid_background_clears_corners() -> None:
    result = imageops.remove_solid_background(make_sheet())
    assert result.mode == "RGBA"
    assert result.getpixel((0, 0))[3] == 0
    assert result.getpixel((50, 50))[3] == 255


def test_remove_solid_background_keeps_interior_color() -> None:
    """角色内部的白色不该被四角洪水填充误删。"""
    img = Image.new("RGB", (100, 100), "white")
    draw = ImageDraw.Draw(img)
    draw.ellipse((20, 20, 80, 80), fill="red")
    draw.ellipse((45, 45, 55, 55), fill="white")
    result = imageops.remove_solid_background(img)
    assert result.getpixel((50, 50))[3] == 255


def test_remove_solid_background_skips_existing_alpha() -> None:
    img = Image.new("RGBA", (20, 20), (0, 0, 0, 0))
    result = imageops.remove_solid_background(img)
    assert result.getpixel((0, 0))[3] == 0


def test_trim_alpha_removes_margins() -> None:
    result = imageops.trim_alpha(imageops.remove_solid_background(make_sheet()), pad=2)
    assert result is not None
    assert result.width < 200
    assert result.height < 100


def test_make_seamless_keeps_size() -> None:
    result = imageops.make_seamless(make_sheet(), out_size=(400, 200), top=20)
    assert result.size == (400, 200)
