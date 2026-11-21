"""序列帧图集构建：拆格、统一缩放、锚点对齐、锁轮廓、调色板、合图与导出。"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageChops, ImageFilter, ImageStat

from .imageops import flatten


@dataclass
class BuildOptions:
    """图集构建参数。"""

    cols: int = 2
    rows: int = 2
    anchor: str = "bottom"
    target_h: int = 0
    pad: int = 0
    thresh: int = 10
    lock_outline: bool = True
    feather: int = 3
    palette: int = 0
    fps: int = 10
    loop: bool = True
    gutter: int = 4
    raw: bool = False


@dataclass
class BuildResult:
    """构建产物与验收指标。"""

    frame_count: int
    frame_size: tuple[int, int]
    pivot: tuple[int, int]
    spritesheet: Path
    manifest: Path
    frames: list[Path] = field(default_factory=list)
    contact: Path | None = None
    gif: Path | None = None
    metrics: dict = field(default_factory=dict)


def split_grid(img: Image.Image, cols: int, rows: int) -> tuple[list[Image.Image], tuple[int, int]]:
    """把网格图按 cols×rows 拆成单帧，返回帧列表与单格尺寸。"""
    if cols <= 0 or rows <= 0:
        raise ValueError("cols 与 rows 必须为正整数")
    rgba = img.convert("RGBA")
    cw = rgba.width // cols
    ch = rgba.height // rows
    if cw == 0 or ch == 0:
        raise ValueError(f"图片 {rgba.size} 太小，无法切分为 {cols}×{rows} 网格")
    frames = [
        rgba.crop((col * cw, row * ch, (col + 1) * cw, (row + 1) * ch))
        for row in range(rows)
        for col in range(cols)
    ]
    return frames, (cw, ch)


class SheetBuilder:
    """负责从单帧列表生成对齐后的瓦片图、单帧、联系图、GIF 与 manifest。"""

    def __init__(self, options: BuildOptions | None = None) -> None:
        self.opts = options or BuildOptions()
        self.frames: list[Image.Image] = []
        self.bboxes: list[tuple[int, int, int, int]] = []
        self.canvas_w = 0
        self.canvas_h = 0
        self.pivot = (0, 0)

    # ---------- 处理步骤 ----------

    def compute_bboxes(self) -> None:
        """计算每帧的 alpha 包围盒。"""
        self.bboxes = []
        for frame in self.frames:
            mask = frame.getchannel("A").point(lambda v: 255 if v > self.opts.thresh else 0)
            self.bboxes.append(mask.getbbox() or (0, 0, frame.width, frame.height))

    def uniform_scale(self) -> float:
        """用所有帧最大包围盒高度推导统一缩放比，保证帧间比例一致。"""
        if not self.frames:
            return 1.0
        max_h = max(bottom - top for _, top, _, bottom in self.bboxes) or 1
        scale = self.opts.target_h / max_h if self.opts.target_h > 0 else 1.0
        if scale != 1.0:
            self.frames = [
                frame.resize(
                    (max(1, round(frame.width * scale)), max(1, round(frame.height * scale))),
                    Image.LANCZOS,
                )
                for frame in self.frames
            ]
            self.bboxes = [tuple(round(v * scale) for v in box) for box in self.bboxes]  # type: ignore[misc]
        return scale

    def align_anchor(self) -> None:
        """把每帧放入统一画布：bottom 模式脚底基线对齐，center 模式包围盒中心对齐。"""
        if not self.frames:
            return
        self.canvas_w = max(frame.width for frame in self.frames) + self.opts.pad * 2
        self.canvas_h = max(frame.height for frame in self.frames) + self.opts.pad * 2
        aligned = []
        for frame, bbox in zip(self.frames, self.bboxes):
            if self.opts.anchor == "bottom":
                cx = self._foot_center_x(frame, bbox)
                ay = bbox[3]
                canvas_ay = self.canvas_h - self.opts.pad
            else:
                cx = (bbox[0] + bbox[2]) * 0.5
                ay = (bbox[1] + bbox[3]) * 0.5
                canvas_ay = self.canvas_h * 0.5
            dx = round(self.canvas_w * 0.5 - cx)
            dy = round(canvas_ay - ay)
            canvas = Image.new("RGBA", (self.canvas_w, self.canvas_h), (0, 0, 0, 0))
            canvas.paste(frame, (dx, dy), frame)
            aligned.append(canvas)
        self.frames = aligned
        if self.opts.anchor == "bottom":
            self.pivot = (round(self.canvas_w * 0.5), self.canvas_h - self.opts.pad)
        else:
            self.pivot = (round(self.canvas_w * 0.5), round(self.canvas_h * 0.5))

    def _foot_center_x(self, img: Image.Image, bbox: tuple[int, int, int, int]) -> float:
        """取底部 15% 区域非透明像素的 x 中点作为脚底中心，比包围盒中心稳定。"""
        pixels = img.load()
        bottom = min(bbox[3], img.height - 1)
        top = int(bottom * 0.85)
        left = max(0, bbox[0])
        right = min(bbox[2], img.width - 1)
        xs = [
            x
            for y in range(top, bottom + 1)
            for x in range(left, right + 1)
            if pixels[x, y][3] > self.opts.thresh
        ]
        if not xs:
            return (bbox[0] + bbox[2]) * 0.5
        return (min(xs) + max(xs)) * 0.5

    def apply_lock_outline(self) -> None:
        """以第 1 帧的 alpha 为基准蒙版，把边缘区域回填第 1 帧颜色，抑制轮廓抖动。"""
        if not self.opts.lock_outline or len(self.frames) < 2:
            return
        base = self.frames[0]
        base_alpha = base.getchannel("A").point(lambda v: 255 if v > self.opts.thresh else 0)
        dilated = base_alpha.filter(ImageFilter.MaxFilter(self.opts.feather * 2 + 1))
        edge = ImageChops.subtract(dilated, base_alpha)
        base_rgb = base.convert("RGB")
        locked = [base]
        for frame in self.frames[1:]:
            out = frame.copy()
            out_rgb = out.convert("RGB")
            out_rgb.paste(base_rgb, None, edge)
            merged_alpha = Image.composite(out.getchannel("A"), base.getchannel("A"), edge)
            locked.append(Image.merge("RGBA", (*out_rgb.split(), merged_alpha)))
        self.frames = locked

    def quantize_palette(self) -> None:
        """把所有帧量化到同一套 N 色调色板。"""
        if self.opts.palette <= 0 or not self.frames:
            return
        combined = Image.new("RGB", (self.frames[0].width, self.frames[0].height * len(self.frames)))
        for index, frame in enumerate(self.frames):
            combined.paste(frame.convert("RGB"), (0, index * frame.height))
        quantized = combined.quantize(colors=self.opts.palette, method=Image.Quantize.MEDIANCUT)
        result = []
        for frame in self.frames:
            rgba = frame.convert("RGB").quantize(palette=quantized).convert("RGBA")
            rgba.putalpha(frame.getchannel("A"))
            result.append(rgba)
        self.frames = result

    # ---------- 产物 ----------

    def build_spritesheet(self) -> Image.Image:
        """横向拼接为瓦片图，帧间留 gutter 透明间隔。"""
        n = len(self.frames)
        width = self.canvas_w * n + self.opts.gutter * (n + 1)
        height = self.canvas_h + self.opts.gutter * 2
        sheet = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        for index, frame in enumerate(self.frames):
            bled = self._bleed_edges(frame, radius=2)
            x = self.opts.gutter + index * (self.canvas_w + self.opts.gutter)
            layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
            layer.paste(bled, (x, self.opts.gutter))
            sheet = Image.alpha_composite(sheet, layer)
        return sheet

    def _bleed_edges(self, img: Image.Image, radius: int = 2) -> Image.Image:
        """把不透明像素颜色向外扩 radius 像素，防止缩放过滤出现黑边。"""
        result = img.copy()
        rgb = img.convert("RGB")
        alpha = img.getchannel("A")
        for _ in range(radius):
            expanded = Image.new("RGBA", img.size, (0, 0, 0, 0))
            for dx, dy in ((0, 0), (1, 0), (-1, 0), (0, 1), (0, -1)):
                layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
                layer.paste(rgb, (dx, dy))
                layer.putalpha(alpha)
                expanded = Image.alpha_composite(expanded, layer)
            transparent = result.getchannel("A").point(lambda v: 0 if v > 0 else 255)
            result = Image.composite(expanded, result, transparent)
        return result

    def build_contact(self) -> Image.Image | None:
        """按 4 列排列所有帧，便于目视检查一致性。"""
        if not self.frames:
            return None
        cols = min(4, len(self.frames))
        rows = math.ceil(len(self.frames) / cols)
        contact = Image.new("RGBA", (self.canvas_w * cols, self.canvas_h * rows), (0, 0, 0, 0))
        for index, frame in enumerate(self.frames):
            row, col = divmod(index, cols)
            contact.paste(frame, (col * self.canvas_w, row * self.canvas_h), frame)
        return contact

    def metrics(self) -> dict:
        """计算帧尺寸范围与首末帧差异，作为验收指标。"""
        if len(self.frames) < 2:
            return {}
        widths = [right - left for left, _, right, _ in self.bboxes]
        heights = [bottom - top for _, top, _, bottom in self.bboxes]
        diff = ImageChops.difference(self.frames[0].convert("RGB"), self.frames[-1].convert("RGB"))
        edge_diff = sum(ImageStat.Stat(diff).mean) / 3.0
        return {
            "bbox_width": [min(widths), max(widths)],
            "bbox_height": [min(heights), max(heights)],
            "first_last_diff": round(edge_diff, 2),
        }

    def _manifest(self, name: str, sheet: Image.Image) -> dict:
        """生成与引擎无关的图集描述。"""
        frames = [
            {
                "index": index,
                "x": self.opts.gutter + index * (self.canvas_w + self.opts.gutter),
                "y": self.opts.gutter,
                "w": self.canvas_w,
                "h": self.canvas_h,
            }
            for index in range(len(self.frames))
        ]
        return {
            "name": name,
            "image": "spritesheet.png",
            "frame_count": len(self.frames),
            "frame_width": self.canvas_w,
            "frame_height": self.canvas_h,
            "image_width": sheet.width,
            "image_height": sheet.height,
            "gutter": self.opts.gutter,
            "pivot": {"x": self.pivot[0], "y": self.pivot[1]},
            "fps": self.opts.fps,
            "loop": self.opts.loop,
            "anchor": self.opts.anchor,
            "frames": frames,
        }

    def _save_gif(self, path: Path) -> None:
        """把帧序列导出为 GIF 预览，透明区域压在白色底上。"""
        if not self.frames:
            return
        duration = max(20, round(1000 / max(1, self.opts.fps)))
        images = [flatten(frame) for frame in self.frames]
        options: dict = {
            "save_all": True,
            "append_images": images[1:],
            "duration": duration,
            "disposal": 2,
            "optimize": False,
        }
        if self.opts.loop:
            options["loop"] = 0
        images[0].save(path, **options)

    # ---------- 入口 ----------

    def build(
        self,
        frames: list[Image.Image],
        out_dir: str | Path,
        name: str,
        *,
        gif: bool = True,
        contact: bool = True,
    ) -> BuildResult:
        """执行完整构建流程并写出所有产物。raw 模式只切图合图，不做任何处理。"""
        if not frames:
            raise ValueError("没有可用的帧")
        self.frames = [frame.convert("RGBA") for frame in frames]

        if self.opts.raw:
            self.canvas_w, self.canvas_h = self.frames[0].size
            self.pivot = (self.canvas_w // 2, self.canvas_h)
        else:
            self.compute_bboxes()
            self.uniform_scale()
            self.align_anchor()
            self.apply_lock_outline()
            self.quantize_palette()

        sheet = self.build_spritesheet()
        contact_img = self.build_contact() if contact else None
        metrics = self.metrics() if not self.opts.raw else {}

        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        frame_paths = []
        for index, frame in enumerate(self.frames):
            path = out / f"frame-{index}.png"
            frame.save(path)
            frame_paths.append(path)
        sheet_path = out / "spritesheet.png"
        sheet.save(sheet_path)

        contact_path = None
        if contact_img is not None:
            contact_path = out / "contact.png"
            contact_img.save(contact_path)

        gif_path = None
        if gif:
            gif_path = out / "preview.gif"
            self._save_gif(gif_path)

        manifest_path = out / "manifest.json"
        manifest_path.write_text(
            json.dumps(self._manifest(name, sheet), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        return BuildResult(
            frame_count=len(self.frames),
            frame_size=(self.canvas_w, self.canvas_h),
            pivot=self.pivot,
            spritesheet=sheet_path,
            manifest=manifest_path,
            frames=frame_paths,
            contact=contact_path,
            gif=gif_path,
            metrics=metrics,
        )
