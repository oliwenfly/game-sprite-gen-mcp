"""网格图集质检：逐格 alpha 包围盒、尺寸偏差与空帧检测。"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import median

from PIL import Image


@dataclass
class CellReport:
    """单格质检结果。"""

    row: int
    col: int
    bbox: tuple[int, int, int, int] | None
    width: int
    height: int
    center: tuple[float, float]
    empty: bool
    touches_edge: bool

    def as_dict(self) -> dict:
        return {
            "row": self.row,
            "col": self.col,
            "bbox": list(self.bbox) if self.bbox else None,
            "width": self.width,
            "height": self.height,
            "center": [round(self.center[0], 1), round(self.center[1], 1)],
            "empty": self.empty,
            "touches_edge": self.touches_edge,
        }


@dataclass
class GridReport:
    """整张网格图的质检结果。"""

    size: tuple[int, int]
    cell: tuple[int, int]
    cells: list[CellReport]
    empty_cells: int
    width_deviation: float
    height_deviation: float
    full_cells: int = 0

    @property
    def edge_cells(self) -> list[CellReport]:
        return [cell for cell in self.cells if cell.touches_edge]

    def as_dict(self) -> dict:
        return {
            "image_size": list(self.size),
            "cell_size": list(self.cell),
            "empty_cells": self.empty_cells,
            "full_cells": self.full_cells,
            "width_deviation": round(self.width_deviation, 3),
            "height_deviation": round(self.height_deviation, 3),
            "cells": [cell.as_dict() for cell in self.cells],
        }


def _cell_bbox(cell: Image.Image, thresh: int) -> tuple[int, int, int, int] | None:
    """计算单格内容包围盒。"""
    mask = cell.getchannel("A").point(lambda v: 255 if v > thresh else 0)
    return mask.getbbox()


def inspect_grid(
    img: Image.Image,
    cols: int,
    rows: int,
    *,
    cell_w: int | None = None,
    cell_h: int | None = None,
    gutter: int = 0,
    thresh: int = 10,
) -> GridReport:
    """按网格切分并统计每格内容。cell_w/cell_h 省略时按图片尺寸均分。"""
    if cols <= 0 or rows <= 0:
        raise ValueError("cols 与 rows 必须为正整数")
    rgba = img.convert("RGBA")
    cw = cell_w or rgba.width // cols
    ch = cell_h or rgba.height // rows

    cells: list[CellReport] = []
    widths: list[int] = []
    heights: list[int] = []
    empty = 0
    full = 0
    for row in range(rows):
        for col in range(cols):
            x = gutter + col * (cw + gutter)
            y = gutter + row * (ch + gutter)
            patch = rgba.crop((x, y, x + cw, y + ch))
            bbox = _cell_bbox(patch, thresh)
            if bbox is None:
                empty += 1
                cells.append(CellReport(row, col, None, 0, 0, (0.0, 0.0), True, False))
                continue
            left, top, right, bottom = bbox
            width = right - left
            height = bottom - top
            widths.append(width)
            heights.append(height)
            # 内容贴到格子边缘说明角色被切到或没有居中，可能影响对齐。
            touches = left <= 1 or top <= 1 or right >= cw - 1 or bottom >= ch - 1
            # 几乎铺满整格通常意味着背景没去掉，而不是角色真的这么大。
            if width >= cw * 0.98 and height >= ch * 0.98:
                full += 1
            cells.append(
                CellReport(
                    row,
                    col,
                    bbox,
                    width,
                    height,
                    ((left + right) / 2, (top + bottom) / 2),
                    False,
                    touches,
                )
            )

    return GridReport(
        size=rgba.size,
        cell=(cw, ch),
        cells=cells,
        empty_cells=empty,
        width_deviation=_relative_spread(widths),
        height_deviation=_relative_spread(heights),
        full_cells=full,
    )


def _relative_spread(values: list[int]) -> float:
    """用 (max-min)/中位数 描述一组尺寸的离散程度。"""
    if len(values) < 2:
        return 0.0
    mid = median(values) or 1
    return (max(values) - min(values)) / mid


def quality_issues(report: GridReport, max_deviation: float = 0.2) -> list[str]:
    """把质检结果转成人类可读的问题列表，空列表表示通过。"""
    issues: list[str] = []
    if report.empty_cells:
        issues.append(f"有 {report.empty_cells} 个空白格，模型没有画满所有帧")
    if report.full_cells and report.full_cells == len(report.cells):
        issues.append("每格内容都铺满整格，背景可能没有去除")
    if report.width_deviation > max_deviation:
        issues.append(f"帧宽差异 {report.width_deviation:.0%} 超过 {max_deviation:.0%}")
    if report.height_deviation > max_deviation:
        issues.append(f"帧高差异 {report.height_deviation:.0%} 超过 {max_deviation:.0%}")
    return issues
