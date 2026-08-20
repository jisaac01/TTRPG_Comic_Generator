"""Utilities for generating and saving images with Gemini/OpenAI-compatible image models."""

from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Protocol

from image_stitcher import stitch_panel_images
from llm_client import build_openai_client

IMAGES_SUBDIR_NAME = "images"
_PROMPT_NAME_RE = re.compile(r"04_page_(\d+)(?:_panel_(\d+))?_prompt\.txt$")
_PANEL_IMAGE_RE = re.compile(r"05_page_(\d+)_panel_(\d+)\.png$")
_VERSION_DIR_RE = re.compile(r"v\d{3}")


class _ImageClient(Protocol):
    def generate(self, *, model: str, prompt: str) -> object:
        ...


@dataclass
class GenerateImagesResult:
    images_dir: Path
    generated_paths: list[Path] = field(default_factory=list)
    stitched_paths: list[Path] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


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


def _images_root(version_dir: Path) -> Path:
    return version_dir / IMAGES_SUBDIR_NAME


def _image_version_dirs(version_dir: Path) -> list[Path]:
    root = _images_root(version_dir)
    if not root.is_dir():
        return []
    return sorted(
        (path for path in root.iterdir() if path.is_dir() and _VERSION_DIR_RE.fullmatch(path.name)),
        key=lambda path: int(path.name[1:]),
    )


def latest_images_dir(version_dir: Path) -> Path | None:
    dirs = _image_version_dirs(version_dir)
    return dirs[-1] if dirs else None


def next_images_dir(version_dir: Path) -> Path:
    existing = _image_version_dirs(version_dir)
    if not existing:
        name = "v001"
    else:
        last_num = int(existing[-1].name[1:])
        name = f"v{last_num + 1:03d}"
    dest = _images_root(version_dir) / name
    dest.mkdir(parents=True, exist_ok=True)
    return dest


def prompt_image_filename(prompt_name: str) -> str | None:
    match = _PROMPT_NAME_RE.fullmatch(prompt_name)
    if match is None:
        return None
    page_number = match.group(1)
    panel_number = match.group(2)
    if panel_number is not None:
        return f"05_page_{page_number}_panel_{panel_number}.png"
    return f"05_page_{page_number}.png"


def prompt_sort_key(path: Path) -> tuple[int, int]:
    match = _PROMPT_NAME_RE.fullmatch(path.name)
    if match is None:
        return (10**9, 10**9)
    return (int(match.group(1)), int(match.group(2) or 0))


def panel_image_sort_key(path: Path) -> tuple[int, int]:
    match = _PANEL_IMAGE_RE.fullmatch(path.name)
    if match is None:
        return (10**9, 10**9)
    return (int(match.group(1)), int(match.group(2)))


def list_panel_images(images_dir: Path) -> list[Path]:
    return sorted(
        (
            path
            for path in images_dir.glob("05_page_*_panel_*.png")
            if _PANEL_IMAGE_RE.fullmatch(path.name)
        ),
        key=panel_image_sort_key,
    )


def next_image_output_path(images_dir: Path, filename: str) -> Path:
    """Return canonical path, or stem_vN.suffix if that file already exists."""
    canonical = images_dir / filename
    if not canonical.exists():
        return canonical
    stem = canonical.stem
    suffix = canonical.suffix
    pattern = re.compile(rf"{re.escape(stem)}_v(\d+)$")
    versions = []
    for path in images_dir.iterdir():
        if not path.is_file() or path.suffix != suffix:
            continue
        match = pattern.fullmatch(path.stem)
        if match:
            versions.append(int(match.group(1)))
    next_n = max(versions, default=0) + 1
    return images_dir / f"{stem}_v{next_n}{suffix}"


def stitch_images_dir(
    images_dir: Path, *, aspect_ratio: str = "3:2"
) -> tuple[list[Path], list[str]]:
    panel_paths = list_panel_images(images_dir)
    if not panel_paths:
        return [], []

    pages = sorted({panel_image_sort_key(path)[0] for path in panel_paths})
    stitched_paths: list[Path] = []
    errors: list[str] = []
    for page_number in pages:
        page_paths = [
            path for path in panel_paths if panel_image_sort_key(path)[0] == page_number
        ]
        output_path = images_dir / f"06_page_{page_number}.png"
        try:
            stitch_panel_images(page_paths, output_path, aspect_ratio=aspect_ratio)
            stitched_paths.append(output_path)
        except Exception as exc:
            errors.append(f"stitching: page {page_number}: {exc}")
    return stitched_paths, errors


def generate_prompt_images(
    version_dir: Path,
    prompt_paths: list[Path],
    *,
    model: str,
    aspect_ratio: str = "3:2",
    stitch: bool = False,
    generator: ImageGenerator | None = None,
    new_folder: bool = True,
) -> GenerateImagesResult:
    if not prompt_paths:
        raise ValueError("No prompt files available to generate images")

    if new_folder:
        images_dir = next_images_dir(version_dir)
    else:
        images_dir = latest_images_dir(version_dir) or next_images_dir(version_dir)

    image_generator = generator or ImageGenerator(model=model)
    generated_paths: list[Path] = []
    errors: list[str] = []

    for prompt_path in sorted(prompt_paths, key=prompt_sort_key):
        filename = prompt_image_filename(prompt_path.name)
        if filename is None:
            errors.append(
                f"image_generation: unable to determine page number for {prompt_path.name}"
            )
            continue
        match = _PROMPT_NAME_RE.fullmatch(prompt_path.name)
        page_number = match.group(1) if match else prompt_path.name
        output_path = (
            images_dir / filename
            if new_folder
            else next_image_output_path(images_dir, filename)
        )
        try:
            prompt_text = prompt_path.read_text(encoding="utf-8")
            image_bytes = image_generator.generate_image(prompt_text)
            image_generator.save_image(image_bytes, output_path)
            generated_paths.append(output_path)
        except Exception as exc:
            errors.append(f"image_generation: page {page_number}: {exc}")

    stitched_paths: list[Path] = []
    if stitch:
        stitched_paths, stitch_errors = stitch_images_dir(
            images_dir, aspect_ratio=aspect_ratio
        )
        errors.extend(stitch_errors)

    return GenerateImagesResult(
        images_dir=images_dir,
        generated_paths=generated_paths,
        stitched_paths=stitched_paths,
        errors=errors,
    )


def append_image_generation_record(
    version_dir: Path,
    *,
    model: str,
    images_dir: Path,
    files: list[Path],
    source: str,
) -> dict:
    """Append an image-generation record to the version's run_status.json."""
    status_path = version_dir / "run_status.json"
    if not status_path.exists():
        raise FileNotFoundError(f"run_status.json not found in {version_dir}")
    status = json.loads(status_path.read_text(encoding="utf-8"))
    if not isinstance(status, dict):
        raise ValueError(f"run_status.json must be a JSON object: {status_path}")

    generations = status.get("image_generations")
    if generations is None:
        generations = []
    elif not isinstance(generations, list):
        raise ValueError(f"image_generations must be a list: {status_path}")

    try:
        relative_dir = images_dir.relative_to(version_dir).as_posix()
    except ValueError:
        relative_dir = images_dir.as_posix()

    generations.append(
        {
            "model": model,
            "images_dir": relative_dir,
            "files": [path.name for path in files],
            "source": source,
        }
    )
    status["image_generations"] = generations

    run_config = status.get("run_config")
    if not isinstance(run_config, dict):
        run_config = {}
        status["run_config"] = run_config
    run_config["image_generation_model"] = model

    status_path.write_text(json.dumps(status, indent=2) + "\n", encoding="utf-8")
    return status
