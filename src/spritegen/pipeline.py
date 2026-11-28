"""完整流水线：生成 → 去背 → 质检 → 重试 → 拆帧对齐 → 合图导出。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

from . import imageops
from .api import ImageAPIError, ImageClient, check_reference_size, load_image
from .config import Settings, load_settings, resolve_asset_dir, safe_name
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

# 逐帧编辑时的背景要求：参考图里可能带场景，必须替换成可抠的纯白。
WHITE_BACKDROP = "a flat pure white background, no scenery, no sky, no clouds, no floor"

# 整张网格编辑模板：一次调用让模型把整张序列帧图集重画出来，成本与帧数无关。
# 刻意不写任何具体角色特征（帽子、尾巴、毛色等），否则换成别的角色时
# 这些约束会和参考图互相矛盾，导致画面被强行改成模板里描述的角色。
SHEET_EDIT_TEMPLATE = (
    "Redraw this sprite sheet with the character performing: {motion}. "
    "Keep the same grid layout as the reference image: exactly {cols} columns and {rows} rows, "
    "exactly {frames} equal cells, same cell size and same cell boundaries, "
    "one complete character centered inside each cell, full body visible in every cell, "
    "no cell left empty and no cell containing two characters. "
    "Everything else stays identical to the reference image: same art style and line quality, "
    "same character design, same face and expression details, same clothing, accessories and colors, "
    "same proportions, same lighting, same camera angle and framing. "
    "Keep the same character size and the same ground line in every cell so the frames line up. "
    "Replace the background with {backdrop}. "
    "No text, no watermark, no border, no grid lines, no shadow between cells."
)

# 整张网格重试时追加的强化提示，重点约束格子数量与空帧。
SHEET_RETRY_HINT = (
    " This time pay special attention to: keep exactly the same grid as the reference image, "
    "exactly one complete character centered in each cell, no empty cells, no extra cells, "
    "and the same character size in every cell."
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
    overlay: Path | None = None
    metrics: dict = field(default_factory=dict)
    alignment: dict = field(default_factory=dict)

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
            "overlay": str(self.overlay) if self.overlay else None,
            "issues": self.issues,
            "metrics": self.metrics,
            "alignment": self.alignment,
            "report": self.report,
        }


@dataclass
class SingleImageResult:
    """单张图片（文生图或改图）的结果。"""

    model: str
    mode: str
    size: tuple[int, int]
    transparent: bool
    images: list[Path] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "ok": not self.issues,
            "mode": self.mode,
            "model": self.model,
            "size": list(self.size),
            "transparent": self.transparent,
            "images": [str(path) for path in self.images],
            "issues": self.issues,
        }


@dataclass
class ReferenceAnimationResult:
    """以参考图为底整张重绘后的序列帧结果。"""

    model: str
    attempts: int
    reference: Path
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
    overlay: Path | None = None
    metrics: dict = field(default_factory=dict)
    alignment: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "ok": not self.issues,
            "mode": "reference",
            "model": self.model,
            "attempts": self.attempts,
            "reference": str(self.reference),
            "source": str(self.source),
            "frame_count": self.frame_count,
            "frame_size": list(self.frame_size),
            "pivot": {"x": self.pivot[0], "y": self.pivot[1]},
            "spritesheet": str(self.spritesheet),
            "manifest": str(self.manifest),
            "contact": str(self.contact) if self.contact else None,
            "gif": str(self.gif) if self.gif else None,
            "overlay": str(self.overlay) if self.overlay else None,
            "issues": self.issues,
            "metrics": self.metrics,
            "alignment": self.alignment,
            "report": self.report,
        }


class SpritePipeline:
    """把生图接口与本地图像处理串成一条可重试的流水线。"""

    def __init__(self, settings: Settings | None = None, client: object | None = None) -> None:
        self.settings = settings or load_settings()
        self.client = client or ImageClient(self.settings)
        # 网关不支持 background/output_format 参数时置位，之后不再重复试探。
        self._alpha_params_unsupported = False

    def _alpha_kwargs(self, use_alpha: bool) -> dict[str, str]:
        """透明背景除了写进提示词，再显式传接口参数，让模型真正直出 alpha 通道。

        网关不认识这两个参数时通常返回 4xx，这里捕获一次后降级为纯提示词方式，
        避免每次调用都因为同一个参数失败。"""
        if not use_alpha or self._alpha_params_unsupported:
            return {}
        return {"background": "transparent", "output_format": "png"}

    def _call_with_alpha(self, method, *args, use_alpha: bool, **kwargs):
        """调用生图接口，透明参数被网关拒绝时自动去掉参数重试一次。"""
        extra = self._alpha_kwargs(use_alpha)
        try:
            return method(*args, **extra, **kwargs)
        except ImageAPIError as exc:
            # 只有客户端错误（参数不被接受）才降级；5xx 是真故障，应当直接抛出。
            if not extra or not exc.status or not 400 <= exc.status < 500:
                raise
            self._alpha_params_unsupported = True
            return method(*args, **kwargs)

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
        size: str | None = None,
        quality: str | None = None,
        model: str | None = None,
        transparent: bool | None = None,
        max_deviation: float = 0.2,
    ) -> AnimationResult:
        """生成一张网格图，质检不合格时重试，最后输出图集产物。"""
        self.settings.require_key()
        opts = options or BuildOptions()
        opts.cols, opts.rows = cols, rows
        use_alpha = self.settings.transparent if transparent is None else transparent

        # 产物目录只取配置里的 outDir 加 name 子目录，调用方不能改根目录，
        # 同时不同作品不会互相覆盖。
        target = resolve_asset_dir(self.settings, name, "anim")
        target.mkdir(parents=True, exist_ok=True)

        best: tuple[Image.Image, GridReport, list[str], str] | None = None
        attempts = 0
        for attempt in range(max(0, retries) + 1):
            attempts = attempt + 1
            text = self.compose_prompt(prompt, cols, rows, use_alpha)
            # 重试时追加更明确的约束，引导模型修正空格与尺寸不一致问题。
            if attempt > 0:
                text += RETRY_HINT
            blobs, used_model = self._call_with_alpha(
                self.client.generate,
                text,
                use_alpha=use_alpha,
                size=size,
                quality=quality,
                model=model,
                n=1,
            )
            base = load_image(blobs[0])
            image = imageops.remove_solid_background(base) if remove_bg else base
            report = inspect_grid(image, cols, rows, thresh=opts.thresh)
            issues = quality_issues(report, max_deviation)
            # 记录问题数最少的一版，即使最终都不合格也输出其中最好的结果。
            if best is None or len(issues) < len(best[2]):
                best = (image, report, issues, used_model)
            if not issues:
                break

        assert best is not None
        image, report, issues, used_model = best
        # 保留去背后的原始网格图，方便后续追溯或重新构建。
        source = target / "source.png"
        image.save(source)

        frames, _ = split_grid(image, cols, rows, thresh=opts.thresh)
        built: BuildResult = SheetBuilder(opts).build(frames, target, name, gif=True)

        return AnimationResult(
            model=used_model,
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
            overlay=built.overlay,
            metrics=built.metrics,
            alignment=built.alignment,
        )

    @staticmethod
    def content_bbox(image: Image.Image, thresh: int = 10) -> tuple[int, int, int, int] | None:
        """按 alpha 计算单张图的内容包围盒。"""
        mask = image.getchannel("A").point(lambda v: 255 if v > thresh else 0)
        return mask.getbbox()

    def generate_from_reference(
        self,
        reference: str | Path,
        motions: list[str] | str,
        *,
        cols: int = 4,
        rows: int = 2,
        name: str = "anim",
        options: BuildOptions | None = None,
        size: str | None = None,
        quality: str | None = None,
        model: str | None = None,
        transparent: bool | None = None,
        retries: int = 1,
        max_deviation: float = 0.2,
        remove_bg: bool = True,
    ) -> ReferenceAnimationResult:
        """以参考图为底整张重绘：只调用一次接口，让模型把整张网格画出来。

        参考图既可以是单张角色图（由本方法先拼成网格再交给模型），
        也可以已经是序列帧图集；无论哪种都只发一次 /v1/images/edits，
        成本与帧数无关。
        """
        self.settings.require_key()
        source = Path(reference).expanduser().resolve()
        if not source.is_file():
            raise FileNotFoundError(f"参考图不存在：{source}")
        opts = options or BuildOptions()
        opts.cols, opts.rows = cols, rows
        target = resolve_asset_dir(self.settings, name, "anim")
        target.mkdir(parents=True, exist_ok=True)

        # 参考图先去掉背景再当底图，否则模型会把原背景一起带进每一帧。
        ref_image = Image.open(source).convert("RGBA")
        # 序列帧图集是本地拼出来的，很容易超过模型尺寸上限，必须在发请求前拦住。
        check_reference_size(ref_image.width, ref_image.height, f"参考图 {source.name} ")
        if remove_bg:
            ref_image = imageops.remove_solid_background(ref_image)
        ref_path = target / "reference.png"
        ref_image.save(ref_path)
        ref_blob = ref_path.read_bytes()

        use_alpha = self.settings.transparent if transparent is None else transparent
        backdrop = BACKDROP_TRANSPARENT if use_alpha else WHITE_BACKDROP
        # motions 既接受列表（按帧描述），也接受一句话（整体描述）。
        motion_text = self._motion_text(motions)
        prompt_body = self.compose_sheet_edit(motion_text, cols, rows, backdrop)

        best: tuple[Image.Image, GridReport, list[str], str] | None = None
        attempts = 0
        for attempt in range(max(0, retries) + 1):
            attempts = attempt + 1
            text = prompt_body if attempt == 0 else prompt_body + SHEET_RETRY_HINT
            blobs, used_model = self._call_with_alpha(
                self.client.edit,
                text,
                ref_blob,
                use_alpha=use_alpha,
                size=size,
                quality=quality,
                model=model,
            )
            base = load_image(blobs[0])
            image = imageops.remove_solid_background(base) if remove_bg else base
            report = inspect_grid(image, cols, rows, thresh=opts.thresh)
            issues = quality_issues(report, max_deviation)
            # 记录问题数最少的一版，即使都不合格也输出其中最好的结果。
            if best is None or len(issues) < len(best[2]):
                best = (image, report, issues, used_model)
            if not issues:
                break

        assert best is not None
        image, report, issues, used_model = best
        # 保留整张网格原图，方便追溯或重新切帧。
        source_path = target / "source.png"
        image.save(source_path)

        frames, _ = split_grid(image, cols, rows, thresh=opts.thresh)
        # 把参考图作为对齐基准：所有帧都贴着同一张底图，而不是相对第 1 帧漂移。
        if opts.reference_frame is None:
            opts.reference_frame = ref_image
        built: BuildResult = SheetBuilder(opts).build(frames, target, name, gif=True)
        return ReferenceAnimationResult(
            model=used_model,
            attempts=attempts,
            reference=ref_path,
            source=source_path,
            spritesheet=built.spritesheet,
            manifest=built.manifest,
            frame_count=built.frame_count,
            frame_size=built.frame_size,
            pivot=built.pivot,
            issues=issues,
            report=report.as_dict(),
            contact=built.contact,
            gif=built.gif,
            overlay=built.overlay,
            metrics=built.metrics,
            alignment=built.alignment,
        )

    @staticmethod
    def _motion_text(motions: list[str] | str) -> str:
        """把 motions 归一化成一段给模型看的动作描述。"""
        if isinstance(motions, str):
            text = motions.strip()
            if not text:
                raise ValueError("motions 不能为空，请描述要做成动画的动作")
            return text
        items = [str(item).strip().rstrip(".") for item in motions if str(item).strip()]
        if not items:
            raise ValueError("motions 不能为空，至少要给一帧的动作描述")
        # 列表形式按顺序拼成“逐格变化”，让模型知道每格该画什么。
        return "; ".join(f"cell {index + 1}: {item}" for index, item in enumerate(items))

    @staticmethod
    def compose_sheet_edit(motion: str, cols: int, rows: int, backdrop: str) -> str:
        """拼出整张网格编辑的提示词：动作在前，网格结构与一致性约束在后。"""
        return SHEET_EDIT_TEMPLATE.format(
            motion=motion.strip().rstrip("."),
            frames=cols * rows,
            cols=cols,
            rows=rows,
            backdrop=backdrop,
        )

    @staticmethod
    def _save_images(blobs: list[bytes], target: Path, stem: str, remove_bg: bool) -> list[Path]:
        """把接口返回的图片落盘；一次多张时按序号命名，避免互相覆盖。"""
        paths: list[Path] = []
        multi = len(blobs) > 1
        for index, blob in enumerate(blobs):
            image = load_image(blob)
            if remove_bg:
                image = imageops.remove_solid_background(image)
            path = target / (f"{stem}-{index + 1}.png" if multi else f"{stem}.png")
            image.save(path)
            paths.append(path)
        return paths

    def generate_image(
        self,
        prompt: str,
        *,
        name: str = "image",
        size: str | None = None,
        quality: str | None = None,
        model: str | None = None,
        n: int = 1,
        transparent: bool | None = None,
        remove_bg: bool = False,
    ) -> SingleImageResult:
        """文生图：生成独立图片，不切格、不拼图集。透明背景写进提示词并透传接口参数。"""
        self.settings.require_key()
        text = prompt.strip()
        if not text:
            raise ValueError("prompt 不能为空")
        use_alpha = self.settings.transparent if transparent is None else transparent
        if use_alpha:
            text = f"{text.rstrip('.')}. {BACKDROP_TRANSPARENT}."
        target = resolve_asset_dir(self.settings, name, "image")
        target.mkdir(parents=True, exist_ok=True)
        blobs, used_model = self._call_with_alpha(
            self.client.generate,
            text,
            use_alpha=use_alpha,
            size=size,
            quality=quality,
            model=model,
            n=max(1, min(n, 4)),
        )
        paths = self._save_images(blobs, target, safe_name(name, "image"), remove_bg)
        return SingleImageResult(
            model=used_model,
            mode="text_to_image",
            size=Image.open(paths[0]).size,
            transparent=use_alpha,
            images=paths,
            issues=self._image_issues(paths),
        )

    def edit_image(
        self,
        reference: str | Path,
        prompt: str,
        *,
        name: str = "edited",
        size: str | None = None,
        quality: str | None = None,
        model: str | None = None,
        n: int = 1,
        transparent: bool | None = None,
        remove_bg: bool = False,
    ) -> SingleImageResult:
        """改图：以参考图为底按提示词重绘独立图片，走 /v1/images/edits。"""
        self.settings.require_key()
        text = prompt.strip()
        if not text:
            raise ValueError("prompt 不能为空")
        source = Path(reference).expanduser().resolve()
        if not source.is_file():
            raise FileNotFoundError(f"参考图不存在：{source}")
        # 与序列帧共用同一套尺寸校验，超限直接报错而不是白花一次调用。
        with Image.open(source) as probe:
            check_reference_size(probe.width, probe.height, f"参考图 {source.name} ")
        use_alpha = self.settings.transparent if transparent is None else transparent
        if use_alpha:
            text = f"{text.rstrip('.')}. {BACKDROP_TRANSPARENT}."
        target = resolve_asset_dir(self.settings, name, "edited")
        target.mkdir(parents=True, exist_ok=True)
        blobs, used_model = self._call_with_alpha(
            self.client.edit,
            text,
            source.read_bytes(),
            use_alpha=use_alpha,
            size=size,
            quality=quality,
            model=model,
            n=max(1, min(n, 4)),
        )
        paths = self._save_images(blobs, target, safe_name(name, "edited"), remove_bg)
        return SingleImageResult(
            model=used_model,
            mode="edit_image",
            size=Image.open(paths[0]).size,
            transparent=use_alpha,
            images=paths,
            issues=self._image_issues(paths),
        )

    @classmethod
    def _image_issues(cls, paths: list[Path]) -> list[str]:
        """检查落盘后的图片是否整张空白。"""
        issues: list[str] = []
        for path in paths:
            image = Image.open(path).convert("RGBA")
            if cls.content_bbox(image) is None:
                issues.append(f"{path.name}：整张图没有内容")
        return issues
