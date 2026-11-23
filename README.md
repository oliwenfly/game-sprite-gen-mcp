# game-sprite-gen-mcp

一句提示词，直接生成可用的**序列帧动画素材**：自动出图、去背、对齐，最后导出瓦片图、单帧、manifest 和 GIF 预览。通过 MCP 让 AI 客户端直接调用，不用你自己启动任何服务。

![fox_hit 序列帧瓦片图](example-fox-hit.png)

## 3 步开始用

> **注意：全文示例路径 `D:/game-sprite-gen-mcp` 只是举例，请替换成你自己实际放源码的目录。**

### 1. 下载源码

下载或解压到任意目录，例如 `D:/game-sprite-gen-mcp`。

> **硬性前提：本机必须已安装 Python 3.10+。** 源码不带 Python，入口脚本只会自动安装依赖包，不会安装 Python 本身。没装的话先去 https://www.python.org/downloads/ 下载安装，安装时勾选 **Add Python to PATH**。装完在命令行运行 `python --version`（macOS / Linux 用 `python3 --version`），能显示 3.10 以上的版本号就可以继续。

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
| `outDir` | `generated/sprites` | 产物目录，相对路径以源码目录为基准 |
| `timeoutMs` | `180000` | 单次请求超时，单位毫秒 |
| `transparent` | `false` | 是否生成透明背景图片 |

改完立即生效，不用重启服务。

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

第一次启动会自动安装依赖（需联网，十几秒），之后不会再装。不用手动启动服务，AI 客户端会自己拉起进程。

## 产物在哪

产物写在配置的 `outDir` 目录下（示例为源码目录下的 `generated/sprites/`）。

```
generated/sprites/fox_run/
  source.png       去背后的原始网格图
  frame-0.png ...  对齐后的单帧
  spritesheet.png  横向瓦片图
  contact.png      联系图，便于目视检查
  preview.gif      GIF 预览（透明区域压白底）
  manifest.json    图集描述文件，含每帧锚点
```

## 已知限制

- 帧间一致性取决于生图模型；复杂动作可能还需要逐帧修正。
- 去背依赖纯色背景，渐变或带阴影的背景去不干净；可在提示词里强调纯色背景。
- GIF 预览把透明区域压成白色，仅用于检查，不是交付格式。

## License

MIT，见 [LICENSE](LICENSE)。
