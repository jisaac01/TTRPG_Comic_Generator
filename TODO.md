## Pipeline & Core

- [ ] Add a second pass for continuity and fun
- [ ] remove validation (e.g. carried items)
- [ ] camera angle, panel lighting
- [ ] figure out why the entities bible is created in the beater step and not in the entities step
- [ ] Does re-run start from the selected version, or just the last? 
- [x] Add a style selector (Run + Output; bundled + campaign `art_direction/`)
- [ ] Style template editing UX beyond Prompts-tab multi-style list
- [ ] Add entities_bible to prompts page for editing
- [ ] Allow editing version files
- [ ] Add "professional" pipeline tooling, whatever that means, for the resume
- [ ] Move character descriptions into the style step (so they are styled)
- [ ] Add some descriptors like age, hair style, height, skin color, to physical description prompt
- [ ] *** Convert the prompt to the markdown style and try it ***
- [ ] Add a json/markdown mode to output everything in those formats

---

## Plan: Implement Panel-by-Panel Generation Mode

This plan introduces a new "panel" generation mode to the pipeline. In this mode, image prompts are generated for each panel individually, and the resulting images are then stitched together to form a complete page. This approach will provide more granular control over image generation and lay the groundwork for more flexible page layouts in the future.

### Phase 1: Core Panel Generation Logic

This phase focuses on modifying the pipeline to support the new generation mode, from configuration to prompt generation. DONE. 

### Phase 2: Image Stitching and GUI Integration

This phase covers combining the generated panel images into a single page and exposing the new mode in the user interface. DONE.

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