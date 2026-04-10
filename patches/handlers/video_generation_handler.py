"""Video generation orchestration handler (patches/handlers/video_generation_handler.py).

This module shadows the official handlers/video_generation_handler.py via PYTHONPATH priority.

The generate() and generate_video() methods are monkey-patched by app_factory.py
(patched_generate / patched_generate_video), so only the helper methods used by
those patches need to live here: _prepare_image and _resolve_seed.

All other official methods (_generate_a2v, _generate_forced_api, etc.) were removed
because the monkey-patches never delegate to them.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from threading import RLock
from typing import TYPE_CHECKING

from PIL import Image

from api_types import (
    GenerateVideoRequest,
    GenerateVideoResponse,
)
from _routes._errors import HTTPError
from handlers.base import StateHandlerBase
from handlers.generation_handler import GenerationHandler
from handlers.pipelines_handler import PipelinesHandler
from handlers.text_handler import TextHandler
from server_utils.media_validation import validate_image_file
from services.interfaces import LTXAPIClient
from state.app_state_types import AppState

if TYPE_CHECKING:
    from runtime_config.runtime_config import RuntimeConfig

logger = logging.getLogger(__name__)


def _read_custom_encoder_path() -> str | None:
    """Return user-specified Gemma encoder directory override, or None to use default.
    Read from custom_text_encoder_path in LTXDesktop settings.json.
    / 从 settings.json 读取用户自定义 Gemma 编码器目录，未设置则返回 None。
    """
    cfg = Path(os.environ.get("LOCALAPPDATA", "")) / "LTXDesktop" / "settings.json"
    try:
        data = json.loads(cfg.read_text(encoding="utf-8"))
        p = data.get("custom_text_encoder_path", "").strip()
        return p if p else None
    except Exception:
        return None


class VideoGenerationHandler(StateHandlerBase):
    def __init__(
        self,
        state: AppState,
        lock: RLock,
        generation_handler: GenerationHandler,
        pipelines_handler: PipelinesHandler,
        text_handler: TextHandler,
        ltx_api_client: LTXAPIClient,
        config: RuntimeConfig,
    ) -> None:
        super().__init__(state, lock, config)
        self._generation = generation_handler
        self._pipelines = pipelines_handler
        self._text = text_handler
        self._ltx_api_client = ltx_api_client

    # generate() and generate_video() are monkey-patched by app_factory.py.
    # The stubs below satisfy static analysis and ensure the attributes exist
    # before the patch replaces them.

    def generate(self, req: GenerateVideoRequest) -> GenerateVideoResponse:  # type: ignore[override]
        raise NotImplementedError("replaced by app_factory.patched_generate")

    def generate_video(self, *args, **kwargs):
        raise NotImplementedError("replaced by app_factory.patched_generate_video")

    # -------------------------------------------------------------------------
    # Helpers used by patched_generate / patched_generate_video
    # -------------------------------------------------------------------------

    def _prepare_image(self, image_path: str, width: int, height: int) -> Image.Image:
        validated_path = validate_image_file(image_path)
        try:
            img = Image.open(validated_path).convert("RGB")
        except Exception:
            raise HTTPError(400, f"Invalid image file: {image_path}") from None
        img_w, img_h = img.size
        target_ratio = width / height
        img_ratio = img_w / img_h
        if img_ratio > target_ratio:
            new_h = height
            new_w = int(img_w * (height / img_h))
        else:
            new_w = width
            new_h = int(img_h * (width / img_w))
        resized = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
        left = (new_w - width) // 2
        top = (new_h - height) // 2
        return resized.crop((left, top, left + width, top + height))

    def _resolve_seed(self) -> int:
        import time
        settings = self.state.app_settings
        if settings.seed_locked:
            logger.info("Using locked seed: %s", settings.locked_seed)
            return settings.locked_seed
        return int(time.time()) % 2147483647
