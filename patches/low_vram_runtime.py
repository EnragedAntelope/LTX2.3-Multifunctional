"""低显存模式：尽量降峰值显存（以速度换显存）；效果取决于官方管线是否支持 offload。"""

from __future__ import annotations

import gc
import logging
import os
import types
from pathlib import Path
from typing import Any

logger = logging.getLogger("ltx_low_vram")


def _ltx_desktop_config_dir() -> Path:
    p = (
        Path(os.environ.get("LOCALAPPDATA", os.path.expanduser("~/AppData/Local")))
        / "LTXDesktop"
    )
    p.mkdir(parents=True, exist_ok=True)
    return p.resolve()


def low_vram_pref_path() -> Path:
    return _ltx_desktop_config_dir() / "low_vram_mode.pref"


def read_low_vram_pref() -> bool:
    f = low_vram_pref_path()
    if not f.is_file():
        return False
    return f.read_text(encoding="utf-8").strip().lower() in ("1", "true", "yes", "on")


def write_low_vram_pref(enabled: bool) -> None:
    low_vram_pref_path().write_text(
        "true\n" if enabled else "false\n", encoding="utf-8"
    )


def apply_low_vram_config_tweaks(handler: Any) -> None:
    """在官方 RuntimeConfig 上尽量关闭 fast 超分等（若字段存在）。"""
    cfg = getattr(handler, "config", None)
    if cfg is None:
        return
    fm = getattr(cfg, "fast_model", None)
    if fm is None:
        return
    try:
        if hasattr(fm, "model_copy"):
            updated = fm.model_copy(update={"use_upscaler": False})
            setattr(cfg, "fast_model", updated)
        elif hasattr(fm, "use_upscaler"):
            setattr(fm, "use_upscaler", False)
    except Exception as e:
        logger.debug("low_vram: 无法关闭 fast_model.use_upscaler: %s", e)


def install_low_vram_on_pipelines(handler: Any) -> None:
    """启动时读取偏好，挂到 pipelines 上供各补丁读取。"""
    pl = handler.pipelines
    low = read_low_vram_pref()
    setattr(pl, "low_vram_mode", bool(low))
    if low:
        apply_low_vram_config_tweaks(handler)
        logger.info(
            "low_vram_mode: 已开启（尝试关闭 fast 超分；若显存仍高，多为权重常驻 GPU，需降分辨率/时长或 FP8 权重）"
        )


def get_vram_limit() -> float | None:
    """Read the user-configured VRAM cap (in GB) from settings.json, or None if unset."""
    try:
        import json
        settings_file = _ltx_desktop_config_dir() / "settings.json"
        if settings_file.exists():
            with open(settings_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            lim = data.get("vram_limit", "")
            if lim != "":
                return float(lim)
    except Exception:
        pass
    return None


def install_low_vram_pipeline_hooks(pl: Any) -> None:
    """在 load_gpu_pipeline / load_a2v 返回后尝试 Diffusers 式 CPU offload（无则静默）。
    Also patches pipeline __call__ to dynamically tune streaming_prefetch_count per VRAM limit.
    """
    if getattr(pl, "_ltx_low_vram_hooks_installed", False):
        return
    pl._ltx_low_vram_hooks_installed = True

    if hasattr(pl, "load_gpu_pipeline"):
        _orig_gpu = pl.load_gpu_pipeline
        pl._ltx_orig_load_gpu_for_low_vram = _orig_gpu

        def _load_gpu_wrapped(self: Any, *a: Any, **kw: Any) -> Any:
            r = _orig_gpu(*a, **kw)
            if getattr(self, "low_vram_mode", False):
                try_sequential_offload_on_pipeline_state(r)
            return r

        pl.load_gpu_pipeline = types.MethodType(_load_gpu_wrapped, pl)

    if hasattr(pl, "load_a2v_pipeline"):
        _orig_a2v = pl.load_a2v_pipeline
        pl._ltx_orig_load_a2v_for_low_vram = _orig_a2v

        def _load_a2v_wrapped(self: Any, *a: Any, **kw: Any) -> Any:
            r = _orig_a2v(*a, **kw)
            if getattr(self, "low_vram_mode", False):
                try_sequential_offload_on_pipeline_state(r)
            return r

        pl.load_a2v_pipeline = types.MethodType(_load_a2v_wrapped, pl)

    # Patch pipeline __call__ to inject streaming_prefetch_count based on vram_limit setting.
    # streaming_prefetch_count controls layer-streaming in the DiT transformer.
    # When count=None: DiT is built entirely on GPU — fastest, but high VRAM footprint.
    # When count=N:    DiT is built on CPU; N layers are prefetched to GPU per step.
    #
    # IMPORTANT — Stage 2 VRAM constraint:
    # The DistilledPipeline is two-stage. Stage 1 runs at half resolution (e.g. 640×352);
    # Stage 2 at full resolution (e.g. 1280×704) with 4× more tokens → ~4× larger activation
    # tensors (~30 GB on a 32 GB GPU). gpu_model() calls torch.cuda.empty_cache() on teardown,
    # so Gemma is NOT resident during Stage 2. The overflow is caused by activation tensors
    # dominating VRAM regardless of streaming count, plus CUDA workspace when count=None loads
    # all DiT layers simultaneously. The _install_resolution_aware_streaming() patch (installed
    # below) handles the per-stage count selection; this outer patch handles the user's vram_limit
    # cap for low-VRAM systems (lim < 25 GB). For lim ≥ 25 GB we defer to the pipeline default
    # so that _install_resolution_aware_streaming() can apply optimal per-stage counts instead.
    if not getattr(pl, "_ltx_layer_streaming_patched", False):
        pl._ltx_layer_streaming_patched = True

        def _patch_pipeline_class(cls_name: str, mod_name: str) -> None:
            import importlib
            try:
                mod = importlib.import_module(mod_name)
                pipeline_cls = getattr(mod, cls_name)
                _orig_call = pipeline_cls.__call__

                def _patched_call(self: Any, *args: Any, **kwargs: Any) -> Any:
                    lim = get_vram_limit()
                    if lim is not None:
                        if lim == 0:
                            # 0 = explicit "unlimited": disable layer streaming.
                            # WARNING: count=None loads the full DiT on GPU (~26 GB).
                            # Only use this on GPUs with ≥ 48 GB VRAM.
                            kwargs["streaming_prefetch_count"] = None
                            logger.info("vram_limit: unlimited (0) — layer streaming disabled (requires ≥48 GB VRAM).")
                        elif lim >= 25.0:
                            # Do not override: defer to the pipeline's own default (count=2).
                            # count=None would overflow VRAM on 32 GB GPUs at Stage 2 full res.
                            logger.debug(
                                "vram_limit: %.1fGB ≥ 25 GB — deferring to pipeline default (count=2) to avoid Stage 2 VRAM overflow.",
                                lim,
                            )
                        else:
                            if lim <= 10.0:
                                count = 1
                            else:
                                extra_gb = float(lim) - 10.0
                                count = max(1, min(24, 1 + round(extra_gb / 0.67)))
                            kwargs["streaming_prefetch_count"] = count
                            logger.info(
                                "vram_limit: %.1fGB → streaming_prefetch_count=%s",
                                lim,
                                count,
                            )
                    return _orig_call(self, *args, **kwargs)

                pipeline_cls.__call__ = _patched_call
                logger.info("vram_limit: patched %s.__call__", cls_name)
            except Exception:
                pass  # module not present in this LTX Desktop version — skip silently

        _patch_pipeline_class("DistilledPipeline", "ltx_pipelines.distilled")
        _patch_pipeline_class("LTXRetakePipeline", "services.retake_pipeline.ltx_retake_pipeline")
        _patch_pipeline_class("ICLoRAPipeline", "services.ic_lora_pipeline.ltx_ic_lora_pipeline")
        _patch_pipeline_class("A2VPipeline", "services.a2v_pipeline.distilled_a2v_pipeline")

    _install_resolution_aware_streaming()


def _install_resolution_aware_streaming() -> None:
    """Patch DiffusionStage.__call__ to apply optimised per-stage streaming counts.

    The DistilledPipeline calls the same DiffusionStage at two very different resolutions:

      Stage 1 (half resolution, e.g. 640×352 = 225 K px):
        Activation tensors are ~4× smaller than Stage 2.  With count=None (full DiT on GPU)
        the total footprint is roughly 2 GB (fp8 DiT weights) + ~7-10 GB (activations) ≈ 10 GB
        — easily fits on any GPU with ≥ 20 GB VRAM.  Using count=None eliminates all PCIe
        layer-transfer overhead and is the fastest possible path.

      Stage 2 (full resolution, e.g. 1280×704 = 901 K px):
        Activation tensors dominate at ~28-30 GB.  count=None additionally loads CUDA
        workspace for every layer simultaneously, pushing total VRAM to ~34 GB and causing
        silent overflow into system RAM via CUDA paging (~125× slower than HBM).
        Any finite streaming count keeps weights off GPU until needed, avoiding the
        workspace spike.  count=8 is safe (≈ 1 GB extra weights on top of ~30 GB activations)
        and ~3-4× faster than count=2 (fewer PCIe transfers per denoising step).

    This patch is installed once per process via a class-level flag and is applied
    independently of the vram_limit setting — it is always beneficial.

    Interaction with the vram_limit patch on DistilledPipeline.__call__:
      The vram_limit patch may set a coarse streaming count before each call.
      This patch refines that count at the DiffusionStage level where the actual
      resolution is known.  Stage 2 count is capped to STAGE2_STREAMING_COUNT;
      Stage 1 count is promoted to None on high-VRAM GPUs.
    """
    try:
        from ltx_pipelines.utils.blocks import DiffusionStage
        if getattr(DiffusionStage, "_ltx_resolution_aware_patched", False):
            return

        _orig_call = DiffusionStage.__call__

        # Detect GPU VRAM once at patch-install time.
        try:
            import torch as _torch
            _vram_gb = (
                _torch.cuda.get_device_properties(_torch.cuda.current_device()).total_memory
                / 1024 ** 3
                if _torch.cuda.is_available()
                else 0.0
            )
        except Exception:
            _vram_gb = 0.0

        # Stage 1 with count=None uses ~10 GB total (safe when GPU ≥ 20 GB).
        _STAGE1_FULL_GPU_MIN_VRAM_GB = 20.0
        _stage1_allow_full_gpu = _vram_gb >= _STAGE1_FULL_GPU_MIN_VRAM_GB

        # Pixel count that separates Stage 1 (half-res) from Stage 2 (full-res).
        # For 1280×704 output:  Stage 1 = 225 K px, Stage 2 = 901 K px → midpoint ≈ 600 K.
        # For 1920×1088 output: Stage 1 = 522 K px, Stage 2 = 2 M px → also correct.
        _STAGE2_PIXEL_THRESHOLD = 600_000

        # Stage 2 streaming count: safe and fast for the target GPU.
        # count=8 keeps ~1 GB of DiT weight pages on GPU alongside ~30 GB of activations
        # → total ≈ 31 GB, comfortably under 31.5 GB on a 32 GB card.
        # Smaller GPUs use a more conservative count to leave headroom for activations.
        if _vram_gb >= 32:
            _STAGE2_STREAMING_COUNT = 8
        elif _vram_gb >= 24:
            _STAGE2_STREAMING_COUNT = 4
        else:
            _STAGE2_STREAMING_COUNT = 2

        def _resolution_aware_call(
            self: Any,
            *args: Any,
            streaming_prefetch_count: int | None = None,
            width: int | None = None,
            height: int | None = None,
            **kwargs: Any,
        ) -> Any:
            if width is not None and height is not None:
                pixel_count = width * height
                if pixel_count > _STAGE2_PIXEL_THRESHOLD:
                    # Stage 2 (full resolution): force exactly _STAGE2_STREAMING_COUNT.
                    #
                    # Why force, not just cap?
                    # The official pipeline default is count=2. _STAGE2_STREAMING_COUNT is
                    # our GPU-calibrated optimum (e.g. 8 on 32 GB).  Simply capping would
                    # leave count=2 unchanged — no performance gain.  Forcing to 8 means
                    # 4× fewer PCIe layer-transfer round-trips per denoising step.
                    #
                    # Why not allow count=None?
                    # count=None builds the full DiT in GPU VRAM simultaneously, adding
                    # CUDA workspace overhead (~3-4 GB extra) on top of the ~30 GB Stage 2
                    # activation tensors.  This reliably overflows 32 GB cards into
                    # system-RAM paging at PCIe speeds (~125× slower than HBM).
                    #
                    # Edge case: user set vram_limit ≤ 10 GB on a large-VRAM GPU.
                    # Their count (e.g. 1) is smaller than _STAGE2_STREAMING_COUNT (8).
                    # We still force 8 because Stage 2 activation tensors (~30 GB)
                    # already exceed any single-digit VRAM cap anyway — the streaming
                    # count only controls weight-prefetch pages, not activation size.
                    streaming_prefetch_count = _STAGE2_STREAMING_COUNT
                elif _stage1_allow_full_gpu and streaming_prefetch_count is not None:
                    # Stage 1 (half resolution) on a high-VRAM GPU: drop streaming entirely.
                    # Full DiT on GPU is safe at half-res and removes all PCIe overhead.
                    streaming_prefetch_count = None

            return _orig_call(
                self,
                *args,
                streaming_prefetch_count=streaming_prefetch_count,
                width=width,
                height=height,
                **kwargs,
            )

        DiffusionStage.__call__ = _resolution_aware_call
        DiffusionStage._ltx_resolution_aware_patched = True
        logger.info(
            "[streaming] DiffusionStage resolution-aware patch installed: "
            "Stage 1 → %s | Stage 2 → count=%d  (GPU %.0f GB)",
            "count=None (full GPU)" if _stage1_allow_full_gpu else "pipeline-default",
            _STAGE2_STREAMING_COUNT,
            _vram_gb,
        )
    except Exception as exc:
        logger.warning("[streaming] Resolution-aware patch failed (non-fatal): %s", exc)


def try_sequential_offload_on_pipeline_state(state: Any) -> None:
    """若底层为 Diffusers 风格 API，尝试按层 CPU offload（显著变慢、降峰值）。"""
    if state is None:
        return
    root = getattr(state, "pipeline", state)
    candidates: list[Any] = [root]
    inner = getattr(root, "pipeline", None)
    if inner is not None and inner is not root:
        candidates.append(inner)
    for obj in candidates:
        for method_name in (
            "enable_sequential_cpu_offload",
            "enable_model_cpu_offload",
        ):
            fn = getattr(obj, method_name, None)
            if callable(fn):
                try:
                    fn()
                    logger.info(
                        "low_vram_mode: 已对管线调用 %s()",
                        method_name,
                    )
                    return
                except Exception as e:
                    logger.debug(
                        "low_vram_mode: %s() 失败（可忽略）: %s",
                        method_name,
                        e,
                    )


def maybe_release_pipeline_after_task(handler: Any) -> None:
    """单次生成结束后：低显存模式下强制卸载管线并回收缓存。"""
    pl = getattr(handler, "pipelines", None) or getattr(handler, "_pipelines", None)
    if pl is None or not getattr(pl, "low_vram_mode", False):
        return
    try:
        from keep_models_runtime import force_unload_gpu_pipeline

        force_unload_gpu_pipeline(pl)
    except Exception as e:
        logger.debug("low_vram_mode: 任务后卸载失败: %s", e)
    try:
        pl._pipeline_signature = None
    except Exception:
        pass
    gc.collect()
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass
