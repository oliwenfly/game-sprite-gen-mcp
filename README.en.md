# game-sprite-gen-mcp

Turn a single prompt into usable **sprite-sheet animation assets**: generate, remove the background, align, then export a tile sheet, individual frames, a manifest, and a GIF preview. AI clients call it over MCP — you never start a service yourself.

![fox_hit sprite sheet](example-fox-hit.png)

## 3 steps

> **The path `D:/game-sprite-gen-mcp` used throughout this guide is only an example. Replace it with the directory where you actually put the source.**

### 1. Download the source

Unzip it anywhere, e.g. `D:/game-sprite-gen-mcp`.

> **Requirement: Python 3.10+ must already be installed.** Python is not bundled with the source, and the entry script only installs dependency packages — it does not install Python itself. If needed, download it from https://www.python.org/downloads/ and check **Add Python to PATH** during installation. Verify with `python --version` (`python3 --version` on macOS/Linux); it must print 3.10 or later before you continue.

### 2. Edit the configuration

If there is no `config.json` in the source directory, create one using `config.example.json` as a reference, or simply rename `config.example.json` to `config.json`. Then edit it as described below. Only `apiKey` is required; every other field has a default.

```json
{
  "baseUrl": "https://api.openai.com",
  "model": "gpt-image-2.5",
  "quality": "auto",
  "apiKey": "sk-your-key",
  "outDir": "generated/sprites",
  "timeoutMs": 180000,
  "transparent": false
}
```

| Field | Default | Description |
| --- | --- | --- |
| `apiKey` | empty | Image API key, required |
| `baseUrl` | `https://api.openai.com` | API base URL; any OpenAI-compatible image gateway works |
| `model` | `gpt-image-2.5` | Generation model |
| `quality` | `auto` | Quality parameter, e.g. `low` / `medium` / `high` / `auto` |
| `outDir` | `generated/sprites` | Output directory; relative paths are resolved from the source directory |
| `timeoutMs` | `180000` | Per-request timeout in milliseconds |
| `transparent` | `false` | Whether to generate images with a transparent background |

Edits take effect immediately; no restart needed.

### 3. Paste this into your AI client's MCP config

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

**Only change the path in `args`**: replace `D:/game-sprite-gen-mcp` in the example with the directory where you put the source in step 1. Leave everything else as-is. Use `python3` as `command` on macOS/Linux.

Restart the AI client and just say:

> Use spritegen to make a 4-frame fox running sprite animation with a transparent background, named fox_run

The first launch installs dependencies automatically (needs internet, a few seconds), and never again. No need to start the service yourself — the AI client launches it.

## Output

Files go to the configured `outDir` (the example uses `generated/sprites/` inside the source directory).

```
generated/sprites/fox_run/
  source.png       Raw grid image after background removal
  frame-0.png ...  Aligned individual frames
  spritesheet.png  Horizontal tile sheet
  contact.png      Contact sheet for visual inspection
  preview.gif      GIF preview (transparent areas on white)
  manifest.json    Atlas description with a per-frame pivot
```

## Known limitations

- Frame-to-frame consistency depends on the image model; complex actions may still need per-frame fixes.
- Background removal relies on a flat solid background; gradients and shadows cannot be fully removed — mention a solid background in your prompt.
- The GIF preview composites transparency onto white. It is for inspection only.

## License

MIT, see [LICENSE](LICENSE).
