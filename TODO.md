## Pipeline & Core

- [ ] Add a second pass for continuity and fun
- [ ] remove validation (e.g. carried items)
- [ ] panel stitching
---

## Plan: Implement Panel-by-Panel Generation Mode

This plan introduces a new "panel" generation mode to the pipeline. In this mode, image prompts are generated for each panel individually, and the resulting images are then stitched together to form a complete page. This approach will provide more granular control over image generation and lay the groundwork for more flexible page layouts in the future.

### Phase 1: Core Panel Generation Logic

This phase focuses on modifying the pipeline to support the new generation mode, from configuration to prompt generation.

**Steps**
- [x] 1. **Update Pipeline Configuration**:
    *   In [src/pipeline_config.py](src/pipeline_config.py), modify the `RunConfig` class to include a `generation_mode` field. This field will accept either `"page"` or `"panel"` as values, with `"page"` as the default.
    *   This will allow the pipeline to switch between the existing page-based generation and the new panel-based generation.

- [x] 2. **Adapt Pipeline Orchestration**:
    *   In [src/pipeline.py](src/pipeline.py), update the `ComicPipeline.run()` method to check the `generation_mode`.
    *   If the mode is `"panel"`, the pipeline logic will need to iterate through each panel within a page's script checkpoint (`03_script_page_*.json`) to generate individual image prompts.
    *   The output should be one prompt file per panel (e.g., `04_page_1_panel_1_prompt.txt`, `04_page_1_panel_2_prompt.txt`, etc.) instead of one per page.

- [ ] 3. **Refine Prompter for Panel-Level Context**:
    *   In [src/prompter.py](src/prompter.py), adjust the `Prompter` class to generate prompts at the panel level when in `"panel"` mode.
    *   The existing character filtering logic (`_character_is_referenced()`) should be applied to the text of a single panel, rather than the entire page. This will ensure that only characters present in a specific panel are included in its image prompt.
    *   Modify the prompt templates to remove page-level context like page numbers or episode titles from individual panel prompts.

**Relevant files**
- [src/pipeline_config.py](src/pipeline_config.py) — To add the `generation_mode` flag to `RunConfig`.
- [src/pipeline.py](src/pipeline.py) — To modify the main pipeline logic to handle the new mode.
- [src/prompter.py](src/prompter.py) — To adapt prompt generation and character filtering to the panel level.

### Phase 2: Image Stitching and GUI Integration

This phase covers combining the generated panel images into a single page and exposing the new mode in the user interface.

**Steps**
- [ ] 1. **Develop a Robust Image Stitcher**:
    *   Create a new module, `src/image_stitcher.py`, to handle the combination of panel images.
    *   This module will replace the functionality of the existing [src/combine_temp_images.py](src/combine_temp_images.py) script with a more production-ready solution.
    *   The stitcher should initially support a basic grid layout but be designed with future flexibility in mind. It should read panel metadata (like `panel_scale` and `panel_shape` from the script checkpoint) to determine the layout.

- [ ] 2. **Integrate Stitcher into Pipeline**:
    *   In [src/pipeline.py](src/pipeline.py), add a new pipeline stage that runs the image stitcher after the panel images have been generated in `"panel"` mode.
    *   The stitcher will take the individual panel images and the corresponding script checkpoint as input to produce a final page image.

- [ ] 3. **Update GUI for Mode Selection**:
    *   In [src/gui.py](src/gui.py), add a control (e.g., a dropdown or radio buttons) to allow the user to select the `generation_mode` (`"Page by Page"` or `"Panel by Panel"`).
    *   This selection will be passed to the `RunConfig` when a pipeline run is initiated.

**Relevant files**
- `src/image_stitcher.py` (new file) — For the new production-ready image stitching logic.
- [src/pipeline.py](src/pipeline.py) — To integrate the new stitching step.
- [src/gui.py](src/gui.py) — To add the UI control for selecting the generation mode.

### Phase 3: Advanced Layouts and Future Considerations

This phase outlines the steps for supporting more complex and dynamic page layouts, building on the foundation established in the previous phases.

**Steps**
- [ ] 1. **Enhance the Layout Engine**:
    *   Evolve the `image_stitcher.py` module into a more sophisticated layout engine.
    *   Implement logic to support variable panel sizes and positions based on the `panel_scale` and `panel_shape` attributes in the `Panel` objects.
    *   This will enable layouts where panels can have different dimensions (e.g., a panel taking up 3/4 of a page).

- [ ] 2. **Support for Overlays and Annotations**:
    *   Extend the layout engine to handle elements that are not strictly panels, such as overlapping panels, text annotations between panels, and page numbers.
    *   This may require adding new object types to the script checkpoint schema to represent these elements.

**Relevant files**
- `src/image_stitcher.py` — To be enhanced into an advanced layout engine.
- [src/scriptwriter.py](src/scriptwriter.py) — May need modifications to support new layout elements in the script.

**Verification**
1.  Run the pipeline in `"panel"` mode and verify that individual image prompts are created for each panel.
2.  Confirm that the generated panel images are correctly stitched together into a final page image that reflects a simple grid layout.
3.  After implementing Phase 3, create test cases with varied `panel_scale` and `panel_shape` values to ensure the layout engine arranges panels correctly.
4.  Use the "Run Pytest" task to ensure that existing tests continue to pass after these changes.