"""OpenAI 兼容的图片生成接口客户端。"""

from __future__ import annotations

import base64
import io

import httpx
from PIL import Image

from .config import Settings

# 部分第三方网关会拦截默认的 python-httpx UA，这里伪装成浏览器规避 403。
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"

# 网关只认标准图片类型，按真实字节判断后再标 multipart，避免把 jpg 标成 png。
_FORMAT_MIME = {
    "PNG": ("png", "image/png"),
    "JPEG": ("jpg", "image/jpeg"),
    "WEBP": ("webp", "image/webp"),
}


class ImageAPIError(RuntimeError):
    """生图接口返回错误，status 为 HTTP 状态码（本地错误时为 None）。"""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class ReferenceTooLargeError(ImageAPIError):
    """参考图超出模型尺寸限制，必须在发请求前拦下来，不能靠网关报错。"""


# 主流生图接口对单边和总像素都有上限，超出会直接 400 或静默裁切。
# 这里取保守值：单边 4096、总像素 4194304（2048×2048）。
MAX_REFERENCE_SIDE = 4096
MAX_REFERENCE_PIXELS = 2048 * 2048


def check_reference_size(width: int, height: int, label: str = "参考图") -> None:
    """校验参考图尺寸，超限时抛出带明确修复建议的异常。

    序列帧图集是本地拼接出来的，很容易超过模型上限（例如 8 帧 × 1024px 宽），
    这类请求发出去只会浪费一次调用，所以必须在上传前拦住。
    """
    pixels = width * height
    if width <= MAX_REFERENCE_SIDE and height <= MAX_REFERENCE_SIDE and pixels <= MAX_REFERENCE_PIXELS:
        return
    raise ReferenceTooLargeError(
        f"{label}尺寸 {width}×{height}（{pixels} 像素）超出模型限制："
        f"单边不得超过 {MAX_REFERENCE_SIDE}px，总像素不得超过 {MAX_REFERENCE_PIXELS}。"
        "序列帧图集是本地拼出来的，帧数或单帧尺寸过大时很容易超限。"
        "请减少 cols/rows、降低单帧尺寸后重新生成，或直接对单张角色图调用本工具（不要传已拼好的图集）。"
    )


def sniff_image(data: bytes) -> tuple[str, str]:
    """按字节判断图片真实格式，返回 (扩展名, MIME)；无法识别时按 PNG 处理。"""
    try:
        with Image.open(io.BytesIO(data)) as probe:
            fmt = (probe.format or "").upper()
    except Exception:  # noqa: BLE001 - 识别失败时走保守默认值
        return "png", "image/png"
    return _FORMAT_MIME.get(fmt, ("png", "image/png"))


def _decode_item(item: dict) -> bytes:
    """把接口返回项还原成图片字节。"""
    if item.get("b64_json"):
        return base64.b64decode(item["b64_json"])
    url = item.get("url")
    if not url:
        raise ImageAPIError("返回项既没有 b64_json 也没有 url")
    resp = httpx.get(url, headers={"User-Agent": USER_AGENT}, timeout=120, follow_redirects=True)
    resp.raise_for_status()
    return resp.content


class ImageClient:
    """极简客户端，覆盖 /v1/images/generations（文生图）与 /v1/images/edits（图生图）。"""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def _auth_headers(self) -> dict[str, str]:
        """鉴权与 UA 头；multipart 请求的 Content-Type 交给 httpx 自己补。"""
        return {
            "Authorization": f"Bearer {self.settings.api_key}",
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        }

    def _json_headers(self) -> dict[str, str]:
        """JSON 请求头。"""
        return {**self._auth_headers(), "Content-Type": "application/json"}

    def _timeout(self) -> httpx.Timeout:
        return httpx.Timeout(self.settings.timeout_s)

    def generate(
        self,
        prompt: str,
        *,
        size: str | None = None,
        quality: str | None = None,
        model: str | None = None,
        n: int = 1,
        background: str | None = None,
        output_format: str | None = None,
    ) -> tuple[list[bytes], str]:
        """文生图，返回图片字节列表与模型名。

        透明背景由流水线在提示词里要求模型直出，这里不按 settings.transparent 改接口参数；
        background / output_format 只在调用方显式传入时透传。"""
        target_model = model or self.settings.model
        payload: dict[str, object] = {
            "model": target_model,
            "prompt": prompt,
            "quality": quality or self.settings.quality,
        }
        if size:
            payload["size"] = size
        if n > 1:
            payload["n"] = n
        if background:
            payload["background"] = background
            # 透明背景只有 PNG 支持 alpha，显式要透明时补上输出格式。
            if background == "transparent" and not output_format:
                output_format = "png"
        if output_format:
            payload["output_format"] = output_format

        endpoint = f"{self.settings.api_base}/v1/images/generations"
        with httpx.Client(timeout=self._timeout(), follow_redirects=True) as client:
            resp = client.post(endpoint, json=payload, headers=self._json_headers())
        return self._collect(resp, endpoint, target_model)

    def edit(
        self,
        prompt: str,
        images: bytes | list[bytes],
        *,
        size: str | None = None,
        quality: str | None = None,
        model: str | None = None,
        n: int = 1,
        background: str | None = None,
        output_format: str | None = None,
    ) -> tuple[list[bytes], str]:
        """图生图，以参考图为底按提示词重绘，走 /v1/images/edits，返回图片字节列表与模型名。"""
        blobs = [images] if isinstance(images, bytes) else list(images)
        if not blobs:
            raise ImageAPIError("图生图至少需要一张参考图")
        target_model = model or self.settings.model
        payload: dict[str, str] = {
            "model": target_model,
            "prompt": prompt,
            "quality": quality or self.settings.quality,
        }
        if size:
            payload["size"] = size
        if n > 1:
            payload["n"] = str(n)
        if background:
            payload["background"] = background
            # 透明背景只有 PNG 支持 alpha，显式要透明时补上输出格式。
            if background == "transparent" and not output_format:
                output_format = "png"
        if output_format:
            payload["output_format"] = output_format
        # 单图字段名是 image，多图按 OpenAI 约定用 image[] 数组。
        field = "image" if len(blobs) == 1 else "image[]"
        files = []
        for index, blob in enumerate(blobs):
            # 按真实字节标 MIME 与扩展名，严格的网关不会因为格式不符拒绝请求。
            ext, mime = sniff_image(blob)
            files.append((field, (f"reference-{index}.{ext}", blob, mime)))
        endpoint = f"{self.settings.api_base}/v1/images/edits"
        with httpx.Client(timeout=self._timeout(), follow_redirects=True) as client:
            resp = client.post(endpoint, data=payload, files=files, headers=self._auth_headers())
        return self._collect(resp, endpoint, target_model)

    @staticmethod
    def _collect(resp: httpx.Response, endpoint: str, model: str) -> tuple[list[bytes], str]:
        """校验响应并提取图片字节。"""
        if resp.status_code in (404, 405):
            body = resp.text[:500] or "(空响应)"
            raise ImageAPIError(
                f"端点 {endpoint} 不存在或不支持该方法（{resp.status_code}）：{body}；"
                "当前网关可能没有开放这个接口（例如缺少 /v1/images/edits）",
                status=resp.status_code,
            )
        if resp.status_code >= 400:
            body = resp.text[:500] or "(空响应)"
            raise ImageAPIError(
                f"接口返回 {resp.status_code}：{body}；端点 {endpoint}，模型 {model}",
                status=resp.status_code,
            )
        try:
            data = resp.json()
        except ValueError as exc:
            raise ImageAPIError(f"接口返回的不是 JSON：{resp.text[:300]}", status=resp.status_code) from exc
        items = data.get("data") or []
        if not items:
            raise ImageAPIError(f"接口没有返回图片：{str(data)[:400]}", status=resp.status_code)
        return [_decode_item(item) for item in items], str(data.get("model") or model)


def load_image(data: bytes) -> Image.Image:
    """把图片字节解码为 RGBA 图像。"""
    return Image.open(io.BytesIO(data)).convert("RGBA")
