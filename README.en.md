# game-sprite-gen-mcp

An image-generation tool for AI clients, with four capabilities: **text-to-image, image editing, text-to-animation, and animation editing**.

Sprite sheets are background-keyed and aligned automatically, then exported as a tile sheet, individual frames, a GIF preview and a manifest. Edits upload the reference image to the model for real, so art style and the character do not drift. **All four tools make a single call — cost does not scale with frame count.**

![fox_hit sprite sheet](example-fox-hit.png)

## 3 steps

> The path `D:/game-sprite-gen-mcp` used throughout is only an example. Replace it with the directory where you put the source.

### 1. Download the source

Unzip it anywhere, e.g. `D:/game-sprite-gen-mcp`.

> **Requirement: Python 3.10+ must already be installed.** Python is not bundled, and the script only installs dependency packages.
> If needed, get it from https://www.python.org/downloads/ and check **Add Python to PATH** during installation.
> Verify with `python --version` (`python3 --version` on macOS/Linux).

### 2. Edit the configuration

Rename `config.example.json` to `config.json` and fill in your `apiKey`. Every other field has a default, so you can leave them as-is.

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
| `apiKey` | empty | Required; your image API key |
| `baseUrl` | `https://api.openai.com` | API base URL; any OpenAI-compatible gateway works |
| `model` | `gpt-image-2.5` | Generation model |
| `quality` | `auto` | `low` / `medium` / `high` / `auto` |
| `outDir` | `generated/sprites` | Output directory; relative paths resolve from the source directory |
| `timeoutMs` | `180000` | Per-request timeout in milliseconds |
| `transparent` | `false` | Generate transparent backgrounds |

Changes take effect immediately; no restart needed.

### 3. Add it to your AI client

Paste this into your AI client's MCP config:

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

**Only change the path in `args`** to the directory from step 1. Use `python` as `command` on Windows, `python3` on macOS/Linux.

Restart the client and just say:

> Use spritegen to make a 4-frame fox running sprite animation with a transparent background, named fox_run

The first launch installs dependencies automatically (needs internet, a few seconds) and never again. No need to start a service yourself.

## Output

Files go to `outDir`, **one subdirectory per `name`**, so different names never overwrite each other.

```
generated/sprites/fox_run/
  spritesheet.png   Horizontal tile sheet
  frame-0.png ...   Aligned individual frames
  preview.gif       GIF preview
  contact.png       Contact sheet for visual inspection
  overlay.png       Alignment check; coloured ghosting means drift remains
  manifest.json     Atlas description with per-frame pivot
  source.png        The grid image as painted by the model
  reference.png     Keyed reference used as the base (edit_animation only)
```

Single-image tools (`generate_image` / `edit_image`) also use a `name` subdirectory and put the PNGs directly inside it:

```
generated/sprites/icon_fox/icon_fox.png      n = 1
generated/sprites/icon_fox/icon_fox-1.png    n > 1, numbered
```

## The four tools

| Tool | Input | Output | Use when |
| --- | --- | --- | --- |
| `generate_image` | prompt | one PNG | icons, portraits, backgrounds, props |
| `edit_image` | reference + prompt | one PNG | recolor, outfit swap, background swap, single-frame fix |
| `generate_animation` | prompt | sprite sheet | animation with no reference |
| `edit_animation` | reference + one action description | sprite sheet | you already have art and want it animated |

The selection rules are built into each tool description, so the AI client picks correctly:

1. single image, no reference → `generate_image`
2. single image, with a reference → `edit_image`
3. animation, no reference → `generate_animation`
4. animation, with a reference → `edit_animation`

The two reference-based tools upload the image as a real input rather than describing it in words, so art style, face, outfit and lighting do not drift.

### Reference size limit

The reference is size-checked before upload, so an oversized image **fails fast instead of burning a call**: no side above 4096px, no more than 4194304 total pixels (2048×2048).

A sprite sheet is assembled locally and can easily exceed that — eight 1024px frames are 8192px wide and will be rejected. If that happens, call `edit_animation` on a **single character drawing** and let it lay out the grid.

## Three local tools

These **never call the generation API, cost nothing**, and do not require `apiKey`:

| Tool | Purpose |
| --- | --- |
| `build_spritesheet` | Reassemble an existing grid or loose frames into an aligned sheet |
| `inspect_spritesheet` | Per-cell check: empty cells, edge-touching content, size deviation |
| `remove_background` | Key out a flat background, writing `<original>-nobg.png` |

## Common parameters

| Param | Default | Description |
| --- | --- | --- |
| `name` | per tool | Output directory name; files land in `outDir/<name>/` |
| `size` | model default | e.g. `1024x1024`, `1536x1024`, `1024x1536` |
| `quality` | config value | Overrides the configured quality |
| `n` | 1 | How many images; capped at 4 for single-image tools |
| `transparent` | config value | Request a transparent background; gateways that reject the parameter fall back to prompt-only |
| `remove_bg` | false single, true animation | Key out a flat background |
| `cols` / `rows` | 4 / 2 | Frame count = cols × rows |
| `motions` | required | **One** action description for `edit_animation`, e.g. "the fox jumps and waves both paws" |
| `fps` | 10 / 12 | GIF frame rate |
| `loop` | true | Whether the GIF loops |
| `anchor` | `bottom` | Alignment pivot; usually bottom for characters |
| `retries` | 1 | Re-rolls when the grid fails quality checks |
| `pad` | 0 | Extra transparent margin around each frame |
| `target_h` | 0 | Force a uniform frame height |
| `gutter` | 4 | Gap between frames in the tile sheet |
| `raw` | false | Pass-through mode; only slice, no processing |

**If the animation looks jittery**, check `overlay.png` for coloured ghosting first. The defaults align well in most cases.

## FAQ

**The animation drifts.**
Look at `overlay.png` first. Automatic alignment (size normalization plus cross-correlation) is on by default and residual drift is usually under 1px. If ghosting is still obvious, the model probably drew the character itself inconsistently (wrong proportions, a missing limb) — that frame has to be re-rolled.

**Do I need to restart after changing config?**
No, changes take effect immediately.

**The first call is slow.**
Dependencies install on first launch, taking a few seconds. It will not repeat.

**Background removal leaves edges.**
It relies on a flat solid background; gradients and shadows cannot be fully removed. Emphasising "solid background" in the prompt helps a lot.

**Can `edit_animation` target a specific frame?**
No. It repaints the whole sheet from one overall description and the model decides how to distribute it across cells. Per-frame control means editing by hand.

## Known limitations

- Frame-to-frame consistency depends on the image model; complex actions may still need manual fixes.
- `edit_image` and `edit_animation` require a gateway that exposes `/v1/images/edits`.
- `edit_animation` repaints the whole grid, so the model may draw the wrong number of cells or leave one empty. The sheet is checked and re-rolled (`retries`), but this is not per-frame control.
- `generate_image` caps `n` at 4 to avoid accidental spend.
- The GIF preview composites transparency onto white; it is for inspection only.

## License

MIT, see [LICENSE](LICENSE).
