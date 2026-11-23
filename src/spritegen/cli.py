"""命令行入口：不依赖 MCP 也能直接使用完整流水线。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image

from . import imageops
from .inspect import inspect_grid, quality_issues
from .pipeline import SpritePipeline
from .sheet import BuildOptions, SheetBuilder, split_grid


def _load_frames(inputs: list[str], cols: int, rows: int) -> list[Image.Image]:
    """收集输入并拆格，目录内按文件名排序取 png。"""
    files: list[Path] = []
    for raw in inputs:
        path = Path(raw)
        if path.is_dir():
            files.extend(sorted(path.glob("*.png")))
        elif path.is_file():
            files.append(path)
    if not files:
        raise SystemExit("没有找到输入图片")

    frames: list[Image.Image] = []
    for path in files:
        grid, _ = split_grid(Image.open(path), cols, rows)
        frames.extend(grid)
    return frames


def _cmd_generate(args: argparse.Namespace) -> None:
    options = BuildOptions(
        cols=args.cols,
        rows=args.rows,
        anchor=args.anchor,
        target_h=args.target_h,
        pad=args.pad,
        palette=args.palette,
        fps=args.fps,
        loop=not args.no_loop,
        gutter=args.gutter,
    )
    result = SpritePipeline().generate(
        args.prompt,
        cols=args.cols,
        rows=args.rows,
        name=args.name,
        options=options,
        retries=args.retries,
        remove_bg=not args.keep_bg,
        gif=not args.no_gif,
        size=args.size,
    )
    if args.json:
        print(json.dumps(result.as_dict(), ensure_ascii=False, indent=2))
        return
    print(f"帧数 {result.frame_count} · 单帧 {result.frame_size[0]}×{result.frame_size[1]} · 尝试 {result.attempts} 次")
    print(f"瓦片图 {result.spritesheet}")
    print(f"manifest {result.manifest}")
    if result.gif:
        print(f"GIF 预览 {result.gif}")
    for issue in result.issues:
        print(f"[质检未通过] {issue}")


def _cmd_build(args: argparse.Namespace) -> None:
    options = BuildOptions(
        cols=args.cols,
        rows=args.rows,
        anchor=args.anchor,
        target_h=args.target_h,
        pad=args.pad,
        palette=args.palette,
        fps=args.fps,
        loop=not args.no_loop,
        gutter=args.gutter,
        lock_outline=not args.no_lock_outline,
        feather=args.feather,
        raw=args.raw,
    )
    frames = _load_frames(args.inputs, args.cols, args.rows)
    result = SheetBuilder(options).build(frames, args.out, args.name, gif=not args.no_gif)
    print(f"帧数 {result.frame_count} · 单帧 {result.frame_size[0]}×{result.frame_size[1]} · pivot {result.pivot}")
    print(f"瓦片图 {result.spritesheet}")
    print(f"manifest {result.manifest}")
    if result.contact:
        print(f"联系图 {result.contact}")
    if result.gif:
        print(f"GIF 预览 {result.gif}")
    if result.metrics:
        print(f"验收指标 {json.dumps(result.metrics, ensure_ascii=False)}")


def _cmd_inspect(args: argparse.Namespace) -> None:
    report = inspect_grid(Image.open(args.image), args.cols, args.rows, gutter=args.gutter)
    issues = quality_issues(report, args.max_deviation)
    payload = {"ok": not issues, "issues": issues, **report.as_dict()}
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    print(f"{args.image} {report.size[0]}×{report.size[1]}  单格 {report.cell[0]}×{report.cell[1]}")
    for cell in report.cells:
        if cell.empty:
            print(f"  格({cell.row},{cell.col}) 空")
        else:
            box = cell.bbox
            print(
                f"  格({cell.row},{cell.col}) 内容 {cell.width}×{cell.height} "
                f"bbox={box} 中心=({cell.center[0]:.0f},{cell.center[1]:.0f})"
            )
    for issue in issues:
        print(f"[问题] {issue}")


def _cmd_remove_bg(args: argparse.Namespace) -> None:
    source = Path(args.image)
    target = Path(args.output) if args.output else source.with_name(f"{source.stem}-nobg.png")
    result = imageops.remove_solid_background(Image.open(source), tolerance=args.tolerance)
    result.save(target)
    print(f"去背 {source} -> {target} {result.size}")


def _cmd_trim(args: argparse.Namespace) -> None:
    result = imageops.trim_alpha(Image.open(args.image), pad=args.pad, max_side=args.max_side, thresh=args.thresh)
    if result is None:
        raise SystemExit("没有不透明像素")
    result.save(args.output)
    print(f"裁切 {args.image} -> {args.output} {result.size}")


def _cmd_seamless(args: argparse.Namespace) -> None:
    size = (args.w, args.h) if args.w and args.h else None
    result = imageops.make_seamless(Image.open(args.image), out_size=size, top=args.top)
    result.save(args.output)
    print(f"生成 {args.output} {result.size}")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="spritegen", description="序列帧动画生成与图集处理工具")
    sub = parser.add_subparsers(dest="command", required=True)

    generate = sub.add_parser("generate", help="生成序列帧并直接输出图集")
    generate.add_argument("prompt", help="角色与动作描述")
    generate.add_argument("--cols", type=int, default=4, help="网格列数")
    generate.add_argument("--rows", type=int, default=2, help="网格行数")
    generate.add_argument("--name", default="anim", help="动作名，写入 manifest")
    generate.add_argument("--retries", type=int, default=1, help="质检不通过时的重试次数")
    generate.add_argument("--fps", type=int, default=10, help="帧率")
    generate.add_argument("--no-loop", action="store_true", help="manifest 中标记为不循环")
    generate.add_argument("--anchor", choices=["bottom", "center"], default="bottom", help="锚点对齐方式")
    generate.add_argument("--target-h", type=int, default=0, help="统一缩放目标高度，0=自适应")
    generate.add_argument("--pad", type=int, default=0, help="每帧四周透明边距")
    generate.add_argument("--palette", type=int, default=0, help="统一调色板颜色数，0=不量化")
    generate.add_argument("--gutter", type=int, default=4, help="瓦片图帧间隔")
    generate.add_argument("--size", help="请求画布尺寸，例如 1024x1024")
    generate.add_argument("--keep-bg", action="store_true", help="保留原背景，不做去背")
    generate.add_argument("--no-gif", action="store_true", help="不导出 GIF 预览")
    generate.add_argument("--json", action="store_true", help="以 JSON 输出结果")
    generate.set_defaults(func=_cmd_generate)

    build = sub.add_parser("build", help="把已有网格图构建成图集")
    build.add_argument("inputs", nargs="+", help="输入网格图或目录")
    build.add_argument("--cols", type=int, default=2, help="每张输入图的网格列数")
    build.add_argument("--rows", type=int, default=2, help="每张输入图的网格行数")
    build.add_argument("--out", required=True, help="输出目录")
    build.add_argument("--name", required=True, help="动作名，写入 manifest")
    build.add_argument("--anchor", choices=["bottom", "center"], default="bottom")
    build.add_argument("--target-h", type=int, default=0)
    build.add_argument("--pad", type=int, default=0)
    build.add_argument("--palette", type=int, default=0)
    build.add_argument("--fps", type=int, default=10)
    build.add_argument("--gutter", type=int, default=4)
    build.add_argument("--no-loop", action="store_true")
    build.add_argument("--no-lock-outline", action="store_true", help="禁用锁轮廓")
    build.add_argument("--feather", type=int, default=3, help="锁轮廓边缘回填宽度")
    build.add_argument("--raw", action="store_true", help="原样模式：只切图合图")
    build.add_argument("--no-gif", action="store_true")
    build.set_defaults(func=_cmd_build)

    inspect = sub.add_parser("inspect", help="检查网格图集每格内容")
    inspect.add_argument("image")
    inspect.add_argument("--cols", type=int, required=True)
    inspect.add_argument("--rows", type=int, required=True)
    inspect.add_argument("--gutter", type=int, default=0)
    inspect.add_argument("--max-deviation", type=float, default=0.2, help="允许的帧尺寸相对偏差")
    inspect.add_argument("--json", action="store_true")
    inspect.set_defaults(func=_cmd_inspect)

    remove_bg = sub.add_parser("remove-bg", help="去除纯色背景")
    remove_bg.add_argument("image")
    remove_bg.add_argument("output", nargs="?")
    remove_bg.add_argument("--tolerance", type=int, default=18)
    remove_bg.set_defaults(func=_cmd_remove_bg)

    trim = sub.add_parser("trim", help="按 alpha 裁边")
    trim.add_argument("image")
    trim.add_argument("output")
    trim.add_argument("--pad", type=int, default=0)
    trim.add_argument("--max-side", type=int, default=0)
    trim.add_argument("--thresh", type=int, default=0)
    trim.set_defaults(func=_cmd_trim)

    seamless = sub.add_parser("seamless", help="生成左右无缝可平铺图")
    seamless.add_argument("image")
    seamless.add_argument("output")
    seamless.add_argument("--w", type=int, default=0)
    seamless.add_argument("--h", type=int, default=0)
    seamless.add_argument("--top", type=int, default=0)
    seamless.set_defaults(func=_cmd_seamless)

    return parser


def main() -> None:
    """解析参数并执行对应子命令。"""
    args = _build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
