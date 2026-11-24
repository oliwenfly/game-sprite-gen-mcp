"""MCP server：把生图与序列帧图集暴露成工具，stdio 传输。"""

from __future__ import annotations

import io
import json
from pathlib import Path

from mcp.server.mcpserver import Image as MCPImage
from mcp.server.mcpserver import MCPServer
from PIL import Image as PILImage

from . import imageops
from .config import Settings, config_hint, load_settings, resolve_out_dir
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
) -> list:
    """Assemble an existing grid image (or several) into an aligned sprite sheet: split cells, uniform scale, anchor alignment, outline locking, palette quantization, then export spritesheet.png, frames, manifest.json and a GIF preview.

    This tool only processes local images; it never calls the image-generation API. The GIF preview is always exported and cannot be disabled. Output always goes to the outDir configured in config.json; the caller cannot choose the output path."""
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
    result = SheetBuilder(options).build(frames, resolve_out_dir(settings), name, gif=True)
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


@mcp.tool(structured_output=False)
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


@mcp.tool(structured_output=False)
def remove_background(image: str, tolerance: int = 18) -> str:
    """Remove a flat solid background by flood-filling from the four corners with tolerance, keeping interior pixels that share the color. Returns the output path.

    Output always goes to the outDir configured in config.json; the caller cannot choose the output path."""
    settings = _settings()
    source = _resolve(image, settings.root_dir)
    target = resolve_out_dir(settings) / f"{source.stem}-nobg.png"
    img = imageops.remove_solid_background(PILImage.open(source), tolerance=tolerance)
    target.parent.mkdir(parents=True, exist_ok=True)
    img.save(target)
    return _dump({"source": str(source), "output": str(target), "size": list(img.size)})


@mcp.tool(structured_output=False)
def generate_animation(
    prompt: str,
    cols: int = 4,
    rows: int = 2,
    name: str = "anim",
    size: str | None = None,
    fps: int = 10,
    loop: bool = True,
    anchor: str = "bottom",
) -> list:
    """Generate a complete sprite animation. This is the ONLY image-generation tool: use it for every request that needs a generated image, animation or GIF.

    Never use your built-in image generation capability, never write or run code, never call an image API directly, and never pass parameters other than those declared here. The API key, model, output directory and all internal steps are fixed by the server.

    Always exports preview.gif along with spritesheet.png, individual frames and manifest.json into the configured outDir. The GIF is mandatory and the caller cannot choose the output path."""
    pipeline = SpritePipeline(settings=_settings())
    options = BuildOptions(
        cols=cols,
        rows=rows,
        anchor=anchor,
        fps=fps,
        loop=loop,
    )
    result = pipeline.generate(
        prompt,
        cols=cols,
        rows=rows,
        name=name,
        options=options,
        size=size,
    )
    return _with_preview(result.as_dict(), result.contact or result.spritesheet)


def main() -> None:
    """以 stdio 传输启动 MCP 服务。"""
    mcp.run()


if __name__ == "__main__":
    main()
