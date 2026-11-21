"""网格质检测试。"""

from PIL import Image, ImageDraw

from spritegen.inspect import inspect_grid, quality_issues


def test_inspect_grid_finds_empty_cell() -> None:
    img = Image.new("RGBA", (200, 100), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse((20, 20, 80, 80), fill=(255, 0, 0, 255))
    report = inspect_grid(img, cols=2, rows=1)

    assert report.empty_cells == 1
    assert [(cell.empty, cell.width) for cell in report.cells] == [(False, 61), (True, 0)]
    assert quality_issues(report)


def test_inspect_grid_reports_size_deviation() -> None:
    img = Image.new("RGBA", (200, 100), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse((10, 10, 90, 90), fill=(255, 0, 0, 255))
    draw.rectangle((110, 30, 130, 70), fill=(255, 0, 0, 255))
    report = inspect_grid(img, cols=2, rows=1)

    assert report.empty_cells == 0
    assert report.width_deviation > 0.1
    assert any("帧宽差异" in issue for issue in quality_issues(report, max_deviation=0.1))
