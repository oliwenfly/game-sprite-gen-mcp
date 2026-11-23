"""完整流水线：生成 → 去背 → 质检 → 重试 → 拆帧对齐 → 合图导出。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

from . import imageops
from .api import ImageClient, load_image
from .config import Settings, load_settings, resolve_out_dir
from .inspect import GridReport, inspect_grid, quality_issues
from .sheet import BuildOptions, BuildResult, SheetBuilder, split_grid

# 网格模板里的背景描述：要透明通道就直接要 alpha，否则要纯色便于本地抠图。
BACKDROP_SOLID = "flat uniform single-color background"
BACKDROP_TRANSPARENT = (
    "fully transparent background with a real alpha channel, no floor, no cast shadow, "
    "no frame and clean edges without white halo"
)

PROMPT_TEMPLATE = (
    # 把网格结构、每格完整性与一致性一次性写清，背景部分按 transparent 切换，减少模型自由发挥。
    "Sprite sheet with exactly {frames} frames of the same character, "
    "arranged in a grid of {cols} columns and {rows} rows, equal cell size, "
    "full body visible in every cell, identical scale and identical style in every cell, "
    "{backdrop}, no text, no watermark, no grid lines, "
    "no shadow between cells."
)

# 首次生成不合格时追加的强化提示，重点约束空格、居中和大小一致。
RETRY_HINT = (
    " This time pay special attention to: exactly one complete character centered in each cell, "
    "no empty cells, and the same character size in every cell."
)


@dataclass
class AnimationResult:
    """一次序列帧生成的完整结果。"""

    model: str
    attempts: int
    source: Path
    spritesheet: Path
    manifest: Path
    frame_count: int
    frame_size: tuple[int, int]
    pivot: tuple[int, int]
    report: dict
    issues: list[str] = field(default_factory=list)
    contact: Path | None = None
    gif: Path | None = None
    metrics: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "ok": not self.issues,
            "model": self.model,
            "attempts": self.attempts,
            "frame_count": self.frame_count,
            "frame_size": list(self.frame_size),
            "pivot": {"x": self.pivot[0], "y": self.pivot[1]},
            "source": str(self.source),
            "spritesheet": str(self.spritesheet),
            "manifest": str(self.manifest),
            "contact": str(self.contact) if self.contact else None,
            "gif": str(self.gif) if self.gif else None,
            "issues": self.issues,
            "metrics": self.metrics,
            "report": self.report,
        }


class SpritePipeline:
    """把生图接口与本地图像处理串成一条可重试的流水线。"""

    def __init__(self, settings: Settings | None = None, client: object | None = None) -> None:
        self.settings = settings or load_settings()
        self.client = client or ImageClient(self.settings)

    @staticmethod
    def compose_prompt(prompt: str, cols: int, rows: int, transparent: bool = False) -> str:
        """在用户提示词后补上网格、一致性与背景约束，transparent 为真时改为要求直出透明通道。"""
        suffix = PROMPT_TEMPLATE.format(
            frames=cols * rows,
            cols=cols,
            rows=rows,
            backdrop=BACKDROP_TRANSPARENT if transparent else BACKDROP_SOLID,
        )
        head = prompt.strip().rstrip(".")
        return f"{head}. {suffix}" if head else suffix

    def generate(
        self,
        prompt: str,
        *,
        cols: int = 4,
        rows: int = 2,
        name: str = "anim",
        options: BuildOptions | None = None,
        retries: int = 1,
        remove_bg: bool = True,
        gif: bool = True,
        size: str | None = None,
        max_deviation: float = 0.2,
    ) -> AnimationResult:
        """生成一张网格图，质检不合格时重试，最后输出图集产物。"""
        self.settings.require_key()
        opts = options or BuildOptions()
        opts.cols, opts.rows = cols, rows

        # 产物目录只取配置里的 outDir，调用方不能改。
        target = resolve_out_dir(self.settings)
        target.mkdir(parents=True, exist_ok=True)

        best: tuple[Image.Image, GridReport, list[str], str] | None = None
        attempts = 0
        for attempt in range(max(0, retries) + 1):
            attempts = attempt + 1
            text = self.compose_prompt(prompt, cols, rows, self.settings.transparent)
            # 重试时追加更明确的约束，引导模型修正空格与尺寸不一致问题。
            if attempt > 0:
                text += RETRY_HINT
            blobs, model = self.client.generate(text, size=size, n=1)
            base = load_image(blobs[0])
            image = imageops.remove_solid_background(base) if remove_bg else base
            report = inspect_grid(image, cols, rows, thresh=opts.thresh)
            issues = quality_issues(report, max_deviation)
            # 记录问题数最少的一版，即使最终都不合格也输出其中最好的结果。
            if best is None or len(issues) < len(best[2]):
                best = (image, report, issues, model)
            if not issues:
                break

        assert best is not None
        image, report, issues, model = best
        # 保留去背后的原始网格图，方便后续追溯或重新构建。
        source = target / "source.png"
        image.save(source)

        frames, _ = split_grid(image, cols, rows)
        built: BuildResult = SheetBuilder(opts).build(frames, target, name, gif=gif)

        return AnimationResult(
            model=model,
            attempts=attempts,
            source=source,
            spritesheet=built.spritesheet,
            manifest=built.manifest,
            frame_count=built.frame_count,
            frame_size=built.frame_size,
            pivot=built.pivot,
            report=report.as_dict(),
            issues=issues,
            contact=built.contact,
            gif=built.gif,
            metrics=built.metrics,
        )
