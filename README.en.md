# game-sprite-gen-mcp

Turn a single prompt into usable **sprite-sheet animation assets**: generate an N×M grid, remove the background, inspect every cell, retry on failure, then export an aligned tile sheet, individual frames, a manifest, and a GIF preview. AI clients call it over MCP — you never start a service yourself.

![fox_hit sprite sheet](example-fox-hit.png)

## 3 steps

> **The path `D:/game-sprite-gen-mcp` used throughout this guide is only an example, not a fixed value. Replace it with the directory where you actually put the source; nothing else needs changing.**
>
> **Also required: Python 3.10+ must already be installed on your machine.** See step 1.

### 1. Download the source

Unzip it anywhere, e.g. `D:/game-sprite-gen-mcp`.

> **Requirement: Python 3.10+ must already be installed.** Python is not bundled with the source, and the entry script only installs dependency packages — it does not install Python itself. If needed, download it from https://www.python.org/downloads/ and check **Add Python to PATH** during installation. Verify with `python --version` (`python3 --version` on macOS/Linux); it must print 3.10 or later before you continue.

> **Remember this path** — you need it in step 3's MCP config.

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
| `outDir` | `generated/sprites` | Output directory; relative paths are resolved from the source directory. **This is the only source of the output path** — the tools expose no output-path argument |
| `timeoutMs` | `180000` | Per-request timeout in milliseconds |
| `transparent` | `false` | Whether the model should return images with an alpha channel (see below) |

With `transparent: true`, no request parameter changes: the transparent-background requirement is **written into the prompt** (`config.TRANSPARENT_PROMPT`) so the model itself returns a PNG with alpha. The grid template's "flat single-color background" wording is swapped for the transparent one as well. If the returned image already has alpha, local background removal is skipped; if the model still returns a flat backdrop, the removal fallback still applies.

Optional overrides via environment variables: `SPRITEGEN_API_KEY`, `SPRITEGEN_BASE_URL`, `SPRITEGEN_MODEL`, `SPRITEGEN_QUALITY`, `SPRITEGEN_TIMEOUT_MS`, `SPRITEGEN_TRANSPARENT`. Normally unnecessary.

`config.json` is in `.gitignore` and is never committed.

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

The first launch installs `mcp`, `pillow`, and `httpx` automatically (needs internet, ~10s); if that fails, the exact manual command is printed. **Do not run the script yourself** — the client starts and stops the process.

Settings always come from `config.json` in the source directory; edits take effect on the next call without a restart.

## Available tools

| Tool | Description |
| --- | --- |
| `generate_animation` | **End to end**: generate → remove background → inspect → retry → compose, returning the tile sheet and manifest |
| `build_spritesheet` | Build an aligned sheet from an existing grid image (no image API call) |
| `inspect_spritesheet` | Per-cell inspection: bbox, size, center, empty frames, deviation |
| `remove_background` | Remove a flat solid background (four-corner flood fill) |
| `generate_image` | Image generation only, returning file paths and sizes |
| `edit_image` | Edit images using a local reference, useful for consistent characters |

Tools return JSON text plus a preview image so the result is visible in the client.

## Output

Files go to the configured `outDir` (the example uses `generated/sprites/` inside the source directory). There is no output-path argument on the tools, so a caller cannot write anywhere else.

```
generated/sprites/fox_run/
  source.png       Raw grid image after background removal
  frame-0.png ...  Aligned individual frames
  spritesheet.png  Horizontal tile sheet
  contact.png      Contact sheet for visual inspection
  preview.gif      GIF preview (transparent areas on white)
  manifest.json    Engine-agnostic atlas description; pivot is ready to use as an anchor
```

## Known limitations

- Frame-to-frame consistency depends on the image model; complex actions may need per-frame `edit_image` fixes.
- Background removal is color keying: gradients and shadows cannot be fully removed; reinforce "flat uniform single-color" in the prompt.
- The GIF preview composites transparency onto white. It is for inspection only.

## License

MIT, see [LICENSE](LICENSE).
