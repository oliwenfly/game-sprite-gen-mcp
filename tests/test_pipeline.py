"""端到端流水线测试，用假客户端替代真实生图接口。"""

import io
from pathlib import Path

from PIL import Image, ImageDraw

from spritegen.config import Settings
from spritegen.pipeline import SpritePipeline


class StubClient:
    """返回一张白底双格网格图，模拟生图接口。"""

    def __init__(self, empty_second_cell: bool = False) -> None:
        self.calls = 0
        self.empty_second_cell = empty_second_cell

    def generate(self, prompt: str, *, size=None, n=1, background=None):
        self.calls += 1
        img = Image.new("RGB", (200, 100), "white")
        draw = ImageDraw.Draw(img)
        draw.ellipse((20, 20, 80, 80), fill="red")
        if not self.empty_second_cell:
            draw.ellipse((120, 20, 180, 80), fill="red")
        buffer = io.BytesIO()
        img.save(buffer, format="PNG")
        return [buffer.getvalue()], "stub-model"


def make_settings(tmp_path: Path) -> Settings:
    return Settings(
        api_key="test",
        base_url="https://example.invalid",
        model="stub",
        edit_model="stub",
        quality="auto",
        out_dir="out",
        root_dir=tmp_path,
        timeout_s=5,
    )


def test_generate_writes_all_artifacts(tmp_path: Path) -> None:
    pipeline = SpritePipeline(settings=make_settings(tmp_path), client=StubClient())
    result = pipeline.generate("a red ball", cols=2, rows=1, out_dir=tmp_path / "out", name="ball", retries=0)

    assert result.frame_count == 2
    assert result.issues == []
    for path in (result.source, result.spritesheet, result.manifest, result.contact, result.gif):
        assert path is not None and Path(path).is_file()
    assert Path(result.manifest).read_text(encoding="utf-8").count('"index"') == 2


def test_generate_retries_when_cell_is_empty(tmp_path: Path) -> None:
    client = StubClient(empty_second_cell=True)
    pipeline = SpritePipeline(settings=make_settings(tmp_path), client=client)
    result = pipeline.generate(
        "a red ball", cols=2, rows=1, out_dir=tmp_path / "out", name="ball", retries=1
    )

    assert client.calls == 2
    assert result.attempts == 2
    assert result.issues
