"""MCP server：把生图与序列帧图集构建暴露成工具，stdio 传输。"""

from __future__ import annotations

import io
import json
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp import Image as MCPImage
from PIL import Image as PILImage

from . import imageops
from .api import ImageClient, save_images
from .config import Settings, config_hint, load_settings
from .inspect import inspect_grid, quality_issues
from .pipeline import SpritePipeline
from .sheet import BuildOptions, SheetBuilder, split_grid

mcp = FastMCP("spritegen")


def _dump(data: object) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def _settings() -> Settings:
    """加载配置；用户还没创建 config.json 时直接提示，而不是继续请求接口。"""
    settings = load_settings()
    if settings.config_file is None and not settings.api_key:
        raise RuntimeError(config_hint())
    return settings


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


@mcp.tool()
def generate_image(
    prompt: str,
    size: str | None = None,
    n: int = 1,
    out_dir: str | None = None,
) -> list:
    """Generate 1-4 images from a text prompt with the configured OpenAI-compatible image API and save them to disk, returning file paths and pixel sizes."""
    settings = _settings()
    settings.require_key()
    blobs, model = ImageClient(settings).generate(
        prompt.strip(), size=size, n=max(1, min(n, 4))
    )
    directory = _resolve(out_dir, settings.root_dir) if out_dir else settings.root_dir / settings.out_dir
    paths = save_images(blobs, directory)
    payload = {
        "model": model,
        "images": [
            {"path": str(path), "size": list(PILImage.open(path).size)} for path in paths
        ],
    }
    return _with_preview(payload, paths[0])


@mcp.tool()
def edit_image(
    prompt: str,
    images: list[str],
    size: str | None = None,
    out_dir: str | None = None,
) -> list:
    """Edit or restyle existing local images with a text prompt, uploading them as references. Use it to keep a character consistent across poses."""
    settings = _settings()
    settings.require_key()
    sources = _collect(images, settings.root_dir)
    blobs, model = ImageClient(settings).edit(prompt.strip(), sources, size=size)
    directory = _resolve(out_dir, settings.root_dir) if out_dir else settings.root_dir / settings.out_dir
    paths = save_images(blobs, directory, prefix="edit")
    payload = {
        "model": model,
        "images": [
            {"path": str(path), "size": list(PILImage.open(path).size)} for path in paths
        ],
    }
    return _with_preview(payload, paths[0])


@mcp.tool()
def build_spritesheet(
    inputs: list[str],
    cols: int = 2,
    rows: int = 2,
    out_dir: str = "generated/sprites",
    name: str = "anim",
    anchor: str = "bottom",
    target_h: int = 0,
    pad: int = 0,
    palette: int = 0,
    fps: int = 10,
    loop: bool = True,
    gutter: int = 4,
    raw: bool = False,
    gif: bool = True,
) -> list:
    """Assemble an existing grid image (or several) into an aligned sprite sheet: split cells, uniform scale, anchor alignment, outline locking, palette quantization, then export spritesheet.png, frames, manifest.json and a GIF preview."""
    settings = _settings()
    files = _collect(inputs, settings.root_dir)
    frames: list[PILImage.Image] = []
    for path in files:
        grid, _ = split_grid(PILImage.open(path), cols, rows)
        frames.extend(grid)

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
    )
    result = SheetBuilder(options).build(frames, _resolve(out_dir, settings.root_dir), name, gif=gif)
    payload = {
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


@mcp.tool()
def inspect_spritesheet(
    image: str,
    cols: int,
    rows: int,
    gutter: int = 0,
    max_deviation: float = 0.2,
) -> str:
    """Inspect a grid sprite sheet cell by cell: alpha bounding boxes, per-frame size, centers, empty cells and size deviation. Returns JSON with the detected problems."""
    settings = _settings()
    path = _resolve(image, settings.root_dir)
    report = inspect_grid(PILImage.open(path), cols, rows, gutter=gutter)
    issues = quality_issues(report, max_deviation)
    return _dump({"ok": not issues, "issues": issues, **report.as_dict()})


@mcp.tool()
def remove_background(image: str, output: str | None = None, tolerance: int = 18) -> str:
    """Remove a flat solid background by flood-filling from the four corners with tolerance, keeping interior pixels that share the color. Returns the output path."""
    settings = _settings()
    source = _resolve(image, settings.root_dir)
    target = _resolve(output, settings.root_dir) if output else source.with_name(f"{source.stem}-nobg.png")
    img = imageops.remove_solid_background(PILImage.open(source), tolerance=tolerance)
    target.parent.mkdir(parents=True, exist_ok=True)
    img.save(target)
    return _dump({"source": str(source), "output": str(target), "size": list(img.size)})


@mcp.tool()
def generate_animation(
    prompt: str,
    cols: int = 4,
    rows: int = 2,
    name: str = "anim",
    out_dir: str | None = None,
    retries: int = 1,
    fps: int = 10,
    loop: bool = True,
    anchor: str = "bottom",
    target_h: int = 0,
    palette: int = 0,
    remove_bg: bool = True,
    gif: bool = True,
    size: str | None = None,
) -> list:
    """End-to-end sprite animation generation: build a grid prompt, generate the sheet, remove the background, inspect every cell, retry when cells are empty or inconsistent, then export spritesheet.png, frames, manifest.json and a GIF preview."""
    pipeline = SpritePipeline(settings=_settings())
    options = BuildOptions(
        cols=cols,
        rows=rows,
        anchor=anchor,
        target_h=target_h,
        palette=palette,
        fps=fps,
        loop=loop,
    )
    result = pipeline.generate(
        prompt,
        cols=cols,
        rows=rows,
        out_dir=out_dir,
        name=name,
        options=options,
        retries=retries,
        remove_bg=remove_bg,
        gif=gif,
        size=size,
    )
    return _with_preview(result.as_dict(), result.contact or result.spritesheet)


def main() -> None:
    """以 stdio 传输启动 MCP 服务。"""
    mcp.run()


if __name__ == "__main__":
    main()
