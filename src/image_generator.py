"""Utilities for generating and saving images with Gemini/OpenAI-compatible image models."""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Callable, Protocol

from llm_client import build_openai_client


class _ImageClient(Protocol):
    def generate(self, *, model: str, prompt: str) -> object:
        ...


class ImageGenerator:
    def __init__(
        self,
        model: str,
        client_factory: Callable[[str], object] = build_openai_client,
    ) -> None:
        self.model = model
        self._client_factory = client_factory

    def generate_image(self, prompt: str) -> bytes:
        client = self._client_factory(self.model)
        response = client.images.generate(model=self.model, prompt=prompt)

        data_items = getattr(response, "data", None) or []
        if not data_items:
            raise ValueError("No image data returned from image generation response")

        first_item = data_items[0]
        b64_json = getattr(first_item, "b64_json", None)
        if not b64_json:
            raise ValueError("No image payload returned from image generation response")

        return base64.b64decode(b64_json)

    def save_image(self, image_bytes: bytes, output_path: Path) -> Path:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(image_bytes)
        return output_path
