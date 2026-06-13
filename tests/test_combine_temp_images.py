from pathlib import Path
from types import SimpleNamespace

from src.combine_temp_images import _sorted_image_paths


def test_sorted_image_paths_uses_oldest_to_newest_creation_order(tmp_path, monkeypatch):
    first = tmp_path / "frame_01.png"
    second = tmp_path / "frame_02.png"
    third = tmp_path / "frame_03.png"

    for path in (first, second, third):
        path.write_bytes(b"fake")

    timestamps = {
        first: 10.0,
        second: 30.0,
        third: 20.0,
    }

    def fake_stat(self):
        return SimpleNamespace(st_birthtime=timestamps[self], st_ctime=timestamps[self])

    monkeypatch.setattr(Path, "stat", fake_stat)

    ordered = _sorted_image_paths(tmp_path)

    assert [path.name for path in ordered] == ["frame_01.png", "frame_03.png", "frame_02.png"]
