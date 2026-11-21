# game-sprite-gen-mcp

Turn a single prompt into usable **sprite-sheet animation assets**: generate an N×M grid, remove the background, inspect every cell, retry on failure, then export an aligned tile sheet, individual frames, a manifest, and a GIF preview. AI clients call it over MCP — you never start a service yourself.

![fox_hit sprite sheet](example-fox-hit.png)

## 3 steps

> **The path `D:/game-sprite-gen-mcp` used throughout this guide is only an example, not a fixed value. Replace it with the directory where you actually put the source; nothing else needs changing.**

### 1. Download the source

Unzip it anywhere, e.g. `D:/game-sprite-gen-mcp`. Requires Python 3.10+.

> **Remember this path** — you need it in step 3's MCP config.

### 2. Edit the configuration

If there is no `config.json` in the source directory, create one using `config.example.json` as a reference, or simply rename `config.example.json` to `config.json`. Then edit it as described below. Only `apiKey` is required; every other field has a default.

```json
{
  "baseUrl": "https://api.openai.com",
  "model": "gpt-image-2.5",
  "editModel": "gpt-image-2.5",
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
| `editModel` | `gpt-image-2.5` | Model used when editing with reference images |
| `quality` | `auto` | Quality parameter, e.g. `low` / `medium` / `high` / `auto` |
| `outDir` | `generated/sprites` | Default output directory; relative paths are resolved from the source directory, and tool calls can override it with `out_dir` |
| `timeoutMs` | `180000` | Per-request timeout in milliseconds |
| `transparent` | `false` | Whether the model returns transparent PNGs directly (see below) |

With `transparent: true`, requests include `background: "transparent"` (and force `output_format: "png"`), so the model returns images with alpha and local background removal is skipped. Supported by gpt-image-2.5 and later; older models or third-party gateways may reject it — keep `false` in that case and rely on local background removal.

Optional overrides via environment variables: `SPRITEGEN_API_KEY`, `SPRITEGEN_BASE_URL`, `SPRITEGEN_MODEL`, `SPRITEGEN_EDIT_MODEL`, `SPRITEGEN_QUALITY`, `SPRITEGEN_TIMEOUT_MS`, `SPRITEGEN_TRANSPARENT`. Normally unnecessary.

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

Without `out_dir`, files go to `generated/sprites/` inside the source directory. Pass an absolute path in the tool call to write elsewhere.

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
