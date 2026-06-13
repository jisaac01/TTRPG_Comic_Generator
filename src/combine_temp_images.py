#!/usr/bin/env python3
"""Combine a folder of generated images into one comic-page PNG."""

from __future__ import annotations

import argparse
import math
from pathlib import Path

from PIL import Image


def _sorted_image_paths(folder: Path, output_path: Path | None = None) -> list[Path]:
    def created_at(path: Path) -> float:
        stat = path.stat()
        return getattr(stat, "st_birthtime", stat.st_ctime)

    image_paths = [path for path in folder.glob("*.png") if path.is_file()]
    if output_path is not None:
        image_paths = [path for path in image_paths if path.resolve() != output_path.resolve()]

    return sorted(image_paths, key=lambda path: (created_at(path), path.name))


def _grid_size(frame_count: int) -> tuple[int, int]:
    cols = math.ceil(math.sqrt(frame_count))
    rows = math.ceil(frame_count / cols)
    return cols, rows


def create_comic_page(
    image_paths: list[Path],
    output_path: Path,
    gutter: int = 24,
    bg_color: tuple[int, int, int] = (255, 255, 255),
) -> Path:
    """Create one comic page from a list of image files."""
    if not image_paths:
        raise ValueError("No images were provided to combine.")

    images = [Image.open(path).convert("RGB") for path in image_paths]
    try:
        frame_width, frame_height = images[0].size
        cols, rows = _grid_size(len(images))

        page_width = frame_width * cols + gutter * (cols + 1)
        page_height = frame_height * rows + gutter * (rows + 1)

        page = Image.new("RGB", (page_width, page_height), bg_color)

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


def main() -> None:
    parser = argparse.ArgumentParser(description="Combine generated images into a single comic-page PNG.")
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=Path("temp_images"),
        help="Folder containing the images to combine.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("temp_images/comic_page.png"),
        help="Destination PNG for the final comic page.",
    )
    args = parser.parse_args()

    image_paths = _sorted_image_paths(args.input_dir.resolve(), args.output.resolve())
    if not image_paths:
        raise FileNotFoundError(f"No PNG images found in {args.input_dir}")

    cols, rows = _grid_size(len(image_paths))
    output_path = create_comic_page(image_paths, args.output.resolve(), gutter=24)
    print(f"Created {len(image_paths)}-frame page at {output_path}")
    print(f"Layout: {cols} columns x {rows} rows")


if __name__ == "__main__":
    main()
