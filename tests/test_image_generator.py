from __future__ import annotations

import base64
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))

from image_generator import ImageGenerator


def test_image_generator_generates_and_saves_image_bytes(tmp_path) -> None:
    fake_payload = base64.b64encode(b"fake-image-bytes").decode("ascii")
    fake_client = SimpleNamespace(
        images=SimpleNamespace(
            generate=MagicMock(
                return_value=SimpleNamespace(data=[SimpleNamespace(b64_json=fake_payload)])
            )
        )
    )
    client_factory = MagicMock(return_value=fake_client)

    generator = ImageGenerator("gemini-2.5-flash-image", client_factory=client_factory)

    image_bytes = generator.generate_image("A glowing dragon over a castle")

    assert image_bytes == b"fake-image-bytes"
    client_factory.assert_called_once_with("gemini-2.5-flash-image")
    fake_client.images.generate.assert_called_once_with(
        model="gemini-2.5-flash-image",
        prompt="A glowing dragon over a castle",
    )

    output_path = tmp_path / "generated" / "page_001.png"
    saved_path = generator.save_image(image_bytes, output_path)

    assert saved_path == output_path
    assert output_path.read_bytes() == b"fake-image-bytes"


def test_image_generator_raises_when_response_has_no_image_payload() -> None:
    fake_client = SimpleNamespace(
        images=SimpleNamespace(generate=MagicMock(return_value=SimpleNamespace(data=[])))
    )
    generator = ImageGenerator("gemini-2.5-flash-image", client_factory=lambda _: fake_client)

    with pytest.raises(ValueError, match="No image data returned"):
        generator.generate_image("A blank scene")
