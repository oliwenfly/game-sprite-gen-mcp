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
    thresh: int = 32
    lock_outline: bool = False
    feather: int = 3
    palette: int = 0
    fps: int = 10
    loop: bool = True
    gutter: int = 4
    raw: bool = False
    # frame：每帧按自身稳健身高归一化；global：所有帧同一系数；none：不缩放。
    scale: str = "frame"
    # 每帧缩放系数的钳制范围，避免把真实的下蹲/跳跃强行拉平。
    scale_min: float = 0.9
    scale_max: float = 1.1
    # 是否用掩码互相关做精修（比纯 bbox 锚点稳得多）。
    register: bool = True
    # 时序平滑强度：0 关闭，1 只滤单帧跳变，2 更强。
    smooth: int = 1
    # 是否额外导出 overlay.png（所有帧轮廓叠加），用于目视检查残余偏移。
    overlay: bool = True
    # 可选基准帧：edit_animation 传入参考图，让所有帧对齐到它而不是第 1 帧。
    reference_frame: Image.Image | None = None


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
    overlay: Path | None = None
    metrics: dict = field(default_factory=dict)
    alignment: dict = field(default_factory=dict)


def _hsv_to_rgb(h: float, s: float, v: float) -> tuple[int, int, int]:
    """极简 HSV 转 RGB，只用于给叠加图分配可区分的颜色。"""
    i = int(h * 6) % 6
    f = h * 6 - int(h * 6)
    p = round(255 * v * (1 - s))
    q = round(255 * v * (1 - f * s))
    t = round(255 * v * (1 - (1 - f) * s))
    full = round(255 * v)
    return [
        (full, t, p),
        (q, full, p),
        (p, full, t),
        (p, q, full),
        (t, p, full),
        (full, p, q),
    ][i]


def _alpha_mask(img: Image.Image, thresh: int) -> Image.Image:
    """把 alpha 二值化成 0/255 掩码，阈值之上才算实体像素。"""
    return img.getchannel("A").point(lambda v: 255 if v > thresh else 0)


def _largest_component_bbox(mask: Image.Image) -> tuple[int, int, int, int] | None:
    """取 alpha 最大连通域的包围盒，丢掉悬浮碎屑和分离的投影。

    PIL 没有连通域标记，这里用腐蚀去掉细碎噪点后再取 bbox：
    碎屑和投影通常在形态学开运算后消失，主体轮廓保留。
    """
    if mask.getbbox() is None:
        return None
    # 开运算（先腐蚀后膨胀）去掉小连通域，核大小按图像尺寸自适应。
    radius = max(1, round(min(mask.size) / 128))
    size = radius * 2 + 1
    opened = mask.filter(ImageFilter.MinFilter(size)).filter(ImageFilter.MaxFilter(size))
    bbox = opened.getbbox()
    # 开运算把主体也吃掉了（例如角色本身很细），退回原始 bbox。
    return bbox or mask.getbbox()


def split_grid(
    img: Image.Image,
    cols: int,
    rows: int,
    *,
    thresh: int = 32,
    adaptive: bool = True,
) -> tuple[list[Image.Image], tuple[int, int]]:
    """把网格图按 cols×rows 拆成单帧，返回帧列表与单格尺寸。

    adaptive 为真时按每格内容的实际包围盒重新定界，避免模型没有严格居中
    导致角色被格子边界切掉；格间留出原图等分得到的固定步长。
    """
    if cols <= 0 or rows <= 0:
        raise ValueError("cols 与 rows 必须为正整数")
    rgba = img.convert("RGBA")
    cw = rgba.width // cols
    ch = rgba.height // rows
    if cw == 0 or ch == 0:
        raise ValueError(f"图片 {rgba.size} 太小，无法切分为 {cols}×{rows} 网格")

    if not adaptive:
        frames = [
            rgba.crop((col * cw, row * ch, (col + 1) * cw, (row + 1) * ch))
            for row in range(rows)
            for col in range(cols)
        ]
        return frames, (cw, ch)

    # 自适应：先按格子取内容包围盒，再把所有格子的包围盒对齐到统一尺寸。
    boxes: list[tuple[int, int, int, int] | None] = []
    for row in range(rows):
        for col in range(cols):
            cell = rgba.crop((col * cw, row * ch, (col + 1) * cw, (row + 1) * ch))
            boxes.append(_largest_component_bbox(_alpha_mask(cell, thresh)))

    valid = [box for box in boxes if box]
    if not valid:
        # 整张图都没有实体像素，退回等分切法，让后续质检报告空帧。
        frames = [
            rgba.crop((col * cw, row * ch, (col + 1) * cw, (row + 1) * ch))
            for row in range(rows)
            for col in range(cols)
        ]
        return frames, (cw, ch)

    # 每格向外扩一点余量，保证不切到抗锯齿边缘；再统一到同一尺寸。
    margin = max(2, round(min(cw, ch) * 0.04))
    widths = [min(cw, (b[2] - b[0]) + margin * 2) for b in valid]
    heights = [min(ch, (b[3] - b[1]) + margin * 2) for b in valid]
    out_w = max(widths)
    out_h = max(heights)

    frames = []
    for index, box in enumerate(boxes):
        row, col = divmod(index, cols)
        origin_x, origin_y = col * cw, row * ch
        if box is None:
            frames.append(Image.new("RGBA", (out_w, out_h), (0, 0, 0, 0)))
            continue
        left, top, right, bottom = box
        cx, cy = (left + right) / 2 + origin_x, (top + bottom) / 2 + origin_y
        # 以内容中心为中心开一个 out_w×out_h 的窗口，允许跨出原格子边界。
        x0 = round(cx - out_w / 2)
        y0 = round(cy - out_h / 2)
        patch = Image.new("RGBA", (out_w, out_h), (0, 0, 0, 0))
        patch.paste(rgba.crop((x0, y0, x0 + out_w, y0 + out_h)), (0, 0))
        frames.append(patch)
    return frames, (out_w, out_h)


class SheetBuilder:
    """负责从单帧列表生成对齐后的瓦片图、单帧、联系图、GIF 与 manifest。"""

    def __init__(self, options: BuildOptions | None = None) -> None:
        self.opts = options or BuildOptions()
        self.frames: list[Image.Image] = []
        self.bboxes: list[tuple[int, int, int, int]] = []
        self.canvas_w = 0
        self.canvas_h = 0
        self.pivot = (0, 0)
        # 每帧实际使用的缩放系数与对齐报告，供 manifest 与返回值使用。
        self.scale_factors: list[float] = []
        self.alignment: dict = {}

    # ---------- 处理步骤 ----------

    def compute_bboxes(self) -> None:
        """计算每帧的稳健包围盒（最大连通域），忽略悬浮碎屑与分离投影。"""
        self.bboxes = []
        for frame in self.frames:
            mask = _alpha_mask(frame, self.opts.thresh)
            box = _largest_component_bbox(mask) or (0, 0, frame.width, frame.height)
            self.bboxes.append(box)
        self.scale_factors = [1.0] * len(self.frames)

    def uniform_scale(self) -> None:
        """按 scale 策略归一化每帧尺寸，消除模型画大画小的帧间差异。

        - frame：每帧按自身稳健身高缩放到同一目标身高，系数钳制在
          [scale_min, scale_max]，避免把真实的下蹲/跳跃强行拉平；
        - global：所有帧用同一个系数，保持真实动作幅度；
        - none：不缩放。
        """
        if not self.frames:
            return
        heights = [bottom - top for _, top, _, bottom in self.bboxes]
        if not heights or max(heights) <= 0:
            return

        # 目标身高：用户显式给了 target_h 就用它，否则取各帧身高中位数。
        target = self.opts.target_h if self.opts.target_h > 0 else float(sorted(heights)[len(heights) // 2])

        if self.opts.scale == "none":
            return

        if self.opts.scale == "global":
            # 单一系数，保证帧间比例与真实动作幅度都不变。
            factor = target / max(heights) if max(heights) else 1.0
            factors = [factor] * len(self.frames)
        else:
            # frame：每帧独立归一化，再用钳制区间挡住姿势导致的高度变化。
            factors = []
            for height in heights:
                raw = target / height if height else 1.0
                factors.append(min(self.opts.scale_max, max(self.opts.scale_min, raw)))

        self.scale_factors = factors
        if all(abs(f - 1.0) < 1e-6 for f in factors):
            return

        scaled = []
        scaled_boxes = []
        for frame, box, factor in zip(self.frames, self.bboxes, factors):
            if abs(factor - 1.0) < 1e-6:
                scaled.append(frame)
                scaled_boxes.append(box)
                continue
            size = (max(1, round(frame.width * factor)), max(1, round(frame.height * factor)))
            scaled.append(frame.resize(size, Image.LANCZOS))
            scaled_boxes.append(tuple(round(v * factor) for v in box))
        self.frames = scaled
        self.bboxes = scaled_boxes  # type: ignore[assignment]

    def _ground_line(self, mask: Image.Image, bbox: tuple[int, int, int, int]) -> int:
        """稳健接地线：取底部满足最小像素密度的最低行，忽略零星噪点。

        直接用 bbox 底边会把一粒灰尘、投影尖或尾巴尖当成脚底，
        导致整帧被抬高。这里要求该行实体像素数达到宽度的 5%（至少 2 个）。
        """
        width = max(1, bbox[2] - bbox[0])
        need = max(2, round(width * 0.05))
        pixels = mask.load()
        left, right = max(0, bbox[0]), min(mask.width - 1, bbox[2] - 1)
        bottom = min(mask.height - 1, bbox[3] - 1)
        for y in range(bottom, max(-1, bbox[1] - 1), -1):
            count = 0
            for x in range(left, right + 1):
                if pixels[x, y]:
                    count += 1
                    if count >= need:
                        return y
        return bottom

    def _centroid_x(self, mask: Image.Image, bbox: tuple[int, int, int, int]) -> float:
        """水平质心：用整块实体像素的 x 质心，对腿部姿势不敏感。

        比“底部条带 min/max 中点”稳，因为后者在双腿分开程度每帧不同时会左右漂。
        """
        total = 0
        weighted = 0
        pixels = mask.load()
        for y in range(max(0, bbox[1]), min(mask.height, bbox[3])):
            for x in range(max(0, bbox[0]), min(mask.width, bbox[2])):
                if pixels[x, y]:
                    total += 1
                    weighted += x
        if not total:
            return (bbox[0] + bbox[2]) * 0.5
        return weighted / total

    def _translate(self, frame: Image.Image, dx: float, dy: float) -> Image.Image:
        """把画面内容整体平移 (dx, dy) 像素，用仿射变换保留亚像素精度。

        仿射矩阵 (1, 0, -dx, 0, 1, -dy) 表示输出像素取输入 (x-dx, y-dy)，
        等价于把内容移动到 +dx/+dy 方向。
        """
        if abs(dx) < 1e-6 and abs(dy) < 1e-6:
            return frame
        return frame.transform(
            frame.size,
            Image.AFFINE,
            (1, 0, -dx, 0, 1, -dy),
            resample=Image.BICUBIC,
        )

    def _register_offset(
        self,
        base_mask: Image.Image,
        mask: Image.Image,
        search: int,
    ) -> tuple[float, float]:
        """用掩码互相关估计 mask 相对 base_mask 的位移，返回 (dx, dy)。

        先把两张掩码降到约 64px 做粗搜，再回到原分辨率做一次细化。
        纯 PIL 实现，靠 ImageChops.difference + ImageStat 计算差异能量。
        """
        def best_shift(a: Image.Image, b: Image.Image, radius: int) -> tuple[int, int]:
            best = (0, 0)
            best_score = None
            for dy in range(-radius, radius + 1):
                for dx in range(-radius, radius + 1):
                    shifted = b.transform(
                        b.size, Image.AFFINE, (1, 0, -dx, 0, 1, -dy), resample=Image.NEAREST
                    )
                    score = sum(ImageStat.Stat(ImageChops.difference(a, shifted)).mean)
                    if best_score is None or score < best_score:
                        best_score = score
                        best = (dx, dy)
            return best

        # 粗搜：降采样到长边约 64px，搜索范围按比例换算，向上取整保证覆盖。
        long_side = max(base_mask.size)
        factor = max(1, round(long_side / 64))
        small_a = base_mask.resize((max(1, base_mask.width // factor), max(1, base_mask.height // factor)))
        small_b = mask.resize((max(1, mask.width // factor), max(1, mask.height // factor)))
        # 用 ceil 而非整除，避免缩小后搜索范围不够导致只找到局部最优。
        coarse_radius = max(1, math.ceil(search / factor))
        cdx, cdy = best_shift(small_a, small_b, coarse_radius)

        # 细化：回到原分辨率，在粗搜结果附近小范围微调。
        fdx, fdy = cdx * factor, cdy * factor
        fine = 2
        best = (fdx, fdy)
        best_score = None
        for dy in range(fdy - fine, fdy + fine + 1):
            for dx in range(fdx - fine, fdx + fine + 1):
                shifted = mask.transform(
                    mask.size, Image.AFFINE, (1, 0, -dx, 0, 1, -dy), resample=Image.NEAREST
                )
                score = sum(ImageStat.Stat(ImageChops.difference(base_mask, shifted)).mean)
                if best_score is None or score < best_score:
                    best_score = score
                    best = (dx, dy)
        return float(best[0]), float(best[1])

    def _smooth(self, values: list[float]) -> list[float]:
        """对一维序列做中值平滑，只在出现跳变时替换，避免抹平真实动作。

        仅用于「纯锚点」模式：那里没有互相关兜底，单帧锚点算歪需要压制。
        配准模式不使用它，因为配准算出的偏移本身就是要应用的修正量。
        """
        if self.opts.smooth <= 0 or len(values) < 3:
            return values
        out = list(values)
        for index in range(1, len(values) - 1):
            median = sorted(values[index - 1 : index + 2])[1]
            # 与中位数偏离超过相邻帧平均变化的两倍才判定为跳变。
            neighbors = abs(values[index + 1] - values[index - 1])
            if abs(values[index] - median) > max(1.0, neighbors):
                out[index] = median
        return out

    def align_anchor(self) -> None:
        """把所有帧对齐到统一画布：稳健接地线 + 水平质心 + 互相关精修 + 时序平滑。"""
        if not self.frames:
            return
        # 画布要在最大帧尺寸之外再留出平移余量，否则对齐后内容会被裁掉。
        # 余量按互相关搜索范围与 pad 共同决定，保证最大可能的位移也放得下。
        max_w = max(frame.width for frame in self.frames)
        max_h = max(frame.height for frame in self.frames)
        margin = max(8, round(min(max_w, max_h) * 0.15)) + self.opts.pad
        self.canvas_w = max_w + margin * 2
        self.canvas_h = max_h + margin * 2

        masks = [_alpha_mask(frame, self.opts.thresh) for frame in self.frames]
        boxes = [_largest_component_bbox(mask) or (0, 0, frame.width, frame.height)
                 for mask, frame in zip(masks, self.frames)]

        if self.opts.anchor == "bottom":
            grounds = [self._ground_line(mask, box) for mask, box in zip(masks, boxes)]
            # 接地线做一次跨帧中值，压掉个别帧被投影带偏的情况。
            ground_ref = sorted(grounds)[len(grounds) // 2]
            centers = [self._centroid_x(mask, box) for mask, box in zip(masks, boxes)]
            center_ref = sorted(centers)[len(centers) // 2]
        else:
            grounds = [(box[1] + box[3]) * 0.5 for box in boxes]
            ground_ref = sorted(grounds)[len(grounds) // 2]
            centers = [(box[0] + box[2]) * 0.5 for box in boxes]
            center_ref = sorted(centers)[len(centers) // 2]

        # 以中位锚点决定基准帧的放置位置，避免第 1 帧本身是离群帧时整体偏移。
        base_dx = self.canvas_w * 0.5 - center_ref
        base_dy = (
            (self.canvas_h - self.opts.pad - ground_ref) if self.opts.anchor == "bottom"
            else (self.canvas_h * 0.5 - ground_ref)
        )
        dxs = [base_dx] * len(self.frames)
        dys = [base_dy] * len(self.frames)

        if self.opts.register and len(self.frames) > 1:
            # 互相关精修：以基准帧为参照估计每帧的相对位移。
            # edit_animation 会传入参考图作基准，保证所有帧都贴着同一张底图，
            # 而不是相对第 1 帧漂移。
            search = max(2, round(min(self.canvas_w, self.canvas_h) * 0.06))
            base_mask = masks[0]
            if self.opts.reference_frame is not None:
                ref = self.opts.reference_frame.convert("RGBA")
                # 参考图尺寸可能和帧不同，先缩放到同尺寸再取掩码。
                if ref.size != self.frames[0].size:
                    ref = ref.resize(self.frames[0].size, Image.LANCZOS)
                base_mask = _alpha_mask(ref, self.opts.thresh)
            dxs = [base_dx]
            dys = [base_dy]
            for index in range(1, len(self.frames)):
                rdx, rdy = self._register_offset(base_mask, masks[index], search)
                # _register_offset 返回的是“把该帧拉回基准所需的位移”，与 dxs 同向。
                # 这里不做中值过滤：单帧跳变正是要被拉回的目标，过滤掉反而留下偏移。
                dxs.append(base_dx + rdx)
                dys.append(base_dy + rdy)
        else:
            # 不用配准时退回逐帧锚点对齐，此时才需要时序平滑抑制单帧跳变。
            dxs = [self.canvas_w * 0.5 - c for c in centers]
            dys = [
                (self.canvas_h - self.opts.pad - g) if self.opts.anchor == "bottom"
                else (self.canvas_h * 0.5 - g)
                for g in grounds
            ]
            dxs = self._smooth(dxs)
            dys = self._smooth(dys)

        aligned = []
        for frame, dx, dy in zip(self.frames, dxs, dys):
            # 先把帧贴到画布左上角，再在画布坐标系里平移，两套坐标才不会错位。
            canvas = Image.new("RGBA", (self.canvas_w, self.canvas_h), (0, 0, 0, 0))
            canvas.alpha_composite(frame, (0, 0))
            moved = self._translate(canvas, dx, dy)
            aligned.append(moved)
        self.frames = aligned

        self.alignment = {
            "mode": "registration" if self.opts.register else "anchor",
            "scale_mode": self.opts.scale,
            "per_frame": [
                {
                    "index": index,
                    "scale": round(self.scale_factors[index], 4) if index < len(self.scale_factors) else 1.0,
                    "dx": round(dxs[index], 2),
                    "dy": round(dys[index], 2),
                    "ground": grounds[index],
                    "center": round(centers[index], 2),
                }
                for index in range(len(self.frames))
            ],
        }
        # 用对齐后重新测得的锚点做残余偏移统计，作为验收指标。
        self._measure_drift(center_ref, ground_ref)

        if self.opts.anchor == "bottom":
            self.pivot = (round(self.canvas_w * 0.5), self.canvas_h - self.opts.pad)
        else:
            self.pivot = (round(self.canvas_w * 0.5), round(self.canvas_h * 0.5))

    def _measure_drift(self, center_ref: float, ground_ref: float) -> None:
        """测量对齐后各帧锚点的残余离散度，写进对齐报告。

        对齐的目标是让所有帧的锚点落在同一位置，所以用各帧锚点相对
        目标画布位置的偏差的离散程度来衡量，而不是绝对距离。
        """
        masks = [_alpha_mask(frame, self.opts.thresh) for frame in self.frames]
        boxes = [_largest_component_bbox(mask) or (0, 0, frame.width, frame.height)
                 for mask, frame in zip(masks, self.frames)]
        if self.opts.anchor == "bottom":
            grounds = [self._ground_line(mask, box) for mask, box in zip(masks, boxes)]
            centers = [self._centroid_x(mask, box) for mask, box in zip(masks, boxes)]
            target_y = self.canvas_h - self.opts.pad
        else:
            grounds = [(box[1] + box[3]) * 0.5 for box in boxes]
            centers = [(box[0] + box[2]) * 0.5 for box in boxes]
            target_y = self.canvas_h * 0.5
        if not grounds:
            return
        # 每帧锚点与目标位置的偏差，再统计这组偏差自身的离散度。
        off_x = [c - self.canvas_w * 0.5 for c in centers]
        off_y = [g - target_y for g in grounds]
        mean_x = sum(off_x) / len(off_x)
        mean_y = sum(off_y) / len(off_y)
        residual = [math.hypot(x - mean_x, y - mean_y) for x, y in zip(off_x, off_y)]
        rms = math.sqrt(sum(d * d for d in residual) / len(residual))
        worst = max(residual)
        self.alignment.update(
            {
                "max_drift_px": round(worst, 2),
                "rms_drift_px": round(rms, 2),
                "verdict": "stable" if worst <= 2.0 else ("minor" if worst <= 5.0 else "unstable"),
            }
        )

    def apply_lock_outline(self) -> None:
        """把第 1 帧的边缘颜色回填到其余帧的同一位置，抑制轮廓抖动。

        只在“第 1 帧和第 N 帧都不透明”的像素上回填：姿势不同时两帧轮廓本来
        就不重合，强行按坐标覆盖会把别的部位颜色涂过来，形成鬼影。
        因此默认关闭，需要时再显式打开。
        """
        if not self.opts.lock_outline or len(self.frames) < 2:
            return
        base = self.frames[0]
        base_alpha = _alpha_mask(base, self.opts.thresh)
        # 取轮廓内侧的一圈边缘：该处第 1 帧一定不透明，颜色有效，不会带入透明区的黑色。
        size = self.opts.feather * 2 + 1
        edge = ImageChops.subtract(base_alpha, base_alpha.filter(ImageFilter.MinFilter(size)))
        base_rgb = base.convert("RGB")
        locked = [base]
        for frame in self.frames[1:]:
            out_rgb = frame.convert("RGB")
            # 只有两帧都判为实体的像素才回填，避免在姿势差异处涂出错位色块。
            both = ImageChops.multiply(edge, _alpha_mask(frame, self.opts.thresh))
            out_rgb.paste(base_rgb, None, both)
            locked.append(Image.merge("RGBA", (*out_rgb.split(), frame.getchannel("A"))))
        self.frames = locked

    def quantize_palette(self) -> None:
        """把所有帧量化到同一套 N 色调色板。"""
        if self.opts.palette <= 0 or not self.frames:
            return
        # 先把所有帧纵向拼成一张图，再整体量化，保证每帧使用同一套颜色。
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
            # 直通模式下帧尺寸可能和画布不一致，先贴到画布大小再拼，避免错位。
            if frame.size != (self.canvas_w, self.canvas_h):
                padded = Image.new("RGBA", (self.canvas_w, self.canvas_h), (0, 0, 0, 0))
                padded.paste(frame, (0, 0), frame)
                frame = padded
            # 先做边缘颜色外扩，再做缩放时就不会把透明像素混成黑边。
            bled = self._bleed_edges(frame, radius=2)
            x = self.opts.gutter + index * (self.canvas_w + self.opts.gutter)
            # 每帧单独贴到透明层再合成，避免 alpha 叠加出现暗边。
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

    def build_overlay(self) -> Image.Image | None:
        """把所有帧的半透明轮廓叠在同一个画布上，用于一眼看出残余偏移。

        每帧用不同色相着色，叠得越集中说明对齐越好；出现彩色重影即表示还有位移。
        """
        if len(self.frames) < 2:
            return None
        overlay = Image.new("RGBA", (self.canvas_w, self.canvas_h), (0, 0, 0, 0))
        count = len(self.frames)
        for index, frame in enumerate(self.frames):
            # 沿色轮均匀取色，保证相邻帧颜色区分明显。
            hue = index / count
            color = _hsv_to_rgb(hue, 0.85, 1.0)
            mask = _alpha_mask(frame, self.opts.thresh)
            # 半透明叠加，重叠区域会自然加深，便于判断偏移方向。
            tinted = Image.new("RGBA", frame.size, (*color, 110))
            tinted.putalpha(mask.point(lambda v: 110 if v else 0))
            overlay = Image.alpha_composite(overlay, tinted)
        return overlay

    def metrics(self) -> dict:
        """计算帧尺寸范围与首末帧差异，作为验收指标。"""
        if len(self.frames) < 2:
            return {}
        # 对齐后每帧都在同一张画布上，bboxes 还是缩放前算的，
        # 这里按最终帧重新测量，指标才反映真正交付的图集。
        boxes = []
        for frame in self.frames:
            mask = _alpha_mask(frame, self.opts.thresh)
            boxes.append(mask.getbbox() or (0, 0, frame.width, frame.height))
        widths = [right - left for left, _, right, _ in boxes]
        heights = [bottom - top for _, top, _, bottom in boxes]
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
            "alignment": self.alignment,
            "frames": frames,
        }

    def _save_gif(self, path: Path) -> None:
        """把帧序列导出为 GIF 预览，透明区域压在白色底上。"""
        if not self.frames:
            return
        duration = max(20, round(1000 / max(1, self.opts.fps)))
        # GIF 不支持半透明，先压到白底；disposal=2 表示每帧重置画布，避免残影。
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
            # 直通模式下各帧尺寸可能不一致，画布取最大值，避免小帧被裁或大帧溢出。
            self.canvas_w = max(frame.width for frame in self.frames)
            self.canvas_h = max(frame.height for frame in self.frames)
            self.pivot = (self.canvas_w // 2, self.canvas_h)
            # 统一补齐到画布尺寸，让瓦片图、联系图与单帧导出三处保持一致。
            if any(frame.size != (self.canvas_w, self.canvas_h) for frame in self.frames):
                padded_frames = []
                for frame in self.frames:
                    if frame.size == (self.canvas_w, self.canvas_h):
                        padded_frames.append(frame)
                        continue
                    padded = Image.new("RGBA", (self.canvas_w, self.canvas_h), (0, 0, 0, 0))
                    padded.paste(frame, (0, 0), frame)
                    padded_frames.append(padded)
                self.frames = padded_frames
        else:
            self.compute_bboxes()
            self.uniform_scale()
            self.align_anchor()
            self.apply_lock_outline()
            self.quantize_palette()

        sheet = self.build_spritesheet()
        contact_img = self.build_contact() if contact else None
        overlay_img = self.build_overlay() if (self.opts.overlay and not self.opts.raw) else None
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

        overlay_path = None
        if overlay_img is not None:
            overlay_path = out / "overlay.png"
            overlay_img.save(overlay_path)

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
            overlay=overlay_path,
            metrics=metrics,
            alignment=self.alignment,
        )
