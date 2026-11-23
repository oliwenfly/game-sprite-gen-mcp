# game-sprite-gen-mcp

一句提示词，直接生成可用的**序列帧动画素材**：自动出网格图、去背、逐格质检、不合格重试，最后导出对齐好的瓦片图、单帧、manifest 和 GIF 预览。通过 MCP 让 AI 客户端直接调用，不用你自己启动任何服务。

![fox_hit 序列帧瓦片图](example-fox-hit.png)

## 3 步开始用

> **注意：全文示例路径 `D:/game-sprite-gen-mcp` 只是举例，不是固定值。请把它替换成你自己实际放源码的目录，其他地方不用改。**
>
> **另一个硬性前提：本机必须已经安装 Python 3.10+**，详见第 1 步。

### 1. 下载源码

下载或解压到任意目录，例如 `D:/game-sprite-gen-mcp`。

> **硬性前提：本机必须已安装 Python 3.10+。** 源码不带 Python，入口脚本只会自动安装依赖包，不会安装 Python 本身。没装的话先去 https://www.python.org/downloads/ 下载安装，安装时勾选 **Add Python to PATH**。装完在命令行运行 `python --version`（macOS / Linux 用 `python3 --version`），能显示 3.10 以上的版本号就可以继续。

> **记住这个路径**，第 3 步的 MCP 配置里要原样填上它。

### 2. 修改配置

源码目录下如果没有 `config.json`，新建一个（内容参考 `config.example.json`），或者直接把 `config.example.json` 改名为 `config.json`，然后按下面说明修改。必填的只有 `apiKey`，其余字段都有默认值。

```json
{
  "baseUrl": "https://api.openai.com",
  "model": "gpt-image-2.5",
  "quality": "auto",
  "apiKey": "sk-你的Key",
  "outDir": "generated/sprites",
  "timeoutMs": 180000,
  "transparent": false
}
```

| 字段 | 默认值 | 说明 |
| --- | --- | --- |
| `apiKey` | 空 | 生图 API Key，必填 |
| `baseUrl` | `https://api.openai.com` | 接口根地址，支持任意 OpenAI 兼容的生图网关 |
| `model` | `gpt-image-2.5` | 生图模型 |
| `quality` | `auto` | 质量参数，如 `low` / `medium` / `high` / `auto` |
| `outDir` | `generated/sprites` | 产物目录，相对路径以源码目录为基准。**这是唯一的产物路径来源**，工具参数里没有输出路径入口 |
| `timeoutMs` | `180000` | 单次请求超时，单位毫秒 |
| `transparent` | `false` | 是否要求模型直接产出带 alpha 通道的图，详见下方说明 |

`transparent: true` 不改任何接口参数，而是把透明背景要求**写进提示词**（`config.TRANSPARENT_PROMPT`），让模型自己出带透明通道的 PNG；网格模板里的“纯色背景”也会相应换成透明背景描述。拿到的图若已带 alpha，本地去背会自动跳过；模型仍出白底时，去背兜底照常生效。

也可以用环境变量临时覆盖：`SPRITEGEN_API_KEY`、`SPRITEGEN_BASE_URL`、`SPRITEGEN_MODEL`、`SPRITEGEN_QUALITY`、`SPRITEGEN_TIMEOUT_MS`、`SPRITEGEN_TRANSPARENT`。一般不用配。

`config.json` 在 `.gitignore` 里，不会被提交。

### 3. 把这段粘到 AI 客户端的 MCP 配置里

```json
{
  "mcpServers": {
    "spritegen": {
      "command": "python",
      "args": ["D:/game-sprite-gen-mcp/mcp_server.py"]
    }
  }
}
```

**只需要改 `args` 里的路径**：把示例中的 `D:/game-sprite-gen-mcp` 换成你第 1 步实际放源码的目录，其他原样照抄。

- Windows：`command` 写 `python`
- macOS / Linux：`command` 写 `python3`

重启 AI 客户端，然后直接对 AI 说：

> 用 spritegen 生成 4 帧狐狸奔跑的序列帧，透明背景，名字叫 fox_run

第一次启动时入口脚本会自动安装 `mcp`、`pillow`、`httpx` 三个依赖（需联网，十几秒），以后不会再装。如果自动安装失败，它会把手动安装的命令打印出来。**不要手动运行这个脚本**，AI 客户端会自己拉起进程，关掉客户端进程就结束。

配置只认源码目录里的 `config.json`，改完立即生效，不用重启服务。

## AI 能调用的工具

| 工具 | 说明 |
| --- | --- |
| `generate_animation` | **一步到位**：生图 → 去背 → 逐格质检 → 重试 → 合图，返回瓦片图和 manifest |
| `build_spritesheet` | 把已有网格图做成对齐图集（不调用生图接口） |
| `inspect_spritesheet` | 逐格质检，返回 bbox、尺寸、中心、空帧和偏差 JSON |
| `remove_background` | 去除纯色背景（四角洪水填充，保留内部同色像素） |
| `generate_image` | 纯生图，返回文件路径与尺寸 |
| `edit_image` | 用本地图片作参考改图，适合保持角色一致 |

工具返回文本 JSON，并附带预览图，AI 客户端里能直接看到结果。

## 产物在哪

产物目录只由配置里的 `outDir` 决定（示例为源码目录下的 `generated/sprites/`）；工具参数里没有输出路径这一项，调用方无法把产物写到别处。

```
generated/sprites/fox_run/
  source.png       去背后的原始网格图
  frame-0.png ...  对齐后的单帧
  spritesheet.png  横向瓦片图
  contact.png      联系图，便于目视检查
  preview.gif      GIF 预览（透明区域压白底）
  manifest.json    引擎无关的图集描述，pivot 可直接用作锚点
```

## 已知限制

- 帧间一致性取决于生图模型；复杂动作可能还是要用 `edit_image` 逐帧修正。
- 去背是纯色键控，渐变背景和带阴影的背景去不干净；可让提示词强调 `flat uniform single-color`。
- GIF 预览把透明区域压成白色，仅用于检查，不是交付格式。

## License

MIT，见 [LICENSE](LICENSE)。
