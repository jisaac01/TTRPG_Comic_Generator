## Pipeline & Core

- [ ] Add a second pass for continuity and fun
- [ ] remove validation (e.g. carried items)
- [ ] panel stitching
---

## Plan: Add Image Generation Pipeline Stage

This plan outlines the steps to add a new image generation stage to the processing pipeline. The stage will be off by default and can be triggered for an entire version or for individual image prompts from the GUI. We will also refactor model configuration to be more centralized.

**Steps**

- [x] 1.  **Update Configuration (`src/pipeline_config.py` and `src/settings_service.py`)**
    *   In `src/settings_service.py`:
        *   Add a new property `image_generation_model` to `SettingsService` with a getter and setter. The default value should be `gemini-2.5-flash-image`.
        *   The available models will be `gemini-2.5-flash-image`, `gemini-3.1-flash-image`, and `gemini-3-pro-image`.
    *   In `src/pipeline_config.py`:
        *   Add a new boolean field `generate_images: bool = False` to the `RunConfig` dataclass. This will control whether the image generation stage runs as part of a full pipeline execution.
        *   Remove `beater_model`, `script_model`, and `style_model`. These will now be managed by `SettingsService` as a single default.

- [x] 2.  **Create Image Generation Logic (`src/image_generator.py`)**
    *   Create a new file `src/image_generator.py`.
    *   Implement a class `ImageGenerator` that takes an `LLMClient` instance.
    *   Create a method `generate_image(prompt: str) -> bytes` that calls the image generation model via `llm_client` and returns the image data.
    *   Add a method `save_image(image_data: bytes, output_path: Path)`.

- [x] 3.  **Integrate Image Generation into Pipeline (`src/pipeline.py`)**
    *   Add a new pipeline stage `IMAGE_GENERATION = "Image Generation"`.
    *   Create a new private method `_run_image_generation_stage` in `ComicPipeline`.
    *   This method will:
        *   Check the `run_config.generate_images` flag. If `False`, the stage will be skipped unless it's the `rerun_from` target.
        *   Find all `04_page_*_prompt.txt` files in the version directory.
        *   For each prompt file, call the `ImageGenerator` to generate and save the image (e.g., as `05_page_001.png`).
        *   Emit `PhaseStarted`, `PhaseCompleted`, and `PhaseWarning` events as appropriate.
    *   Add the new stage to the main `run` method's execution flow.

- [x] 4.  **Update GUI for New Configuration (`src/gui.py`)**
    *   In the "Settings" tab:
        *   Remove the individual model selection dropdowns.
        *   Add a new dropdown for `image_generation_model` with the specified options.
    *   In the "Run" tab:
        *   Add a "Generate images" checkbox that sets the `generate_images` flag in the `RunConfig`.

- [ ] 5.  **Add GUI Controls for On-Demand Generation (`src/gui.py`)**
    *   In the "Output" tab, which displays the contents of a version folder:
        *   Add a "Generate Images" button that, when clicked, runs a new pipeline execution specifically for the `IMAGE_GENERATION` stage on the selected version.
        *   Next to each `..._prompt.txt` file listed, add an icon button (e.g., a play or refresh icon).
        *   Clicking this icon will trigger the generation of a single image for that specific prompt and save it to the version folder.

**Relevant files**
- `src/pipeline.py` — To add the new image generation stage.
- `src/pipeline_config.py` — To add the `generate_images` flag to `RunConfig` and remove model-specific fields.
- `src/settings_service.py` — To add the new `image_generation_model` setting.
- `src/gui.py` — To update the UI with the new settings and generation buttons.
- `src/image_generator.py` — **New file** to house the image generation logic.
- `src/llm_client.py` — To add a method for calling the image generation API.

**Verification**
1.  Run the full pipeline with "Generate images" unchecked and confirm no images are created.
2.  Run the full pipeline with "Generate images" checked and confirm images are created in the version folder.
3.  After a run is complete, navigate to the "Output" tab, select the version, and click the main "Generate Images" button. Verify it generates images for all prompts.
4.  On the "Output" tab, click the icon button next to a single prompt file and verify it generates/regenerates only that one image.
5.  Check that the image generation model can be changed in the settings and that the new setting is used for generation.

**Decisions**
- **Centralized Model Config**: Moving the text model configuration to `SettingsService` simplifies the `RunConfig` and makes the "Run" page cleaner. The image model will also be a global setting.
- **Default Off**: Image generation is an expensive operation, so it will be an explicit opt-in action, either via a checkbox for a full run or a button for a completed run. This prevents accidental costs.
- **Granular Control**: Providing both a full-version and a per-prompt generation trigger offers flexibility for iterating on art.


