"""Utilities for generating and saving images with Gemini/OpenAI-compatible image models."""

from __future__ import annotations

import base64
import re
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

        base_name = output_path.stem
        suffix = output_path.suffix
        versioned_files = sorted(
            output_path.parent.glob(f"{base_name}_v*.png") if suffix == ".png" else output_path.parent.glob(f"{base_name}_v*{suffix}"),
            key=lambda path: _version_number(path.name, base_name, suffix),
        )

        for candidate in reversed(versioned_files):
            version = _version_number(candidate.name, base_name, suffix)
            if version is None:
                continue
            target = candidate.with_name(f"{base_name}_v{version + 1}{suffix}")
            candidate.replace(target)

        if output_path.exists():
            output_path.replace(output_path.with_name(f"{base_name}_v1{suffix}"))

        output_path.write_bytes(image_bytes)
        return output_path


def _version_number(filename: str, base_name: str, suffix: str) -> int | None:
    match = re.fullmatch(rf"{re.escape(base_name)}_v(\d+)" + re.escape(suffix), filename)
    return int(match.group(1)) if match else None
