from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from analyzer import WorldStateCheckpoint, analyze_story
from scraper import RawTextCheckpoint, scrape_scrybequill
from scriptwriter import ScriptCheckpoint, write_script


class ComicPipeline:
    def __init__(
        self,
        url: str,
        checkpoint_dir: Path = Path("checkpoints"),
        analysis_model: str = "qwen2.5:7b",
        script_model: str = "qwen2.5:7b",
        panel_count: int = 6,
    ):
        self.url = url
        self.checkpoint_dir = checkpoint_dir
        self.analysis_model = analysis_model
        self.script_model = script_model
        self.panel_count = panel_count

    async def run(self) -> dict[str, dict]:
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)

        raw_path = self.checkpoint_dir / "01_raw_text.json"
        entities_path = self.checkpoint_dir / "02_entities.json"
        script_path = self.checkpoint_dir / "03_script.json"

        if raw_path.exists():
            raw = RawTextCheckpoint.model_validate_json(raw_path.read_text(encoding="utf-8"))
        else:
            raw = await scrape_scrybequill(url=self.url, checkpoint_path=raw_path)

        if entities_path.exists():
            entities = WorldStateCheckpoint.model_validate_json(
                entities_path.read_text(encoding="utf-8")
            )
        else:
            entities = analyze_story(
                raw_checkpoint_path=raw_path,
                output_path=entities_path,
                model=self.analysis_model,
            )

        if script_path.exists():
            script = ScriptCheckpoint.model_validate_json(script_path.read_text(encoding="utf-8"))
        else:
            script = write_script(
                raw_checkpoint_path=raw_path,
                entities_checkpoint_path=entities_path,
                output_path=script_path,
                model=self.script_model,
                panel_count=self.panel_count,
            )

        return {
            "raw_text": raw.model_dump(),
            "entities": entities.model_dump(),
            "script": script.model_dump(),
        }


async def _run_cli() -> None:
    parser = argparse.ArgumentParser(description="Run checkpoint-aware comic pipeline.")
    parser.add_argument("url", help="ScrybeQuill story URL")
    parser.add_argument(
        "--checkpoint-dir",
        default="checkpoints",
        help="Directory for JSON checkpoints",
    )
    parser.add_argument(
        "--analysis-model",
        default="qwen2.5:7b",
        help="Ollama model name used for Phase 2 analysis",
    )
    parser.add_argument(
        "--script-model",
        default="qwen2.5:7b",
        help="Ollama model name used for Phase 3 scripting",
    )
    parser.add_argument(
        "--panel-count",
        default=6,
        type=int,
        help="Number of comic panels to generate in Phase 3",
    )

    args = parser.parse_args()
    pipeline = ComicPipeline(
        url=args.url,
        checkpoint_dir=Path(args.checkpoint_dir),
        analysis_model=args.analysis_model,
        script_model=args.script_model,
        panel_count=args.panel_count,
    )
    result = await pipeline.run()
    print(json.dumps({"status": "ok", "checkpoints": list(result.keys())}, indent=2))


if __name__ == "__main__":
    asyncio.run(_run_cli())
