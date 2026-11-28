# game-sprite-gen-mcp

给 AI 客户端用的生图工具，四种能力：**文生图、改图、文生序列帧、改序列帧**。

序列帧会自动去背、对齐，导出瓦片图、单帧、GIF 预览和 manifest。改图会把参考图真正上传给模型，画风和角色不会跑偏。**四个工具都是一次调用，成本与帧数无关。**

![fox_hit 序列帧瓦片图](example-fox-hit.png)

## 3 步开始用

> 全文示例路径 `D:/game-sprite-gen-mcp` 只是举例，请替换成你自己放源码的目录。

### 1. 下载源码

解压到任意目录，例如 `D:/game-sprite-gen-mcp`。

> **前提：本机已装 Python 3.10+。** 源码不带 Python，脚本只会自动装依赖包。
> 没装的话去 https://www.python.org/downloads/ 下载，安装时勾选 **Add Python to PATH**。
> 装完运行 `python --version`（macOS / Linux 用 `python3 --version`）确认版本。

### 2. 修改配置

把 `config.example.json` 改名为 `config.json`，然后填入你的 `apiKey`。其余字段都有默认值，不改也能用。

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
| `apiKey` | 空 | 必填，生图接口的 Key |
| `baseUrl` | `https://api.openai.com` | 接口地址，支持任意 OpenAI 兼容网关 |
| `model` | `gpt-image-2.5` | 生图模型 |
| `quality` | `auto` | `low` / `medium` / `high` / `auto` |
| `outDir` | `generated/sprites` | 产物目录，相对路径以源码目录为准 |
| `timeoutMs` | `180000` | 单次请求超时（毫秒） |
| `transparent` | `false` | 是否生成透明背景 |

改完立即生效，不用重启。

### 3. 配到 AI 客户端里

把这段粘到 AI 客户端的 MCP 配置中：

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

**只需要改 `args` 里的路径**，换成你第 1 步实际放源码的目录。Windows 的 `command` 写 `python`，macOS / Linux 写 `python3`。

重启客户端，然后直接对 AI 说：

> 用 spritegen 生成 4 帧狐狸奔跑的序列帧，透明背景，名字叫 fox_run

第一次启动会自动装依赖（需联网，十几秒），之后不再装。不用手动启动服务。

## 产物在哪

产物在 `outDir` 下，**每个 `name` 一个子目录**，不同名字不会互相覆盖。

```
generated/sprites/fox_run/
  spritesheet.png   横向瓦片图
  frame-0.png ...   对齐后的单帧
  preview.gif       GIF 预览
  contact.png       联系图，方便目视检查
  overlay.png       对齐检查图，有彩色重影就说明还有偏移
  manifest.json     图集描述，含每帧锚点
  source.png        模型直出的整张网格图
  reference.png     去背后的参考底图（仅 edit_animation）
```

单图工具（`generate_image` / `edit_image`）同样按 `name` 建子目录，图片直接放在里面：

```
generated/sprites/icon_fox/icon_fox.png       n = 1
generated/sprites/icon_fox/icon_fox-1.png     n > 1，按序号命名
```

## 四个工具

| 工具 | 输入 | 产物 | 什么时候用 |
| --- | --- | --- | --- |
| `generate_image` | 提示词 | 单张 PNG | 图标、立绘、背景、道具 |
| `edit_image` | 参考图 + 提示词 | 单张 PNG | 换色、换装、换背景、改单张图 |
| `generate_animation` | 提示词 | 序列帧图集 | 没有参考图的动画 |
| `edit_animation` | 参考图 + 一句动作描述 | 序列帧图集 | 已有立绘，要把它做成动画 |

选型规则已经写进每个工具的描述里，AI 客户端会自己判断：

1. 单张图 + 没有参考图 → `generate_image`
2. 单张图 + 有参考图 → `edit_image`
3. 序列帧 + 没有参考图 → `generate_animation`
4. 序列帧 + 有参考图 → `edit_animation`

带参考图的两个工具会把图真正上传给模型，不是转成文字再重画，所以画风、脸、服装、光照不会跑偏。

### 参考图尺寸限制

上传前会检查参考图尺寸，**超限直接报错，不会白花一次调用**：单边不超过 4096px，总像素不超过 4194304（2048×2048）。

序列帧图集是本地拼出来的，很容易超限。例如 8 帧 × 1024px 就是 8192px 宽，会被拦下。遇到这种情况，直接对**单张角色图**调用 `edit_animation`，让它自己出网格。

## 三个本地工具

这三个工具**不调用生图接口、不花钱**，也不要求配置 `apiKey`：

| 工具 | 用途 |
| --- | --- |
| `build_spritesheet` | 把已有的网格图或单帧重新拼成对齐的图集 |
| `inspect_spritesheet` | 逐格检查图集：空帧、内容贴边、帧尺寸偏差 |
| `remove_background` | 按四角纯色去背，输出 `<原名>-nobg.png` |

## 常用参数

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `name` | 见各工具 | 产物目录名，决定产物落在 `outDir/<name>/` |
| `size` | 模型默认 | 如 `1024x1024`、`1536x1024`、`1024x1536` |
| `quality` | 配置值 | 临时覆盖配置里的质量 |
| `n` | 1 | 一次生成几张，单图工具上限 4 |
| `transparent` | 配置值 | 要求透明背景，不认这个参数的网关会自动降级为只写提示词 |
| `remove_bg` | 单图 false，序列帧 true | 是否按四角纯色去背 |
| `cols` / `rows` | 4 / 2 | 帧数 = cols × rows |
| `motions` | 必填 | `edit_animation` 的**一句**动作描述，例如「狐狸跳起来挥动双爪」 |
| `fps` | 10 / 12 | GIF 帧率 |
| `loop` | true | GIF 是否循环 |
| `anchor` | `bottom` | 对齐锚点，角色一般用底部 |
| `retries` | 1 | 网格质检不合格时的重跑次数 |
| `pad` | 0 | 每帧四周额外留出的透明边距 |
| `target_h` | 0 | 强制统一帧高 |
| `gutter` | 4 | 瓦片图帧间距 |
| `raw` | false | 直通模式，只切图不处理 |

**如果序列帧看起来在抖**，先看 `overlay.png` 有没有彩色重影。绝大多数情况默认参数就能对齐好，不用动。

## 常见问题

**序列帧有偏移？**
先看 `overlay.png`。默认已开启自动对齐（尺寸归一化 + 互相关配准），残余偏移通常在 1px 内。如果仍有明显重影，通常是模型把角色本身画歪了（比例变了、少了条腿），只能重跑那一帧。

**改了配置要重启吗？**
不用，改完立即生效。

**第一次调用很慢？**
首次启动要装依赖，十几秒，之后不会重复。

**去背去不干净？**
去背依赖纯色背景，渐变和阴影去不掉。在提示词里强调「纯色背景」会好很多。

**`edit_animation` 能指定某一帧的动作吗？**
不能。它是整张重画，你给一句整体描述，模型自己决定怎么分配到各格。需要逐帧精确控制只能手动改。

## 已知限制

- 帧间一致性取决于生图模型，复杂动作可能还需要手动修帧。
- `edit_image` 和 `edit_animation` 需要网关开放 `/v1/images/edits`，不支持的网关会报端点不存在。
- `edit_animation` 是整张重画，模型不保证严格遵守网格，可能画错格子数量或留空格。程序会质检并重跑（`retries`），但仍然不是逐帧可控。
- `generate_image` 的 `n` 上限为 4，防止意外产生大量费用。
- GIF 预览把透明区域压成白色，仅用于检查，不是交付格式。

## License

MIT，见 [LICENSE](LICENSE)。
