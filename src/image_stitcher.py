from __future__ import annotations

import math
import re
from pathlib import Path

from PIL import Image


def _target_col_row_ratio(aspect_ratio: str) -> float:
    """Target width/height ratio of the panel grid (cols divided by rows)."""
    if aspect_ratio == "4:3":
        return 0.75
    if aspect_ratio == "1:1":
        return 1.0
    return 1.5


def _grid_size(panel_count: int, aspect_ratio: str = "3:2") -> tuple[int, int]:
    """Choose a panel grid biased toward the page aspect ratio."""
    if panel_count <= 1:
        return 1, 1

    target_ratio = _target_col_row_ratio(aspect_ratio)
    best_cols = 1
    best_rows = panel_count
    best_score = float("inf")

    for cols in range(1, panel_count + 1):
        rows = math.ceil(panel_count / cols)
        empty_cells = cols * rows - panel_count
        ratio = cols / rows
        score = (empty_cells * 10) + abs(math.log(ratio / target_ratio))
        if score < best_score:
            best_score = score
            best_cols = cols
            best_rows = rows

    return best_cols, best_rows


def _rotate_existing_output(output_path: Path) -> None:
    base_name = output_path.stem
    suffix = output_path.suffix
    versioned_files = sorted(
        output_path.parent.glob(f"{base_name}_v*{suffix}"),
        key=lambda path: _version_number(path.name, base_name, suffix),
    )

    for candidate in reversed(versioned_files):
        version = _version_number(candidate.name, base_name, suffix)
        if version is None:
            continue
        candidate.replace(candidate.with_name(f"{base_name}_v{version + 1}{suffix}"))

    if output_path.exists():
        output_path.replace(output_path.with_name(f"{base_name}_v1{suffix}"))


def _version_number(filename: str, base_name: str, suffix: str) -> int | None:
    match = re.fullmatch(rf"{re.escape(base_name)}_v(\d+)" + re.escape(suffix), filename)
    return int(match.group(1)) if match else None


def stitch_panel_images(
    image_paths: list[Path],
    output_path: Path,
    aspect_ratio: str = "3:2",
    gutter: int = 24,
    bg_color: tuple[int, int, int] = (255, 255, 255),
) -> Path:
    """Create one comic page from panel images using a grid shaped by *aspect_ratio*."""
    if not image_paths:
        raise ValueError("No images were provided to stitch.")

    images = [Image.open(path).convert("RGB") for path in image_paths]
    try:
        frame_width, frame_height = images[0].size
        cols, rows = _grid_size(len(images), aspect_ratio=aspect_ratio)

        page_width = frame_width * cols + gutter * (cols + 1)
        page_height = frame_height * rows + gutter * (rows + 1)

        page = Image.new("RGB", (page_width, page_height), bg_color)

        _rotate_existing_output(output_path)

        for index, image in enumerate(images):
            row = index // cols
            col = index % cols
            x = gutter + col * (frame_width + gutter)
            y = gutter + row * (frame_height + gutter)
            page.paste(image, (x, y))

        output_path.parent.mkdir(parents=True, exist_ok=True)
        page.save(output_path, format="PNG")
    finally:
        for image in images:
            image.close()

    return output_path
