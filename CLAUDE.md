# CLAUDE.md — LTX2.3-Multifunctional

## What This Project Is

A custom UI wrapper and patch layer for **LTX Desktop** (Lightricks' local AI video generation app). It replaces the official Electron UI with a bilingual (English/Chinese) web interface and monkey-patches the official Python backend to add features LTX Desktop doesn't expose: LoRA support, custom inference steps, multi-GPU switching, start/end frame interpolation, batch multi-keyframe generation, LM Studio prompt enhancement, and more.

**This is not a standalone app.** It requires a working LTX Desktop installation as the rendering engine.

## Architecture

```
run.bat
  -> main.py (entry point)
       |
       +-- Backend thread (port 3000):
       |     LTX Desktop's bundled Python (%LOCALAPPDATA%\LTXDesktop\python\python.exe)
       |     runs the official ltx2_server with PYTHONPATH set so patches/ overrides
       |     official backend modules (app_factory.py, video_generation_handler.py, etc.)
       |
       +-- UI server (port 4000):
             FastAPI serving static files from UI/ (index.html, index.js, index.css, i18n.js)
```

### Key Directories

- **`patches/`** — Python files that override LTX Desktop backend modules via PYTHONPATH priority
- **`UI/`** — Vanilla JS/HTML/CSS frontend (no framework, no build step)
- **`LTX_Shortcut/`** — User places their LTX Desktop `.lnk` shortcut here for path resolution
- **`archive/`** — Frozen legacy snapshots (do not edit)

### Patch Injection Mechanism

`main.py` sets `PYTHONPATH=patches;{LTX_BACKEND_DIR}` so any Python module in `patches/` shadows the identically-named official module. The patched `app_factory.py` is the main orchestration file — it imports official routers, adds custom endpoints, and monkey-patches handler classes.

### Dual-Server Design

| Server | Port | Role |
|--------|------|------|
| Backend (LTX core) | 3000 | Video/image generation, model management, GPU ops. Run by LTX Desktop's bundled Python. |
| UI (FastAPI) | 4000 | Serves the custom web UI. Run by system or LTX Python. |

The frontend JS (`index.js`) talks to `http://localhost:3000` for all API calls.

### Settings Persistence

- **`%LOCALAPPDATA%\LTXDesktop\settings.json`** — User settings (API keys, dirs, preferences). Read/written by backend endpoints.
- **`patches/settings.json`** — Default/template settings bundled with this repo.
- **`localStorage`** in the browser — UI-only state (LM Studio toggle, temperature, etc.)

### i18n System

- `i18n.js` exports `zh` and `en` translation dictionaries
- HTML elements use `data-i18n` attributes for static text, `data-i18n-placeholder` for placeholders
- JS uses `_t('key')` (aliased from `t()`) for dynamic strings in `addLog()`, template literals, etc.
- Language stored in `localStorage` key `ltx_ui_lang`

## LTX Desktop Backend — Official Routes & Features

These are the official backend routers imported by our patched `app_factory.py`:

| Router | Prefix | Purpose |
|--------|--------|---------|
| `generation_router` | `/api/generate` | Video generation (fast model) |
| `health_router` | `/api/health` | Health check, GPU telemetry, model status |
| `ic_lora_router` | `/api/ic-lora` | IC-LoRA extract + generate (canny/depth conditioning) |
| `image_gen_router` | `/api/generate-image` | Image generation |
| `models_router` | `/api/models` | Model listing (overridden by our custom scanner) |
| `suggest_gap_prompt_router` | `/api/suggest-gap-prompt` | AI prompt suggestion for batch gaps |
| `retake_router` | `/api/retake` | Video retake/replace segments |
| `runtime_policy_router` | `/api/runtime-policy` | Force-API-mode decisions |
| `settings_router` | `/api/settings` | Settings read/write |

### Custom Endpoints Added by Patches

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/system/clear-gpu` | POST | Force-unload models, clear VRAM |
| `/api/system/low-vram-mode` | GET/POST | Toggle sequential CPU offload mode |
| `/api/system/reset-state` | POST | Clear generation state locks without GPU unload |
| `/api/system/set-dir` / `get-dir` | POST/GET | Custom output directory |
| `/api/system/browse-dir` | GET | Windows folder picker dialog |
| `/api/system/enhance-prompt` | POST | LM Studio prompt enhancement |
| `/api/system/list-gpus` | GET | Enumerate CUDA devices |
| `/api/system/switch-gpu` | POST | Switch active GPU (multi-GPU) |
| `/api/system/upload-image` | POST | Upload start/end frames and images |
| `/api/system/file` | GET | Serve files from output directory (path-restricted) |
| `/api/lora-dir` | GET/POST | Custom LoRA directory |
| `/api/models-dir` | GET/POST | Custom models directory + text encoder toggle |
| `/api/loras` | GET | Scan LoRA files (.safetensors, .ckpt, .pt, .bin) |
| `/api/models` | GET | Scan model checkpoints (overrides official router) |
| `/api/system/seed-settings` | GET/POST | Read/write seed lock state and locked seed value |
| `/api/system/upscaler` | GET/POST | Toggle built-in upscaler on/off |

### Backend Model Types (from `api_types.py`)

```
checkpoint, upsampler, distilled_lora, ic_lora, depth_processor,
person_detector, pose_processor, text_encoder, zit
```

### Generation Request Fields (GenerateVideoRequest)

```
prompt, resolution, model, cameraMotion, negativePrompt, duration, fps,
audio, imagePath, audioPath, startFramePath, endFramePath,
keyframePaths, keyframeStrengths, keyframeTimes,
aspectRatio, modelPath, loraPath, loraStrength
```

Note: `inferenceSteps` is added dynamically by `_extend_generate_video_request_model()` with `extra="allow"`.

### Pipeline Types

- **Fast pipeline** (`LTXFastVideoPipeline`) — Default, 8 steps, supports LoRA injection
- **Pro/A2V pipeline** — Used for audio-to-video, higher quality, 20 steps
- **Retake pipeline** — Video segment replacement, uses distilled model, 11 steps
- **IC-LoRA pipeline** — Conditioning-based generation (canny/depth)

### Text Encoding

The backend supports two encoding modes controlled by `use_local_text_encoder` in settings:
- **Local**: Uses the downloaded Gemma text encoder on GPU
- **API**: Uses Gemini API for text encoding (requires `gemini_api_key`)

The `should_use_local_encoding()` method on `TextHandler` decides which path to take.

## Coding Conventions

- **All user-facing strings must be bilingual** — use i18n keys, never hardcode Chinese or English alone
- **Python patches**: Mirror the official module name exactly (e.g., `app_factory.py` shadows `app_factory.py`)
- **No build step** — vanilla JS, edit and refresh
- **Settings changes** persist to `%LOCALAPPDATA%\LTXDesktop\settings.json` via API endpoints, not by writing files from the frontend
- **Error messages** follow the pattern: `"English message / 中文消息"`
- **All Chinese code comments** should have English translations appended

## Completed Work

### Collapse Folder Structure (done 2026-04-05)
Removed the `LTX2.3/` and `LTX2.3-1.0.3/` subdirectory nesting. The root now contains `patches/`, `UI/`, `main.py`, `run.bat` directly. Old code archived in `archive/`.

### Expose Unexposed Backend Features (done 2026-04-05)
Implemented the following features in the UI:
- **Inference steps slider** — Range 1-50, default 8 (fast) / 20 (pro), in video + batch modes
- **Negative prompt customization** — Collapsible textarea, replaces hardcoded default
- **Seed control** — Lock/unlock with backend persistence via `/api/system/seed-settings`
- **Upscaler toggle** — Checkbox in settings with backend persistence via `/api/system/upscaler`
- **LM Studio prompt enhancement discoverability** — Always-visible "Enhance" button, guides to settings when unconfigured

## Future Work (Do Not Implement Yet — Log Only)

### 1. Rewrite README
The current README is a mix of changelog entries, troubleshooting steps, and Chinese/English paragraphs in no clear order. Rewrite for clarity:
- Concise feature list
- Precise install steps (1. install LTX Desktop v1.0.4, 2. copy shortcut, 3. run.bat)
- System requirements (VRAM, Windows, etc.)
- Move troubleshooting to a separate section or file

### 2. Remaining Unexposed Backend Features
These backend features still lack UI exposure:
- **Fast vs Pro model selection**: The backend has `fast_model` and `pro_model` configs. The UI hardcodes `model: "ltx-2"` (fast). Expose a fast/pro toggle.
- **Distilled model**: `distilled_lora` is a model file type; the retake pipeline uses `distilled=True`. Investigate whether users can opt into distilled mode.
- **IC-LoRA (image conditioning)**: Full canny/depth conditioning pipeline exists (`/api/ic-lora/extract`, `/api/ic-lora/generate`). Not exposed.
- **Retake/segment replace**: `/api/retake` endpoint exists. Not surfaced.
- **Camera motion prompts**: Backend appends camera motion text to prompts. Users can't see what text is appended.
- **Torch compile**: `use_torch_compile` exists in settings but isn't exposed.
- **Prompt cache size**: `prompt_cache_size` in settings, not exposed.

### 3. Local Text Encoder Selection
Currently the backend only supports one local text encoder (the bundled Gemma model). The toggle is binary: local Gemma vs API (Gemini). If LTX Desktop ever adds support for alternative local text encoders (e.g., different model sizes, quantized versions, or entirely different encoder architectures), we want to surface that selection in the UI. Monitor `TextHandler`, `resolve_gemma_root()`, and `model_download_specs` for changes in future LTX Desktop releases.
