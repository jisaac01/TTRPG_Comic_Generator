from __future__ import annotations

import base64
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))

from image_generator import (
    ImageGenerator,
    generate_prompt_images,
    latest_images_dir,
    next_images_dir,
)


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


def test_save_image_overwrites_the_given_path(tmp_path: Path) -> None:
    output_path = tmp_path / "05_page_1.png"
    output_path.write_bytes(b"old")

    ImageGenerator(model="gemini-test").save_image(b"new", output_path)

    assert output_path.read_bytes() == b"new"
    assert list(tmp_path.glob("*.png")) == [output_path]


def test_next_images_dir_creates_incrementing_version_folders(tmp_path: Path) -> None:
    version_dir = tmp_path / "v001"
    version_dir.mkdir()

    first = next_images_dir(version_dir)
    second = next_images_dir(version_dir)

    assert first == version_dir / "images" / "v001"
    assert second == version_dir / "images" / "v002"
    assert first.is_dir()
    assert second.is_dir()
    assert latest_images_dir(version_dir) == second


class _FakeGenerator:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    def generate_image(self, prompt: str) -> bytes:
        self.prompts.append(prompt)
        return f"img:{prompt}".encode()

    def save_image(self, image_bytes: bytes, output_path: Path) -> Path:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(image_bytes)
        return output_path


def test_generate_prompt_images_writes_into_a_new_images_folder(tmp_path: Path) -> None:
    version_dir = tmp_path / "v001"
    version_dir.mkdir()
    prompt = version_dir / "04_page_1_prompt.txt"
    prompt.write_text("a marsh at dusk", encoding="utf-8")
    generator = _FakeGenerator()

    result = generate_prompt_images(
        version_dir,
        [prompt],
        model="gemini-test",
        generator=generator,
    )

    image_path = version_dir / "images" / "v001" / "05_page_1.png"
    assert result.images_dir == version_dir / "images" / "v001"
    assert result.generated_paths == [image_path]
    assert image_path.read_bytes() == b"img:a marsh at dusk"
    assert generator.prompts == ["a marsh at dusk"]


def test_generate_prompt_images_second_run_uses_the_next_folder(tmp_path: Path) -> None:
    version_dir = tmp_path / "v001"
    version_dir.mkdir()
    prompt = version_dir / "04_page_1_prompt.txt"
    prompt.write_text("first", encoding="utf-8")
    generator = _FakeGenerator()

    generate_prompt_images(version_dir, [prompt], model="gemini-test", generator=generator)
    prompt.write_text("second", encoding="utf-8")
    result = generate_prompt_images(
        version_dir, [prompt], model="gemini-test", generator=generator
    )

    first = version_dir / "images" / "v001" / "05_page_1.png"
    second = version_dir / "images" / "v002" / "05_page_1.png"
    assert first.read_bytes() == b"img:first"
    assert second.read_bytes() == b"img:second"
    assert result.images_dir == version_dir / "images" / "v002"


def test_generate_prompt_images_one_prompt_writes_only_that_image(tmp_path: Path) -> None:
    version_dir = tmp_path / "v001"
    version_dir.mkdir()
    page_one = version_dir / "04_page_1_prompt.txt"
    page_two = version_dir / "04_page_2_prompt.txt"
    page_one.write_text("one", encoding="utf-8")
    page_two.write_text("two", encoding="utf-8")
    generator = _FakeGenerator()

    result = generate_prompt_images(
        version_dir,
        [page_two],
        model="gemini-test",
        generator=generator,
    )

    assert (version_dir / "images" / "v001" / "05_page_2.png").exists()
    assert result.generated_paths == [version_dir / "images" / "v001" / "05_page_2.png"]
    assert generator.prompts == ["two"]


def test_generate_prompt_images_new_folder_leaves_failed_pages_missing(tmp_path: Path) -> None:
    version_dir = tmp_path / "v001"
    version_dir.mkdir()
    page_one = version_dir / "04_page_1_prompt.txt"
    page_two = version_dir / "04_page_2_prompt.txt"
    page_one.write_text("one", encoding="utf-8")
    page_two.write_text("two", encoding="utf-8")
    generator = _FakeGenerator()

    generate_prompt_images(
        version_dir, [page_one, page_two], model="gemini-test", generator=generator
    )

    class _FailPageTwo(_FakeGenerator):
        def generate_image(self, prompt: str) -> bytes:
            if prompt == "two":
                raise ValueError("No image data returned from image generation response")
            return super().generate_image(prompt)

    result = generate_prompt_images(
        version_dir, [page_one, page_two], model="gemini-test", generator=_FailPageTwo()
    )

    latest = version_dir / "images" / "v002"
    assert result.images_dir == latest
    assert (latest / "05_page_1.png").read_bytes() == b"img:one"
    assert result.errors
    png_names = sorted(path.name for path in latest.glob("*.png"))
    assert png_names == ["05_page_1.png"]


def test_generate_prompt_images_single_regen_adds_suffix_in_latest_folder(
    tmp_path: Path,
) -> None:
    version_dir = tmp_path / "v001"
    version_dir.mkdir()
    page_one = version_dir / "04_page_1_prompt.txt"
    page_two = version_dir / "04_page_2_prompt.txt"
    page_one.write_text("one", encoding="utf-8")
    page_two.write_text("two", encoding="utf-8")
    generator = _FakeGenerator()

    generate_prompt_images(
        version_dir, [page_one, page_two], model="gemini-test", generator=generator
    )
    page_two.write_text("two-b", encoding="utf-8")
    result = generate_prompt_images(
        version_dir,
        [page_two],
        model="gemini-test",
        generator=generator,
        new_folder=False,
    )
    second = generate_prompt_images(
        version_dir,
        [page_two],
        model="gemini-test",
        generator=generator,
        new_folder=False,
    )

    images_dir = version_dir / "images" / "v001"
    assert result.images_dir == images_dir
    assert second.images_dir == images_dir
    assert (images_dir / "05_page_1.png").read_bytes() == b"img:one"
    assert (images_dir / "05_page_2.png").read_bytes() == b"img:two"
    assert (images_dir / "05_page_2_v1.png").read_bytes() == b"img:two-b"
    assert second.generated_paths == [images_dir / "05_page_2_v2.png"]
    assert (images_dir / "05_page_2_v2.png").exists()
    assert latest_images_dir(version_dir) == images_dir


def test_generate_prompt_images_panel_mode_stitches_into_the_same_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    version_dir = tmp_path / "v001"
    version_dir.mkdir()
    panel_one = version_dir / "04_page_1_panel_1_prompt.txt"
    panel_two = version_dir / "04_page_1_panel_2_prompt.txt"
    panel_one.write_text("p1", encoding="utf-8")
    panel_two.write_text("p2", encoding="utf-8")
    stitched: list[Path] = []

    def _fake_stitch(image_paths: list[Path], output_path: Path, **_kwargs: object) -> Path:
        output_path.write_bytes(b"stitched")
        stitched.append(output_path)
        return output_path

    monkeypatch.setattr("image_generator.stitch_panel_images", _fake_stitch)
    generator = _FakeGenerator()

    result = generate_prompt_images(
        version_dir,
        [panel_one, panel_two],
        model="gemini-test",
        stitch=True,
        generator=generator,
    )

    images_dir = version_dir / "images" / "v001"
    assert (images_dir / "05_page_1_panel_1.png").exists()
    assert (images_dir / "05_page_1_panel_2.png").exists()
    assert result.stitched_paths == [images_dir / "06_page_1.png"]
    assert stitched == [images_dir / "06_page_1.png"]
    assert (images_dir / "06_page_1.png").read_bytes() == b"stitched"


def test_stitch_images_dir_uses_canonical_panel_files_not_regen_suffixes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from image_generator import stitch_images_dir

    images_dir = tmp_path / "images" / "v001"
    images_dir.mkdir(parents=True)
    (images_dir / "05_page_1_panel_1.png").write_bytes(b"p1")
    (images_dir / "05_page_1_panel_2.png").write_bytes(b"p2")
    (images_dir / "05_page_1_panel_1_v1.png").write_bytes(b"retry")
    received: list[list[str]] = []

    def _fake_stitch(image_paths: list[Path], output_path: Path, **_kwargs: object) -> Path:
        received.append([path.name for path in image_paths])
        output_path.write_bytes(b"stitched")
        return output_path

    monkeypatch.setattr("image_generator.stitch_panel_images", _fake_stitch)

    stitch_images_dir(images_dir)

    assert received == [["05_page_1_panel_1.png", "05_page_1_panel_2.png"]]
