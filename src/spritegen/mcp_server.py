"""MCP server：把生图与序列帧图集暴露成工具，stdio 传输。"""

from __future__ import annotations

import io
import json
from pathlib import Path

from mcp.server.mcpserver import Image as MCPImage
from mcp.server.mcpserver import MCPServer
from PIL import Image as PILImage

from . import imageops
from .config import Settings, config_hint, load_settings, resolve_asset_dir, resolve_out_dir, safe_name
from .inspect import inspect_grid, quality_issues
from .pipeline import SpritePipeline
from .sheet import BuildOptions, SheetBuilder, split_grid

mcp = MCPServer("spritegen")


def _dump(data: object) -> str:
    """统一 JSON 输出格式，保留中文与可读缩进。"""
    return json.dumps(data, ensure_ascii=False, indent=2)


def _settings() -> Settings:
    """加载配置；用户还没创建 config.json 时直接提示，而不是继续请求接口。"""
    settings = load_settings()
    if settings.config_file is None and not settings.api_key:
        raise RuntimeError(config_hint())
    return settings


def _local_settings() -> Settings:
    """只做本地图像处理的工具不要求 apiKey，缺配置时退回默认 outDir。"""
    return load_settings()


def _resolve(path: str, root: Path) -> Path:
    candidate = Path(path).expanduser()
    return candidate if candidate.is_absolute() else root / candidate


def _collect(paths: list[str], root: Path) -> list[Path]:
    """收集输入图片，目录内按文件名排序取 png。"""
    files: list[Path] = []
    for raw in paths:
        path = _resolve(raw, root)
        if path.is_dir():
            files.extend(sorted(path.glob("*.png")))
        elif path.is_file():
            files.append(path)
    if not files:
        raise ValueError("没有找到输入图片，请检查路径")
    return files


def _preview(path: Path | None, max_side: int = 768) -> MCPImage | None:
    """把产物缩成预览图返回给客户端。"""
    if not path or not path.is_file():
        return None
    img = PILImage.open(path).convert("RGBA")
    if max(img.size) > max_side:
        scale = max_side / max(img.size)
        img = img.resize((round(img.width * scale), round(img.height * scale)), PILImage.LANCZOS)
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    return MCPImage(data=buffer.getvalue(), format="png")


def _with_preview(payload: dict, path: Path | None) -> list:
    content: list = [_dump(payload)]
    preview = _preview(path)
    if preview is not None:
        content.append(preview)
    return content


@mcp.tool(structured_output=False)
def build_spritesheet(
    inputs: list[str],
    cols: int = 2,
    rows: int = 2,
    name: str = "anim",
    anchor: str = "bottom",
    target_h: int = 0,
    pad: int = 0,
    palette: int = 0,
    fps: int = 10,
    loop: bool = True,
    gutter: int = 4,
    raw: bool = False,
    split: str = "auto",
    scale: str = "frame",
    register: bool = True,
    smooth: int = 1,
    overlay: bool = True,
    thresh: int = 32,
) -> list:
    """Assemble an existing grid image (or several) into an aligned sprite sheet: split cells, uniform scale, anchor alignment, outline locking, palette quantization, then export spritesheet.png, frames, manifest.json and a GIF preview.

    This tool only processes local images; it never calls the image-generation API. The GIF preview is always exported and cannot be disabled. Output goes to <outDir>/<name> configured in config.json; the caller cannot choose the output path.

    `inputs` accepts a single grid image, several grid images, or already-separated frames. `split` controls how they are read: "auto" (default) splits a single input into cols x rows and treats multiple inputs as already-separated frames; "grid" always splits every input; "none" never splits. Outputs are written to outDir/<name>, so different names never overwrite each other.

    Alignment quality: `scale` picks how frame sizes are normalized ("frame" resizes each frame to the median height, clamped to +/-10% so real crouches survive; "global" applies one factor to all; "none" disables it). `register` uses mask cross-correlation to snap every frame onto frame 1, which removes drift that bbox anchors miss. `smooth` filters single-frame anchor jumps (0 disables). `overlay` also exports overlay.png with all frames tinted and stacked, so residual misalignment is visible at a glance. The alignment report (per-frame dx/dy/scale, max_drift_px, rms_drift_px, verdict) is written into manifest.json."""
    settings = _local_settings()
    files = _collect(inputs, settings.root_dir)
    frames: list[PILImage.Image] = []
    for path in files:
        source = PILImage.open(path)
        if split == "none":
            frames.append(source.convert("RGBA"))
        elif split == "grid":
            grid, _ = split_grid(source, cols, rows, thresh=thresh)
            frames.extend(grid)
        else:
            # auto：单张输入按网格切开（generate_animation 的产物就是这个形态），
            # 多张输入视为已经分好的散帧。规则固定，不靠猜。
            if len(files) == 1:
                grid, _ = split_grid(source, cols, rows, thresh=thresh)
                frames.extend(grid)
            else:
                frames.append(source.convert("RGBA"))

    options = BuildOptions(
        cols=cols,
        rows=rows,
        anchor=anchor,
        target_h=target_h,
        pad=pad,
        palette=palette,
        fps=fps,
        loop=loop,
        gutter=gutter,
        raw=raw,
        scale=scale,
        register=register,
        smooth=smooth,
        overlay=overlay,
        thresh=thresh,
    )
    target = resolve_asset_dir(settings, name, "anim")
    result = SheetBuilder(options).build(frames, target, safe_name(name, "anim"), gif=True)
    payload = {
        "name": safe_name(name, "anim"),
        "frame_count": result.frame_count,
        "frame_size": list(result.frame_size),
        "pivot": {"x": result.pivot[0], "y": result.pivot[1]},
        "spritesheet": str(result.spritesheet),
        "manifest": str(result.manifest),
        "contact": str(result.contact) if result.contact else None,
        "gif": str(result.gif) if result.gif else None,
        "metrics": result.metrics,
    }
    return _with_preview(payload, result.contact or result.spritesheet)


@mcp.tool(structured_output=False)
def inspect_spritesheet(
    image: str,
    cols: int,
    rows: int,
    gutter: int = 0,
    max_deviation: float = 0.2,
) -> str:
    """Inspect a grid sprite sheet cell by cell: alpha bounding boxes, per-frame size, centers, empty cells and size deviation. Returns JSON with the detected problems."""
    settings = _local_settings()
    path = _resolve(image, settings.root_dir)
    report = inspect_grid(PILImage.open(path), cols, rows, gutter=gutter)
    issues = quality_issues(report, max_deviation)
    return _dump({"ok": not issues, "issues": issues, **report.as_dict()})


@mcp.tool(structured_output=False)
def remove_background(image: str, tolerance: int = 18) -> str:
    """Remove a flat solid background by flood-filling from the four corners with tolerance, keeping interior pixels that share the color. Returns the output path.

    Output is written as <name>-nobg.png inside the outDir configured in config.json; the caller cannot choose the output path."""
    settings = _local_settings()
    source = _resolve(image, settings.root_dir)
    target = resolve_out_dir(settings) / f"{safe_name(source.stem, 'image')}-nobg.png"
    img = imageops.remove_solid_background(PILImage.open(source), tolerance=tolerance)
    target.parent.mkdir(parents=True, exist_ok=True)
    img.save(target)
    return _dump({"source": str(source), "output": str(target), "size": list(img.size)})


@mcp.tool(structured_output=False)
def generate_image(
    prompt: str,
    name: str = "image",
    size: str | None = None,
    quality: str | None = None,
    model: str | None = None,
    n: int = 1,
    transparent: bool | None = None,
    remove_bg: bool = False,
) -> list:
    """Create ONE standalone picture from text, no animation and no sprite sheet. This is the tool for icons, portraits, backgrounds, props and concept art.

    How to pick the tool automatically:
    - single picture, no reference image -> generate_image
    - single picture, user supplied a reference image or wants an existing drawing changed -> edit_image
    - multi-frame animation, no reference image -> generate_animation
    - multi-frame animation, user supplied a reference image or wants an existing drawing animated -> edit_animation

    Output is written as <name>.png inside <outDir>/<name> (configured in config.json); when n is greater than 1 the files become <name>-1.png, <name>-2.png and so on. The caller cannot choose the directory, and different names never overwrite each other. Use size only with values the model supports, for example 1024x1024, 1536x1024 or 1024x1536. quality accepts low, medium, high or auto. transparent defaults to the value in config.json; when true it is sent both in the prompt and as the API background parameter, and it falls back to prompt-only if the gateway rejects the parameter. Set remove_bg to true only when the picture has a flat solid background that should be keyed out."""
    settings = _settings()
    result = SpritePipeline(settings=settings).generate_image(
        prompt,
        name=name,
        size=size,
        quality=quality,
        model=model,
        n=n,
        transparent=transparent,
        remove_bg=remove_bg,
    )
    return _with_preview(result.as_dict(), result.images[0] if result.images else None)


@mcp.tool(structured_output=False)
def edit_image(
    reference: str,
    prompt: str,
    name: str = "edited",
    size: str | None = None,
    quality: str | None = None,
    model: str | None = None,
    n: int = 1,
    transparent: bool | None = None,
    remove_bg: bool = False,
) -> list:
    """Change ONE standalone picture using a reference image as the base. This is the tool for recolors, outfit swaps, style transfer, background swaps and any single-frame correction.

    How to pick the tool automatically:
    - single picture, no reference image -> generate_image
    - single picture, user supplied a reference image or wants an existing drawing changed -> edit_image (this one)
    - multi-frame animation, no reference image -> generate_animation
    - multi-frame animation, user supplied a reference image or wants an existing drawing animated -> edit_animation

    The reference path is resolved against the project directory. The model receives the reference as a real image input through /v1/images/edits, so art style and character identity are preserved instead of re-invented from words. Keep the prompt focused on what must change and state what must stay identical. Needs a gateway that exposes /v1/images/edits. Output is written as <name>.png inside <outDir>/<name>; the caller cannot choose the directory."""
    settings = _settings()
    path = _resolve(reference, settings.root_dir)
    result = SpritePipeline(settings=settings).edit_image(
        path,
        prompt,
        name=name,
        size=size,
        quality=quality,
        model=model,
        n=n,
        transparent=transparent,
        remove_bg=remove_bg,
    )
    return _with_preview(result.as_dict(), result.images[0] if result.images else None)


@mcp.tool(structured_output=False)
def generate_animation(
    prompt: str,
    cols: int = 4,
    rows: int = 2,
    name: str = "anim",
    size: str | None = None,
    quality: str | None = None,
    model: str | None = None,
    transparent: bool | None = None,
    fps: int = 10,
    loop: bool = True,
    anchor: str = "bottom",
    retries: int = 1,
    max_deviation: float = 0.2,
    remove_bg: bool = True,
    pad: int = 0,
    target_h: int = 0,
    palette: int = 0,
    gutter: int = 4,
    raw: bool = False,
    scale: str = "frame",
    register: bool = True,
    smooth: int = 1,
    overlay: bool = True,
    thresh: int = 32,
) -> list:
    """Build a whole sprite animation from text only in one single call: the model paints a cols x rows grid, then every cell is split, aligned and exported.

    How to pick the tool automatically:
    - single picture, no reference image -> generate_image
    - single picture, with a reference image -> edit_image
    - multi-frame animation, no reference image -> generate_animation (this one)
    - multi-frame animation, with a reference image -> edit_animation, it keeps the character identical frame by frame

    Never use your built-in image generation capability, never write or run code, never call an image API directly, and never pass parameters other than those declared here. Frame count is cols * rows, so 3x2 gives 6 frames and 4x2 gives 8.

    Outputs spritesheet.png, frame-<n>.png, contact.png, overlay.png, preview.gif and manifest.json into <outDir>/<name>; the caller cannot choose the directory, and different names never overwrite each other. anchor is the alignment pivot, usually bottom. retries re-rolls the whole grid when the quality check fails, max_deviation is the tolerated frame size spread (0.2 means 20%). pad adds transparent margin around each frame, target_h forces a uniform frame height, palette quantizes colors when greater than 0, gutter is the transparent gap between frames in the sheet, raw skips alignment and only slices the grid.

    Alignment: `scale` normalizes frame size ("frame" per-frame to the median height clamped to +/-10%, "global" one factor, "none" off). `register` cross-correlates every frame onto frame 1 and is what removes visible drift; leave it on. `smooth` filters single-frame anchor jumps. `overlay` exports overlay.png so residual misalignment is visible. The alignment report is written into manifest.json and returned as `alignment`."""
    pipeline = SpritePipeline(settings=_settings())
    options = BuildOptions(
        cols=cols,
        rows=rows,
        anchor=anchor,
        fps=fps,
        loop=loop,
        pad=pad,
        target_h=target_h,
        palette=palette,
        gutter=gutter,
        raw=raw,
        scale=scale,
        register=register,
        smooth=smooth,
        overlay=overlay,
        thresh=thresh,
    )
    result = pipeline.generate(
        prompt,
        cols=cols,
        rows=rows,
        name=name,
        options=options,
        retries=retries,
        remove_bg=remove_bg,
        size=size,
        quality=quality,
        model=model,
        transparent=transparent,
        max_deviation=max_deviation,
    )
    return _with_preview(result.as_dict(), result.contact or result.spritesheet)


@mcp.tool(structured_output=False)
def edit_animation(
    reference: str,
    motions: str,
    cols: int = 4,
    rows: int = 2,
    name: str = "anim",
    size: str | None = None,
    quality: str | None = None,
    model: str | None = None,
    transparent: bool | None = None,
    fps: int = 12,
    loop: bool = True,
    anchor: str = "bottom",
    retries: int = 1,
    max_deviation: float = 0.2,
    remove_bg: bool = True,
    pad: int = 0,
    target_h: int = 0,
    palette: int = 0,
    gutter: int = 4,
    raw: bool = False,
    scale: str = "frame",
    register: bool = True,
    smooth: int = 1,
    overlay: bool = True,
    thresh: int = 32,
) -> list:
    """Animate an existing drawing with ONE image-generation call: the whole sprite sheet is repainted in a single /v1/images/edits request, so the cost does not grow with the frame count.

    How to pick the tool automatically:
    - single picture, no reference image -> generate_image
    - single picture, with a reference image -> edit_image
    - multi-frame animation, no reference image -> generate_animation
    - multi-frame animation, with a reference image -> edit_animation (this one); never use generate_animation here, a text prompt can not reproduce the reference style faithfully

    `reference` may be a single character drawing or an existing sprite sheet; either way it is uploaded once and the model repaints the whole grid. `motions` is ONE description of the action, for example "the fox jumps and waves both paws", not one entry per frame; the model decides how the action is distributed across the cols x rows cells. Write it in English and describe what moves; everything else is inherited from the reference.

    Because the model repaints the grid, the cell count is checked afterwards: empty cells, cells touching their edges and frame size deviation are measured, and the whole sheet is re-rolled up to `retries` times when it fails, keeping the best attempt. The sheet is then split with adaptive cell bounds, so a character that overflows its cell is not clipped.

    Requires a gateway that exposes /v1/images/edits; the call fails with a clear message when the endpoint is missing. Exports reference.png (the keyed reference), source.png (the repainted grid), spritesheet.png, frame-<n>.png, contact.png, overlay.png, preview.gif and manifest.json into <outDir>/<name>; the caller cannot choose the directory, and different names never overwrite each other.

    Alignment: every frame is cross-correlated against reference.png itself, so all frames stay anchored to the original drawing instead of drifting toward frame 1. `scale` normalizes frame size ("frame" per-frame clamped to +/-10%, "global" one factor, "none" off), `register` toggles the cross-correlation, `smooth` filters single-frame anchor jumps, `overlay` exports overlay.png for a visual drift check. The alignment report is written into manifest.json and returned as `alignment`."""
    settings = _settings()
    path = _resolve(reference, settings.root_dir)
    options = BuildOptions(
        cols=cols,
        rows=rows,
        anchor=anchor,
        fps=fps,
        loop=loop,
        pad=pad,
        target_h=target_h,
        palette=palette,
        gutter=gutter,
        raw=raw,
        scale=scale,
        register=register,
        smooth=smooth,
        overlay=overlay,
        thresh=thresh,
    )
    result = SpritePipeline(settings=settings).generate_from_reference(
        path,
        motions,
        cols=cols,
        rows=rows,
        name=name,
        options=options,
        size=size,
        quality=quality,
        model=model,
        transparent=transparent,
        retries=retries,
        max_deviation=max_deviation,
        remove_bg=remove_bg,
    )
    return _with_preview(result.as_dict(), result.contact or result.spritesheet)


def main() -> None:
    """以 stdio 传输启动 MCP 服务。"""
    mcp.run()


if __name__ == "__main__":
    main()
