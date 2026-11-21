# game-sprite-gen-mcp

用任意 OpenAI 兼容的生图接口，从一句提示词直接产出可用的**序列帧动画素材**：自动生成网格序列帧、去背、逐格质检、不合格自动重试，最后输出对齐好的瓦片图、单帧、manifest 与 GIF 预览。

同时提供 **MCP server** 和 **CLI**，既可以让 AI 客户端直接调用，也可以当普通命令行工具用。

## 处理流程

```
提示词 → 生图（N×M 网格） → 去背（出图自带透明时自动跳过） → 逐格质检
                                   ├─ 通过 → 拆帧 → 统一缩放 → 锚点对齐 → 锁轮廓 → 调色板 → 合图导出
                                   └─ 不通过 → 自动重试（最多 retries 次）
```

## 安装

```bash
git clone <repo>
cd game-sprite-gen-mcp
pip install -e .
```

安装后得到两个命令：`spritegen`（CLI）与 `spritegen-mcp`（MCP server）。

## 配置

复制示例并填写：

```bash
cp config.example.json config.json
```

```json
{
  "baseUrl": "https://api.openai.com",
  "model": "gpt-image-1",
  "editModel": "gpt-image-1",
  "quality": "auto",
  "apiKey": "sk-...",
  "outDir": "generated/sprites",
  "timeoutMs": 180000,
  "transparent": false
}
```

优先级：**环境变量 > 配置文件 > 默认值**。配置文件按 `SPRITEGEN_CONFIG` → `./spritegen.json` → `./config.json` 顺序查找。

| 环境变量 | 说明 |
| --- | --- |
| `SPRITEGEN_API_KEY` | 生图 API Key |
| `SPRITEGEN_BASE_URL` | 接口根地址，如 `https://api.openai.com` |
| `SPRITEGEN_MODEL` | 生图模型 |
| `SPRITEGEN_EDIT_MODEL` | 改图模型 |
| `SPRITEGEN_QUALITY` | 质量参数，如 `low` / `medium` / `high` / `auto` |
| `SPRITEGEN_OUT_DIR` | 未指定 `--out` 时的输出子目录 |
| `SPRITEGEN_ROOT` | 相对路径的基准目录，默认为当前目录 |
| `SPRITEGEN_TIMEOUT_MS` | 单次请求超时，默认 180000 |
| `SPRITEGEN_TRANSPARENT` | 模型是否支持直接生成透明 PNG，`true` / `false` |

`transparent` 开启后，请求会带上 `background: "transparent"`（并强制 `output_format: "png"`），模型直接返回带 alpha 的图，本地去背自动跳过。gpt-image-2.5 及之后的模型支持；旧模型或第三方网关可能不支持该参数，会直接报错，此时保持 `false` 用本地去背兜底。

`config.json` 已在 `.gitignore` 中，不会被提交。

## 作为 MCP server 使用

```json
{
  "mcpServers": {
    "spritegen": {
      "command": "spritegen-mcp",
      "env": {
        "SPRITEGEN_API_KEY": "sk-...",
        "SPRITEGEN_BASE_URL": "https://api.openai.com",
        "SPRITEGEN_MODEL": "gpt-image-1"
      }
    }
  }
}
```

### 可用工具

| 工具 | 说明 |
| --- | --- |
| `generate_animation` | **一步到位**：生图 → 去背 → 质检 → 重试 → 合图，返回瓦片图与 manifest |
| `build_spritesheet` | 把已有网格图构建成对齐图集（不调生图接口） |
| `inspect_spritesheet` | 逐格质检，返回 bbox、尺寸、中心、空帧与偏差 JSON |
| `remove_background` | 去纯色背景（四角洪水填充，保留内部同色像素） |
| `generate_image` | 纯生图，返回文件路径与尺寸 |
| `edit_image` | 以本地图片为参考图改图，适合保持角色一致 |

工具返回文本 JSON，同时附带联系图或瓦片图的预览，客户端可直接看到结果。

## CLI

```bash
# 一步生成：4×2 = 8 帧，带质检重试
spritegen generate "a small fox running, flat cartoon style" \
  --cols 4 --rows 2 --name fox_run --out out/fox_run --retries 1 --fps 8

# 只处理已有网格图
spritegen build work/sheet.png --cols 2 --rows 2 --out out/fox --name fox --fps 8

# 质检
spritegen inspect out/fox/spritesheet.png --cols 8 --rows 1 --json

# 去背 / 裁边 / 无缝图
spritegen remove-bg raw.png --tolerance 18
spritegen trim raw.png tight.png --pad 4 --max-side 512
spritegen seamless bg.png tiled.png --w 1920 --h 1080 --top 120
```

`generate` 常用参数：

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `--cols` / `--rows` | 4 / 2 | 网格列数与行数，帧数 = 两者乘积 |
| `--retries` | 1 | 质检不通过时的重试次数（**每次重试都会真实计费**） |
| `--anchor` | bottom | `bottom` 脚底对齐，`center` 包围盒中心对齐 |
| `--target-h` | 0 | 统一缩放目标高度，0 表示按最大包围盒自适应 |
| `--palette` | 0 | 统一调色板颜色数，0 表示不量化 |
| `--gutter` | 4 | 瓦片图帧间隔，避免采样泄漏 |
| `--keep-bg` | 关 | 保留原背景，不去背 |
| `--no-gif` | 关 | 不导出 GIF 预览 |
| `--json` | 关 | 以 JSON 输出完整结果 |

## 输出产物

```
out/fox_run/
  source.png       去背后的原始网格图
  frame-0.png ...  对齐后的单帧
  spritesheet.png  横向瓦片图
  contact.png      4 列联系图，便于目视检查
  preview.gif      GIF 预览（透明区域压白底）
  manifest.json    引擎无关的图集描述
```

`manifest.json`：

```json
{
  "name": "fox_run",
  "image": "spritesheet.png",
  "frame_count": 8,
  "frame_width": 256,
  "frame_height": 256,
  "image_width": 2048,
  "image_height": 264,
  "gutter": 4,
  "pivot": { "x": 128, "y": 256 },
  "fps": 8,
  "loop": true,
  "anchor": "bottom",
  "frames": [{ "index": 0, "x": 4, "y": 4, "w": 256, "h": 256 }]
}
```

`pivot` 是脚底中心，可直接作为 Godot `AtlasTexture`、Unity Sprite 或自研引擎的锚点。

## 质检与重试

每格计算 alpha 包围盒，判定三类问题：

- **空帧**：某格没有不透明像素
- **尺寸偏差**：帧宽/帧高的 `(max-min)/中位数` 超过 `--max-deviation`（默认 20%）
- **背景未去除**：内容包围盒铺满整格（说明去背失败，通常伴随整格边缘接触）

不通过时会把「一格一角色、不要空帧、尺寸一致」的强调追加到提示词后重试。取所有尝试中问题最少的一张，所以重试不会让结果变差。

## 已知限制

- 帧间一致性完全依赖生图模型。复杂动作、多帧序列仍可能需要在 `edit_image` 里逐帧修正。
- 去背是纯色键控：渐变背景、带阴影的背景无法完全去除，可先把背景提示词强调为「flat uniform single-color」。模型支持透明输出时（`transparent: true`）不会走这一步。
- 透明通道支持取决于网关。支持 `background: "transparent"` 的模型可以直接出透明图，本项目的本地去背作为兜底。
- GIF 预览会把透明区域压成白色，仅用于检查，不是最终交付格式。

## License

MIT，见 [LICENSE](LICENSE)。
