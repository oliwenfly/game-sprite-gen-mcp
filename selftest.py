"""离线冒烟测试：用假客户端跑通四条链路，不产生任何生图调用与费用。

用法：python selftest.py
覆盖：文生单图、改单图、文生序列帧、改序列帧，以及 name 分目录、路径穿越防护、
透明参数透传与网关拒绝后的降级、raw 模式尺寸不一致的补齐。
"""

from __future__ import annotations

import io
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from PIL import Image, ImageDraw

from spritegen.api import ImageAPIError, ReferenceTooLargeError
from spritegen.config import load_settings, resolve_asset_dir, safe_name
from spritegen.pipeline import SpritePipeline
from spritegen.sheet import BuildOptions


def solid_png(size: int = 256, color: tuple[int, int, int] = (255, 255, 255)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (size, size), color).save(buf, format="PNG")
    return buf.getvalue()


def blob_png(size: int = 256) -> bytes:
    """白底画一个深色圆，去背与包围盒检测都能正常工作。"""
    img = Image.new("RGB", (size, size), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    draw.ellipse((size // 4, size // 5, size * 3 // 4, size * 4 // 5), fill=(40, 40, 40))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def grid_png(cols: int, rows: int, size: int = 512) -> bytes:
    """网格图：每格一个深色圆，供拆帧质检使用。"""
    img = Image.new("RGB", (size, size), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    cw, ch = size // cols, size // rows
    for row in range(rows):
        for col in range(cols):
            cx, cy = col * cw + cw // 2, row * ch + ch // 2
            r = min(cw, ch) // 3
            draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(40, 40, 40))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class FakeClient:
    """假网关：记录调用参数，返回可控图片，绝不发起网络请求。"""

    def __init__(self, cols: int = 1, rows: int = 1, reject_alpha: bool = False) -> None:
        self.cols, self.rows = cols, rows
        self.reject_alpha = reject_alpha
        self.calls: list[dict] = []

    def _reply(self, kwargs: dict, count: int) -> list[bytes]:
        if self.cols > 1 or self.rows > 1:
            return [grid_png(self.cols, self.rows)]
        return [blob_png() for _ in range(count)]

    def _guard(self, kwargs: dict) -> None:
        # 模拟不支持 background/output_format 的网关：带这两个参数就返回 400。
        if self.reject_alpha and ("background" in kwargs or "output_format" in kwargs):
            raise ImageAPIError("unsupported parameter: background", status=400)

    def generate(self, prompt: str, **kwargs: object) -> tuple[list[bytes], str]:
        self.calls.append({"endpoint": "generations", "prompt": prompt, **kwargs})
        self._guard(kwargs)
        return self._reply(kwargs, int(kwargs.get("n") or 1)), "fake-model"

    def edit(self, prompt: str, images: object, **kwargs: object) -> tuple[list[bytes], str]:
        self.calls.append({"endpoint": "edits", "prompt": prompt, **kwargs})
        self._guard(kwargs)
        return self._reply(kwargs, int(kwargs.get("n") or 1)), "fake-model"


class Checker:
    """极简断言收集器，失败不中断，最后统一汇报。"""

    def __init__(self) -> None:
        self.failures: list[str] = []

    def check(self, label: str, condition: bool, detail: str = "") -> None:
        if condition:
            print(f"  PASS  {label}")
        else:
            print(f"  FAIL  {label} {detail}")
            self.failures.append(label)

    def report(self) -> int:
        print()
        if self.failures:
            print(f"共 {len(self.failures)} 项失败：")
            for item in self.failures:
                print(f"  - {item}")
            return 1
        print("全部通过")
        return 0


def main() -> int:
    base = load_settings()
    checker = Checker()

    print("[1] 文生单图")
    out = Path(tempfile.mkdtemp(prefix="spritegen_t2i_"))
    client = FakeClient()
    pipeline = SpritePipeline(settings=replace(base, out_dir=str(out), transparent=False), client=client)
    single = pipeline.generate_image("a fox icon", name="icon_fox")
    checker.check("产物落在 outDir/name 子目录", Path(single.images[0]).parent == out / "icon_fox",
                  str(single.images[0]))
    checker.check("文件名正确", Path(single.images[0]).name == "icon_fox.png")
    checker.check("mode 正确", single.mode == "text_to_image")
    checker.check("无问题报告", single.issues == [])

    print("[2] 文生单图 n>1 与透明参数")
    out2 = Path(tempfile.mkdtemp(prefix="spritegen_t2i_multi_"))
    client2 = FakeClient()
    p2 = SpritePipeline(settings=replace(base, out_dir=str(out2), transparent=True), client=client2)
    multi = p2.generate_image("a fox icon", name="icon", n=3)
    names = sorted(Path(p).name for p in multi.images)
    checker.check("多张按序号命名", names == ["icon-1.png", "icon-2.png", "icon-3.png"], str(names))
    checker.check("n 已透传", client2.calls[0].get("n") == 3)
    checker.check("透明参数已透传接口", client2.calls[0].get("background") == "transparent",
                  str(client2.calls[0].get("background")))
    checker.check("输出格式为 png", client2.calls[0].get("output_format") == "png")

    print("[3] 改单图")
    out3 = Path(tempfile.mkdtemp(prefix="spritegen_edit_"))
    ref = out3 / "ref.png"
    ref.write_bytes(blob_png())
    client3 = FakeClient()
    p3 = SpritePipeline(settings=replace(base, out_dir=str(out3), transparent=False), client=client3)
    edited = p3.edit_image(ref, "make it blue", name="recolor")
    checker.check("走 edits 端点", client3.calls[0]["endpoint"] == "edits")
    checker.check("产物在 outDir/name", Path(edited.images[0]).parent == out3 / "recolor")

    print("[4] 网关拒绝透明参数时自动降级")
    out4 = Path(tempfile.mkdtemp(prefix="spritegen_fallback_"))
    client4 = FakeClient(reject_alpha=True)
    p4 = SpritePipeline(settings=replace(base, out_dir=str(out4), transparent=True), client=client4)
    fallback = p4.generate_image("a fox icon", name="fallback")
    checker.check("降级后仍然成功", len(fallback.images) == 1)
    checker.check("降级调用不带透明参数", "background" not in client4.calls[-1])
    checker.check("已记住网关不支持", p4._alpha_params_unsupported is True)

    print("[5] 文生序列帧")
    out5 = Path(tempfile.mkdtemp(prefix="spritegen_anim_"))
    client5 = FakeClient(cols=4, rows=2)
    p5 = SpritePipeline(settings=replace(base, out_dir=str(out5), transparent=False), client=client5)
    anim = p5.generate("fox run", cols=4, rows=2, name="fox_run",
                       options=BuildOptions(cols=4, rows=2, anchor="bottom", fps=10))
    anim_dir = out5 / "fox_run"
    checker.check("帧数为 4x2", anim.frame_count == 8, str(anim.frame_count))
    checker.check("产物在 outDir/name", anim.spritesheet.parent == anim_dir)
    for expected in ("spritesheet.png", "manifest.json", "contact.png", "preview.gif", "source.png"):
        checker.check(f"导出 {expected}", (anim_dir / expected).is_file())

    print("[6] 改序列帧（整张一次调用）")
    out6 = Path(tempfile.mkdtemp(prefix="spritegen_ref_"))
    ref6 = out6 / "ref.png"
    ref6.write_bytes(blob_png())
    client6 = FakeClient(cols=4, rows=2)
    p6 = SpritePipeline(settings=replace(base, out_dir=str(out6), transparent=False), client=client6)
    refanim = p6.generate_from_reference(
        ref6, "the fox waves both paws", cols=4, rows=2, name="fox_wave",
        options=BuildOptions(cols=4, rows=2, anchor="bottom", fps=12),
    )
    ref_dir = out6 / "fox_wave"
    checker.check("只发 1 次接口调用", len(client6.calls) == 1, str(len(client6.calls)))
    checker.check("走 edits 端点", client6.calls[0]["endpoint"] == "edits")
    checker.check("帧数来自网格而非调用次数", refanim.frame_count == 8, str(refanim.frame_count))
    checker.check("产物在 outDir/name", refanim.spritesheet.parent == ref_dir)
    checker.check("导出 reference.png", (ref_dir / "reference.png").is_file())
    checker.check("导出整张 source.png", (ref_dir / "source.png").is_file())
    checker.check("不再有逐帧 frame-src", not list(ref_dir.glob("frame-src-*.png")))
    checker.check("导出 overlay.png", (ref_dir / "overlay.png").is_file())
    checker.check("对齐报告存在", refanim.alignment.get("verdict") is not None)

    print("[6b] 网格不合格时整张重试")
    out6b = Path(tempfile.mkdtemp(prefix="spritegen_retry_"))
    ref6b = out6b / "ref.png"
    ref6b.write_bytes(blob_png())

    class BadGridClient:
        """第一次返回空白网格（全部格子为空），第二次返回合格网格。"""

        def __init__(self) -> None:
            self.calls = 0

        def edit(self, prompt: str, images: object, **kwargs: object):
            self.calls += 1
            if self.calls == 1:
                return [solid_png(512)], "fake-model"
            return [grid_png(4, 2)], "fake-model"

    bad = BadGridClient()
    p6b = SpritePipeline(settings=replace(base, out_dir=str(out6b), transparent=False), client=bad)
    retried = p6b.generate_from_reference(
        ref6b, "the fox jumps", cols=4, rows=2, name="retry", retries=1,
        options=BuildOptions(cols=4, rows=2, anchor="bottom"),
    )
    checker.check("失败后重试了一次", bad.calls == 2, str(bad.calls))
    checker.check("重试次数记录正确", retried.attempts == 2, str(retried.attempts))
    checker.check("最终采用合格版本", retried.issues == [], str(retried.issues))

    print("[7] 不同 name 互不覆盖")
    out7 = Path(tempfile.mkdtemp(prefix="spritegen_collide_"))
    client7 = FakeClient(cols=2, rows=2)
    p7 = SpritePipeline(settings=replace(base, out_dir=str(out7), transparent=False), client=client7)
    p7.generate("fox run", cols=2, rows=2, name="fox_run")
    run_manifest = (out7 / "fox_run" / "manifest.json").read_text(encoding="utf-8")
    p7.generate("fox idle", cols=2, rows=2, name="fox_idle")
    idle_manifest = (out7 / "fox_idle" / "manifest.json").read_text(encoding="utf-8")
    checker.check("两份 manifest 都存在", run_manifest != idle_manifest)
    checker.check("fox_run 目录仍在", (out7 / "fox_run" / "spritesheet.png").is_file())
    checker.check("fox_idle 目录独立", (out7 / "fox_idle" / "spritesheet.png").is_file())

    print("[8] 名称安全与路径穿越")
    checker.check("../evil 不逃出 outDir", safe_name("../evil") == "-evil", safe_name("../evil"))
    checker.check("a/b 被折叠", safe_name("a/b") == "a-b", safe_name("a/b"))
    checker.check("空名走 fallback", safe_name("   ", "anim") == "anim")
    checker.check("Windows 保留名被改写", safe_name("con") == "con-asset")
    escaped = resolve_asset_dir(replace(base, out_dir=str(out7)), "../../etc")
    checker.check("resolve_asset_dir 仍在 outDir 内", out7.resolve() in escaped.resolve().parents,
                  str(escaped))

    print("[9] raw 模式帧尺寸不一致")
    out9 = Path(tempfile.mkdtemp(prefix="spritegen_raw_"))
    from spritegen.sheet import SheetBuilder

    sizes = [(64, 64), (48, 96), (80, 40)]
    frames = [Image.new("RGBA", size, (255, 0, 0, 255)) for size in sizes]
    built = SheetBuilder(BuildOptions(raw=True, gutter=2)).build(frames, out9, "rawtest", gif=True)
    checker.check("画布取最大尺寸", built.frame_size == (80, 96), str(built.frame_size))
    checker.check("导出帧尺寸统一", all(Image.open(p).size == (80, 96) for p in built.frames))
    checker.check("导出瓦片图", built.spritesheet.is_file())

    print("[9b] 超限参考图在发请求前被拦下")
    out9b = Path(tempfile.mkdtemp(prefix="spritegen_big_"))
    huge = out9b / "huge_sheet.png"
    # 8 帧 × 1024px = 8192 宽，模拟本地拼出来的超宽序列帧图集。
    Image.new("RGBA", (8192, 1024), (255, 255, 255, 255)).save(huge)

    class NoCallClient:
        """任何调用都算失败：超限图根本不该发出去。"""

        def __init__(self) -> None:
            self.calls = 0

        def edit(self, *args: object, **kwargs: object):
            self.calls += 1
            raise AssertionError("超限参考图不应该发起请求")

        def generate(self, *args: object, **kwargs: object):
            self.calls += 1
            raise AssertionError("超限参考图不应该发起请求")

    spy = NoCallClient()
    p9b = SpritePipeline(settings=replace(base, out_dir=str(out9b / "out")), client=spy)
    try:
        p9b.generate_from_reference(huge, "the fox jumps", cols=8, rows=1, name="big")
        checker.check("超限图应报错", False, "没有抛异常")
    except ReferenceTooLargeError:
        checker.check("超限图报 ReferenceTooLargeError", True)
    checker.check("没有发出任何请求", spy.calls == 0, str(spy.calls))

    try:
        p9b.edit_image(huge, "recolor")
        checker.check("edit_image 应报错", False, "没有抛异常")
    except ReferenceTooLargeError:
        checker.check("edit_image 同样拦截", True)
    checker.check("仍然没有发出请求", spy.calls == 0, str(spy.calls))

    print("[10] 提示词不含角色专属描述")
    import re

    from spritegen.pipeline import SHEET_EDIT_TEMPLATE

    lowered = SHEET_EDIT_TEMPLATE.lower()
    # 用单词边界匹配，避免 "details" 里的 "tail" 造成误报。
    for word in ("aviator", "goggles", "jacket", "scarf", "fur", "tail", "paw", "arms"):
        checker.check(f"模板不含 {word}", re.search(rf"\b{word}\b", lowered) is None)

    return checker.report()


if __name__ == "__main__":
    raise SystemExit(main())
