"""对齐精度量化验证：注入已知位移/缩放，测量对齐后的还原误差。

不联网、不花钱。用一张合成的“角色”图，人为施加位移和缩放，
跑完对齐流程后测量残余误差，用来判断对齐算法是否真的有效。
"""

from __future__ import annotations

import math
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from PIL import Image, ImageDraw

from spritegen.sheet import BuildOptions, SheetBuilder


def make_character(size: int = 256) -> Image.Image:
    """画一个不对称的角色：身体 + 一条腿偏左 + 一条手臂偏右，便于检验中轴。"""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    cx, cy = size // 2, size // 2
    # 躯干
    d.ellipse((cx - 40, cy - 60, cx + 40, cy + 40), fill=(200, 120, 60, 255))
    # 头
    d.ellipse((cx - 28, cy - 100, cx + 28, cy - 45), fill=(230, 170, 110, 255))
    # 左腿（偏左）
    d.rectangle((cx - 34, cy + 30, cx - 14, cy + 95), fill=(120, 70, 40, 255))
    # 右腿（偏右）
    d.rectangle((cx + 14, cy + 30, cx + 34, cy + 95), fill=(120, 70, 40, 255))
    # 右臂伸出，制造左右不对称
    d.rectangle((cx + 38, cy - 40, cx + 80, cy - 20), fill=(200, 120, 60, 255))
    return img


def shifted(img: Image.Image, dx: int, dy: int) -> Image.Image:
    """把内容平移 dx, dy 像素，画布尺寸不变。"""
    out = Image.new("RGBA", img.size, (0, 0, 0, 0))
    out.paste(img, (dx, dy), img)
    return out


def scaled(img: Image.Image, factor: float) -> Image.Image:
    """围绕画布中心缩放，模拟模型画大画小。"""
    w, h = img.size
    new = (max(1, round(w * factor)), max(1, round(h * factor)))
    resized = img.resize(new, Image.LANCZOS)
    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    out.paste(resized, (round((w - new[0]) / 2), round((h - new[1]) / 2)), resized)
    return out


def anchor_of(img: Image.Image, thresh: int = 32) -> tuple[float, float]:
    """取接地线 y 与水平质心 x 作为该帧的锚点。"""
    mask = img.getchannel("A").point(lambda v: 255 if v > thresh else 0)
    bbox = mask.getbbox()
    if not bbox:
        return (0.0, 0.0)
    left, top, right, bottom = bbox
    width = max(1, right - left)
    need = max(2, round(width * 0.05))
    pixels = mask.load()
    ground = bottom - 1
    for y in range(bottom - 1, top - 1, -1):
        count = 0
        for x in range(left, right):
            if pixels[x, y]:
                count += 1
                if count >= need:
                    ground = y
                    break
        else:
            continue
        break
    total = weighted = 0
    for y in range(top, bottom):
        for x in range(left, right):
            if pixels[x, y]:
                total += 1
                weighted += x
    centroid = weighted / total if total else (left + right) / 2
    return centroid, ground


def run_case(label: str, frames: list[Image.Image], options: BuildOptions) -> dict:
    out = Path(tempfile.mkdtemp(prefix="align_"))
    built = SheetBuilder(options).build(frames, out, "test", gif=False, contact=False)
    anchors = [anchor_of(Image.open(p)) for p in built.frames]
    xs = [a[0] for a in anchors]
    ys = [a[1] for a in anchors]
    spread_x = max(xs) - min(xs)
    spread_y = max(ys) - min(ys)
    print(f"\n{label}")
    print(f"  接地线 y 范围: {min(ys):.0f}..{max(ys):.0f}  跨度 {spread_y:.1f}px")
    print(f"  质心   x 范围: {min(xs):.1f}..{max(xs):.1f}  跨度 {spread_x:.1f}px")
    print(f"  对齐报告: max_drift={built.alignment.get('max_drift_px')} "
          f"rms={built.alignment.get('rms_drift_px')} verdict={built.alignment.get('verdict')}")
    return {"spread_x": spread_x, "spread_y": spread_y, "alignment": built.alignment}


def main() -> int:
    base = make_character()
    failures: list[str] = []

    def expect(label: str, ok: bool) -> None:
        print(f"  {'PASS' if ok else 'FAIL'}  {label}")
        if not ok:
            failures.append(label)

    # 场景 1：只有位移，没有缩放。旧算法基本不还原，新算法应压到 2px 内。
    offsets = [(0, 0), (7, -5), (-6, 4), (3, 9), (-9, -3), (5, 6)]
    frames = [shifted(base, dx, dy) for dx, dy in offsets]
    r1 = run_case("[1] 纯位移（注入最大 9px）", frames,
                  BuildOptions(anchor="bottom", scale="none", register=True, smooth=0))
    expect("接地线跨度 <= 2px", r1["spread_y"] <= 2)
    expect("水平质心跨度 <= 2px", r1["spread_x"] <= 2)

    # 场景 2：位移 + 缩放，开启 frame 归一化。
    mixed = []
    for i, (dx, dy) in enumerate(offsets):
        factor = [1.0, 1.06, 0.95, 1.08, 0.94, 1.03][i]
        mixed.append(scaled(shifted(base, dx, dy), factor))
    r2 = run_case("[2] 位移 + 缩放（scale=frame）", mixed,
                  BuildOptions(anchor="bottom", scale="frame", register=True, smooth=0))
    expect("接地线跨度 <= 3px", r2["spread_y"] <= 3)
    expect("水平质心跨度 <= 3px", r2["spread_x"] <= 3)

    # 场景 3：关闭配准，只靠锚点，作为对照。
    r3 = run_case("[3] 对照：关闭配准（scale=frame, register=False）", mixed,
                  BuildOptions(anchor="bottom", scale="frame", register=False, smooth=0))
    print("  （对照项，不判定通过与否）")

    # 场景 4：单帧离群跳变，检验配准能否把它拉回。
    jumpy = [shifted(base, 0, 0) for _ in range(6)]
    jumpy[3] = shifted(base, 18, 12)  # 第 4 帧突然偏移
    r4 = run_case("[4] 单帧跳变（第 4 帧 +18/+12）", jumpy,
                  BuildOptions(anchor="bottom", scale="none", register=True, smooth=1))
    expect("跳变帧被拉回，跨度 <= 3px", r4["spread_y"] <= 3 and r4["spread_x"] <= 3)

    print("\n=== 汇总 ===")
    if failures:
        print(f"{len(failures)} 项未达标：")
        for f in failures:
            print("  -", f)
        return 1
    print("全部达标")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
