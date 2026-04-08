# LTX2.3-Multifunctional

A bilingual (English/Chinese) UI wrapper and feature extension layer for **LTX Desktop** (Lightricks' local AI video generation app). Replaces the official Electron UI with a custom web interface and unlocks backend features not exposed by the official app.

> **This is not a standalone app.** A working LTX Desktop installation is required as the rendering engine.
>
> **实验性项目 / Experimental project** — may break with LTX Desktop updates.

---

## System Requirements / 系统要求

- **OS**: Windows (LTX Desktop is Windows-only)
- **VRAM**: 12 GB minimum (LTX Desktop v1.0.4+). 24 GB recommended for longer videos.
- **LTX Desktop**: v1.0.4 or later — [Download](https://ltx.io/ltx-desktop)

---

## Installation / 安装

1. Install **LTX Desktop v1.0.4+** from [ltx.io/ltx-desktop](https://ltx.io/ltx-desktop)
2. Copy the LTX Desktop shortcut into the `LTX_Shortcut/` folder in this repo
3. Run `run.bat`
4. Open `http://localhost:4000` in your browser

> 1. 安装 LTX Desktop v1.0.4+
> 2. 将 LTX Desktop 快捷方式复制到 `LTX_Shortcut/` 文件夹
> 3. 运行 `run.bat`
> 4. 在浏览器中打开 `http://localhost:4000`

---

## Features / 功能

| Feature | Description |
|---|---|
| **LoRA support** | Load `.safetensors`/`.ckpt`/`.pt`/`.bin` LoRA files from a custom directory |
| **Custom inference steps** | Override the default step count (1–50) per generation |
| **Negative prompt** | Collapsible field to override the hardcoded default |
| **Seed control** | Lock/unlock seed with backend persistence |
| **Upscaler toggle** | Enable/disable the built-in upscaler per generation |
| **VRAM cap** | Set a maximum VRAM budget (GB) to control pipeline layer streaming — useful for 12–24 GB GPUs |
| **Multi-GPU switching** | Select which CUDA device to use |
| **Start / end frame** | Image-to-video with custom start and/or end frames |
| **Batch keyframe generation** | Two modes: latent-space insertion or independent segment stitching |
| **LM Studio prompt enhancement** | Enhance prompts locally via a running LM Studio instance |
| **Local text encoder toggle** | Force local Gemma encoder instead of LTX API encoding |
| **Bilingual UI** | Full English / Chinese interface, switch at runtime |
| **Model selection** | Point to a custom model directory |

---

## Troubleshooting / 常见问题

### GPU not used — system forces API mode / 系统强制使用 API 模式

**Symptom**: Generation goes through the FAL or LTX cloud API even though you have a local GPU.

**Cause**: LTX Desktop requires 31 GB VRAM before it enables local generation by default.

**Fix**: Run `API issues.bat` as Administrator (included in this repo). This patches the VRAM threshold and clears any stored API key that overrides local mode.

**Manual fix** (if the batch file doesn't work):

1. Edit `%LOCALAPPDATA%\LTXDesktop\settings.json` — set `"fal_api_key": ""`
2. Edit `C:\Program Files\LTX Desktop\resources\backend\runtime_config\runtime_policy.py` line 16:
   - Change `return vram_gb < 31` → `return vram_gb < 6`
3. Restart LTX Desktop.

---

## ComfyUI Bridge / ComfyUI 节点

This backend can be accessed from ComfyUI via a community node:
[ComfyUI_TY_LTX_Desktop_Bridge](https://github.com/supart/ComfyUI_TY_LTX_Desktop_Bridge)

---

## Demo / 演示

Video tutorial: https://youtu.be/rM_wUogtrOU
