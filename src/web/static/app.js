(() => {
  const STAGE_ORDER = ["scrape", "entities", "architect", "script", "style", "prompt"];
  const SETTING_MIN = {
    recap: "architect",
    panels: "architect",
    pages: "architect",
    vignette: "architect",
    generation_mode: "script",
    art_style: "style",
    aspect_ratio: "prompt",
    cache_buster: "prompt",
    unstyled_prompts: "prompt",
    chat_mode: "prompt",
    pg13_mode: "script",
  };

  const $ = (id) => document.getElementById(id);
  const state = {
    runEpisodes: [],
    outEpisodes: [],
    promptKey: "",
    outFile: null,
    outFiles: [],
    busy: false,
    runId: null,
    events: null,
    typedApiKey: false,
  };

  function encodeKey(key) {
    return key.split("/").map(encodeURIComponent).join("/");
  }

  async function api(path, options = {}) {
    const opts = { ...options };
    opts.headers = { ...(options.headers || {}) };
    if (opts.body && typeof opts.body !== "string") {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(opts.body);
    }
    const response = await fetch(path, opts);
    const text = await response.text();
    let data = null;
    if (text) {
      try {
        data = JSON.parse(text);
      } catch {
        data = text;
      }
    }
    if (!response.ok) {
      const detail = data && data.detail ? data.detail : response.statusText;
      throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    }
    return data;
  }

  function log(source, message) {
    const list = $("event-log");
    const item = document.createElement("li");
    const stamp = new Date().toISOString().slice(11, 19);
    item.textContent = `${stamp}  [${source}] ${message}`;
    list.appendChild(item);
    list.scrollTop = list.scrollHeight;
    while (list.children.length > 100) list.removeChild(list.firstChild);
  }

  function setBanner(lines) {
    const banner = $("banner");
    if (!lines.length) {
      banner.hidden = true;
      banner.textContent = "";
      return;
    }
    banner.hidden = false;
    banner.textContent = lines.join("\n");
  }

  function fillSelect(select, items, { value, label, selected } = {}) {
    const current = selected === undefined ? select.value : selected;
    select.innerHTML = "";
    for (const item of items) {
      const option = document.createElement("option");
      if (typeof item === "string") {
        option.value = item;
        option.textContent = item;
      } else {
        option.value = value ? item[value] : item.id;
        option.textContent = label ? item[label] : item.label;
      }
      select.appendChild(option);
    }
    if (current && [...select.options].some((opt) => opt.value === current)) {
      select.value = current;
    }
  }

  function showWorkspace(name) {
    document.querySelectorAll(".workspace").forEach((el) => {
      el.hidden = el.dataset.workspace !== name;
    });
    document.querySelectorAll(".tabs button").forEach((btn) => {
      btn.classList.toggle("active", btn.dataset.workspace === name);
    });
  }

  function stageEnabled(field, stage) {
    const min = SETTING_MIN[field];
    if (!min) return false;
    return STAGE_ORDER.indexOf(stage) <= STAGE_ORDER.indexOf(min);
  }

  async function loadCampaigns(preferred) {
    const data = await api("/api/campaigns");
    fillSelect($("run-campaign"), data.campaigns, { selected: preferred });
    fillSelect($("prompt-campaign"), data.campaigns, { selected: preferred });
    fillSelect($("out-campaign"), data.campaigns, { selected: preferred });
    return data.campaigns;
  }

  async function loadArtStyles(campaign, select) {
    if (!campaign) {
      select.innerHTML = "";
      return;
    }
    const data = await api(`/api/campaigns/${encodeURIComponent(campaign)}/art-styles`);
    fillSelect(select, data.styles);
  }

  function runPayload() {
    const mode = $("run-mode").value;
    const episode = state.runEpisodes.find((item) => item.slug === $("run-episode").value);
    const url =
      mode === "story_url"
        ? $("run-url").value.trim()
        : (episode && episode.url) || $("run-url").value.trim();
    return {
      url,
      campaign: $("run-campaign").value,
      rerun_from: mode === "story_url" ? "scrape" : $("run-stage").value,
      recap_version: $("run-recap").value,
      panel_count: Number($("run-panels").value),
      total_pages: Number($("run-pages").value),
      generation_mode: $("run-gen-mode").value,
      aspect_ratio: $("run-aspect").value,
      art_style: $("run-art-style").value || null,
      generate_images: $("run-generate-images").checked,
      vignette: $("run-vignette").checked,
      cache_buster: $("run-cache-buster").checked,
      unstyled_prompts: $("run-unstyled").checked,
      chat_mode: $("run-chat").checked,
      pg13_mode: $("run-pg13").checked,
    };
  }

  function setRunBusy(busy) {
    state.busy = busy;
    $("run-submit").disabled = busy;
    $("run-cancel").hidden = !busy;
    $("out-rerun").disabled = busy;
    $("out-generate").disabled = busy;
    $("out-test").disabled = busy;
    $("out-stitch").disabled = busy;
    $("out-generate-selected").disabled = busy;
  }

  function listenRun(runId) {
    if (state.events) state.events.close();
    state.runId = runId;
    const source = new EventSource(`/api/runs/${runId}/events`);
    state.events = source;
    source.onmessage = (event) => {
      const payload = JSON.parse(event.data);
      const label = payload.phase ? `${payload.phase}: ${payload.message || payload.type}` : payload.type;
      log("Run", label);
      if (payload.phase && payload.type === "PhaseStarted") {
        $("run-phase").textContent = `Stage: ${payload.phase} — ${payload.message || ""}`;
      }
      if (payload.type === "RunCompleted") {
        $("run-status").textContent = `Finished: ${payload.status} ${payload.version || ""}`;
        $("run-error").textContent = (payload.error_messages || []).join("\n");
        setRunBusy(false);
        source.close();
        state.events = null;
        refreshOutput();
      }
    };
    source.onerror = () => {
      source.close();
      state.events = null;
    };
  }

  async function refreshRunEpisodes() {
    const campaign = $("run-campaign").value;
    if (!campaign) {
      $("run-episode").innerHTML = "";
      state.runEpisodes = [];
      return;
    }
    const data = await api(`/api/campaigns/${encodeURIComponent(campaign)}/episodes`);
    state.runEpisodes = data.episodes;
    fillSelect($("run-episode"), data.episodes, { value: "slug", label: "title" });
    await loadArtStyles(campaign, $("run-art-style"));
  }

  function syncRunMode() {
    const story = $("run-mode").value === "story_url";
    $("run-url-wrap").hidden = !story;
    $("run-episode-wrap").hidden = story;
    $("run-stage").disabled = story;
    if (story) $("run-stage").value = "scrape";
  }

  async function loadPromptList() {
    const campaign = $("prompt-campaign").value;
    const list = $("prompt-files");
    list.innerHTML = "";
    if (!campaign) return;
    const data = await api(`/api/campaigns/${encodeURIComponent(campaign)}/prompts`);
    for (const item of data.prompts) {
      const li = document.createElement("li");
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = item.exists ? item.label : `${item.label} ✗`;
      button.dataset.key = item.key;
      if (item.key === state.promptKey) button.classList.add("active");
      button.addEventListener("click", () => loadPrompt(item.key));
      li.appendChild(button);
      list.appendChild(li);
    }
    if (!state.promptKey && data.prompts.length) {
      await loadPrompt(data.prompts[0].key);
    }
  }

  async function loadPrompt(key) {
    const campaign = $("prompt-campaign").value;
    state.promptKey = key;
    $("prompt-files")
      .querySelectorAll("button")
      .forEach((btn) => btn.classList.toggle("active", btn.dataset.key === key));
    const data = await api(
      `/api/campaigns/${encodeURIComponent(campaign)}/prompts/${encodeKey(key)}`
    );
    $("prompt-editor").value = data.content;
    $("prompt-status").textContent = `Loaded ${data.key}`;
  }

  function applyOutputGating() {
    const stage = $("out-stage").value;
    $("out-recap").disabled = !stageEnabled("recap", stage);
    $("out-panels").disabled = !stageEnabled("panels", stage);
    $("out-pages").disabled = !stageEnabled("pages", stage);
    $("out-vignette").disabled = !stageEnabled("vignette", stage);
    $("out-gen-mode").disabled = !stageEnabled("generation_mode", stage);
    $("out-art-style").disabled = !stageEnabled("art_style", stage);
    $("out-aspect").disabled = !stageEnabled("aspect_ratio", stage);
    $("out-cache-buster").disabled = !stageEnabled("cache_buster", stage);
    $("out-unstyled").disabled = !stageEnabled("unstyled_prompts", stage);
    $("out-chat").disabled = !stageEnabled("chat_mode", stage);
    $("out-pg13").disabled = !stageEnabled("pg13_mode", stage);
  }

  async function refreshOutputEpisodes() {
    const campaign = $("out-campaign").value;
    if (!campaign) {
      $("out-episode").innerHTML = "";
      state.outEpisodes = [];
      return;
    }
    const data = await api(`/api/campaigns/${encodeURIComponent(campaign)}/episodes`);
    state.outEpisodes = data.episodes;
    const options = data.episodes.map((ep) => ({
      slug: ep.slug,
      label: ep.has_images ? `${ep.title || ep.slug} 🖼` : ep.title || ep.slug,
    }));
    fillSelect($("out-episode"), options, { value: "slug", label: "label" });
    await loadArtStyles(campaign, $("out-art-style"));
    await refreshOutputVersions();
  }

  async function refreshOutputVersions() {
    const campaign = $("out-campaign").value;
    const episode = $("out-episode").value;
    if (!campaign || !episode) {
      $("out-version").innerHTML = "";
      return;
    }
    const data = await api(
      `/api/campaigns/${encodeURIComponent(campaign)}/episodes/${encodeURIComponent(episode)}/versions`
    );
    fillSelect($("out-version"), data.versions, { value: "version", label: "label" });
    const historical = data.versions.filter((item) => item.version !== "working");
    if (historical.length) $("out-version").value = historical[historical.length - 1].version;
    await refreshOutputFiles();
  }

  async function refreshOutput() {
    if ($("out-campaign").value) await refreshOutputEpisodes();
  }

  function selectedOutput() {
    return {
      campaign: $("out-campaign").value,
      episode: $("out-episode").value,
      version: $("out-version").value,
    };
  }

  function outputFileUrl(kind, key) {
    const { campaign, episode, version } = selectedOutput();
    return `/api/campaigns/${encodeURIComponent(campaign)}/episodes/${encodeURIComponent(episode)}/versions/${encodeURIComponent(version)}/${kind}/${encodeKey(key)}`;
  }

  async function refreshOutputFiles() {
    const { campaign, episode, version } = selectedOutput();
    const list = $("out-files");
    list.innerHTML = "";
    state.outFiles = [];
    $("out-star").hidden = true;
    $("out-note-wrap").hidden = true;
    if (!campaign || !episode || !version) return;
    const versions = await api(
      `/api/campaigns/${encodeURIComponent(campaign)}/episodes/${encodeURIComponent(episode)}/versions`
    );
    const info = versions.versions.find((item) => item.version === version);
    const historical = Boolean(info && !info.editable);
    $("out-star").hidden = !historical;
    $("out-note-wrap").hidden = !historical;
    if (historical && info) {
      $("out-star").textContent = info.starred ? "★ Starred" : "Star";
      $("out-note").value = info.description || "";
    }
    try {
      const status = await api(
        `/api/campaigns/${encodeURIComponent(campaign)}/episodes/${encodeURIComponent(episode)}/versions/${encodeURIComponent(version)}/status`
      );
      $("out-run-status").textContent = `status=${status.status || "unknown"}`;
      const cfg = status.run_config || {};
      if (cfg.recap_version) $("out-recap").value = cfg.recap_version;
      if (cfg.panel_count) $("out-panels").value = cfg.panel_count;
      if (cfg.total_pages) $("out-pages").value = cfg.total_pages;
      if (cfg.generation_mode) $("out-gen-mode").value = cfg.generation_mode;
      if (cfg.aspect_ratio) $("out-aspect").value = cfg.aspect_ratio;
      if (cfg.art_style) $("out-art-style").value = cfg.art_style;
      $("out-vignette").checked = Boolean(cfg.vignette);
      $("out-cache-buster").checked = cfg.cache_buster !== false;
      $("out-unstyled").checked = Boolean(cfg.unstyled_prompts);
      $("out-chat").checked = Boolean(cfg.chat_mode);
      $("out-pg13").checked = Boolean(cfg.pg13_mode);
    } catch {
      $("out-run-status").textContent = "";
    }
    applyOutputGating();
    const files = await api(
      `/api/campaigns/${encodeURIComponent(campaign)}/episodes/${encodeURIComponent(episode)}/versions/${encodeURIComponent(version)}/files`
    );
    state.outFiles = files.files;
    for (const file of files.files) {
      const li = document.createElement("li");
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = file.exists ? file.key : `${file.key} (new)`;
      button.dataset.key = file.key;
      button.addEventListener("click", () => loadOutputFile(file));
      li.appendChild(button);
      list.appendChild(li);
    }
    const preferred =
      files.files.find((file) => file.key === "04_page_1_prompt.txt") ||
      files.files.find((file) => file.key === "run_status.json") ||
      files.files[0];
    if (preferred) await loadOutputFile(preferred);
  }

  async function loadOutputFile(file) {
    state.outFile = file;
    $("out-files")
      .querySelectorAll("button")
      .forEach((btn) => btn.classList.toggle("active", btn.dataset.key === file.key));
    const working = $("out-version").value === "working";
    const editable = working && file.kind === "text";
    $("out-save").disabled = !editable;
    $("out-reload").disabled = !working;
    const isPrompt = file.key.startsWith("04_page_") && file.key.endsWith("_prompt.txt");
    $("out-generate-selected").hidden = !isPrompt;
    if (file.kind === "image") {
      $("out-editor").hidden = true;
      $("out-image").hidden = false;
      $("out-image").src = outputFileUrl("media", file.key);
      $("out-editor").value = "";
      return;
    }
    $("out-image").hidden = true;
    $("out-image").removeAttribute("src");
    $("out-editor").hidden = false;
    if (!file.exists) {
      $("out-editor").value = "";
      return;
    }
    const data = await api(outputFileUrl("files", file.key));
    $("out-editor").value = data.content;
  }

  async function startRun(payload) {
    setRunBusy(true);
    $("run-error").textContent = "";
    $("run-status").textContent = "Running...";
    try {
      const created = await api("/api/runs", { method: "POST", body: payload });
      log("Run", `Started ${created.id}`);
      listenRun(created.id);
    } catch (err) {
      setRunBusy(false);
      $("run-error").textContent = err.message;
      $("run-status").textContent = "";
      log("Run", err.message);
    }
  }

  async function loadSettings() {
    const data = await api("/api/settings");
    fillSelect($("set-text-model"), data.text_models, { selected: data.default_model });
    fillSelect($("set-image-model"), data.image_models, { selected: data.image_generation_model });
    $("set-text-model").value = data.default_model;
    $("set-image-model").value = data.image_generation_model;
    $("set-key").value = "";
    $("set-key-hint").textContent = data.gemini_api_key_configured
      ? `Configured ${data.gemini_api_key_masked}`
      : "No API key stored";
    $("set-warnings").textContent = (data.warnings || []).join("\n");
    state.typedApiKey = false;
    return data;
  }

  function bind() {
    document.querySelectorAll(".tabs button").forEach((btn) => {
      btn.addEventListener("click", () => showWorkspace(btn.dataset.workspace));
    });
    $("log-toggle").addEventListener("click", () => {
      const logEl = $("event-log");
      logEl.hidden = !logEl.hidden;
      $("log-toggle").textContent = logEl.hidden ? "Show Event Log" : "Hide Event Log";
    });
    $("run-mode").addEventListener("change", syncRunMode);
    $("run-campaign").addEventListener("change", () => refreshRunEpisodes().catch((err) => log("Run", err.message)));
    $("run-add-campaign").addEventListener("click", async () => {
      const name = $("run-new-campaign").value.trim();
      try {
        await api("/api/campaigns", { method: "POST", body: { name } });
        $("run-new-campaign").value = "";
        await loadCampaigns(name);
        await refreshRunEpisodes();
        await loadPromptList();
        await refreshOutputEpisodes();
        log("Run", `Campaign ${name} created`);
      } catch (err) {
        log("Run", err.message);
      }
    });
    $("run-submit").addEventListener("click", () => startRun(runPayload()));
    $("run-cancel").addEventListener("click", async () => {
      if (!state.runId) return;
      try {
        await api(`/api/runs/${state.runId}/cancel`, { method: "POST" });
        log("Run", "Cancel requested");
      } catch (err) {
        log("Run", err.message);
      }
    });
    $("prompt-campaign").addEventListener("change", () => {
      state.promptKey = "";
      loadPromptList().catch((err) => log("Prompts", err.message));
    });
    $("prompt-save").addEventListener("click", async () => {
      const campaign = $("prompt-campaign").value;
      if (!campaign || !state.promptKey) return;
      try {
        const saved = await api(
          `/api/campaigns/${encodeURIComponent(campaign)}/prompts/${encodeKey(state.promptKey)}`,
          { method: "PUT", body: { content: $("prompt-editor").value } }
        );
        state.promptKey = saved.key;
        $("prompt-status").textContent = `Saved ${saved.key}`;
        log("Prompts", `Saved ${saved.key}`);
        await loadPromptList();
        await loadArtStyles($("run-campaign").value, $("run-art-style"));
        await loadArtStyles($("out-campaign").value, $("out-art-style"));
      } catch (err) {
        $("prompt-status").textContent = err.message;
        log("Prompts", err.message);
      }
    });
    $("prompt-reset").addEventListener("click", async () => {
      const campaign = $("prompt-campaign").value;
      if (!campaign || !state.promptKey) return;
      const data = await api(
        `/api/campaigns/${encodeURIComponent(campaign)}/prompts/${encodeKey(state.promptKey)}/reset`,
        { method: "POST" }
      );
      $("prompt-editor").value = data.content;
      $("prompt-status").textContent = "Loaded default (not saved)";
    });
    $("out-campaign").addEventListener("change", () => refreshOutputEpisodes().catch((err) => log("Output", err.message)));
    $("out-episode").addEventListener("change", () => refreshOutputVersions().catch((err) => log("Output", err.message)));
    $("out-version").addEventListener("change", () => refreshOutputFiles().catch((err) => log("Output", err.message)));
    $("out-stage").addEventListener("change", applyOutputGating);
    $("out-save").addEventListener("click", async () => {
      if (!state.outFile) return;
      try {
        await api(outputFileUrl("files", state.outFile.key), {
          method: "PUT",
          body: { content: $("out-editor").value },
        });
        $("out-status").textContent = `Saved ${state.outFile.key} → working/`;
        log("Output", `Saved ${state.outFile.key}`);
      } catch (err) {
        $("out-status").textContent = err.message;
      }
    });
    $("out-reload").addEventListener("click", () => {
      if (state.outFile) loadOutputFile(state.outFile);
    });
    $("out-star").addEventListener("click", async () => {
      const { campaign, episode, version } = selectedOutput();
      const starred = $("out-star").textContent.includes("Starred") ? false : true;
      await api(
        `/api/campaigns/${encodeURIComponent(campaign)}/episodes/${encodeURIComponent(episode)}/versions/${encodeURIComponent(version)}`,
        { method: "PATCH", body: { starred } }
      );
      await refreshOutputVersions();
    });
    $("out-note").addEventListener("change", async () => {
      const { campaign, episode, version } = selectedOutput();
      await api(
        `/api/campaigns/${encodeURIComponent(campaign)}/episodes/${encodeURIComponent(episode)}/versions/${encodeURIComponent(version)}`,
        { method: "PATCH", body: { description: $("out-note").value } }
      );
    });
    $("out-rerun").addEventListener("click", () => {
      const episode = state.outEpisodes.find((item) => item.slug === $("out-episode").value);
      const stage = $("out-stage").value;
      startRun({
        url: (episode && episode.url) || "",
        campaign: $("out-campaign").value,
        rerun_from: stage,
        stop_after: $("out-only-stage").checked ? stage : null,
        recap_version: $("out-recap").value,
        panel_count: Number($("out-panels").value),
        total_pages: Number($("out-pages").value),
        generation_mode: $("out-gen-mode").value,
        aspect_ratio: $("out-aspect").value,
        art_style: $("out-art-style").value || null,
        vignette: $("out-vignette").checked,
        cache_buster: $("out-cache-buster").checked,
        unstyled_prompts: $("out-unstyled").checked,
        chat_mode: $("out-chat").checked,
        pg13_mode: $("out-pg13").checked,
      });
    });
    async function imageJob(path, body) {
      const { campaign, episode, version } = selectedOutput();
      $("out-status").textContent = "Working...";
      setRunBusy(true);
      try {
        const options = { method: "POST" };
        if (body !== undefined) options.body = body;
        const result = await api(
          `/api/campaigns/${encodeURIComponent(campaign)}/episodes/${encodeURIComponent(episode)}/versions/${encodeURIComponent(version)}/images/${path}`,
          options
        );
        $("out-status").textContent = `${result.source}: ${(result.files || result.stitched || []).join(", ")}`;
        log("Images", result.source);
        await refreshOutputFiles();
      } catch (err) {
        $("out-status").textContent = err.message;
        log("Images", err.message);
      } finally {
        setRunBusy(false);
      }
    }
    $("out-generate").addEventListener("click", () => imageJob("generate"));
    $("out-test").addEventListener("click", () =>
      imageJob("test", state.outFile ? { prompt: state.outFile.key } : {})
    );
    $("out-stitch").addEventListener("click", () => imageJob("stitch"));
    $("out-generate-selected").addEventListener("click", () =>
      imageJob("generate-selected", { prompt: state.outFile && state.outFile.key })
    );
    $("open-settings").addEventListener("click", async () => {
      await loadSettings();
      $("settings-dialog").showModal();
    });
    $("set-key").addEventListener("input", () => {
      state.typedApiKey = true;
    });
    $("set-refresh").addEventListener("click", async () => {
      try {
        const data = await api("/api/settings/refresh-models", { method: "POST" });
        fillSelect($("set-text-model"), data.text_models, { selected: data.default_model });
        fillSelect($("set-image-model"), data.image_models, { selected: data.image_generation_model });
        $("set-status").textContent = data.fetch_error || "Models refreshed";
        $("set-warnings").textContent = (data.warnings || []).join("\n");
      } catch (err) {
        $("set-status").textContent = err.message;
      }
    });
    $("settings-form").addEventListener("submit", async (event) => {
      if (event.submitter && event.submitter.id === "set-save") {
        event.preventDefault();
        const body = {
          default_model: $("set-text-model").value,
          image_generation_model: $("set-image-model").value,
        };
        if (state.typedApiKey && $("set-key").value.trim()) {
          body.gemini_api_key = $("set-key").value.trim();
        }
        try {
          const saved = await api("/api/settings", { method: "PUT", body });
          $("set-status").textContent = "Settings saved";
          $("set-warnings").textContent = (saved.warnings || []).join("\n");
          log("Settings", "Saved settings");
          await loadSettings();
        } catch (err) {
          $("set-status").textContent = err.message;
        }
      }
    });
  }

  async function boot() {
    bind();
    syncRunMode();
    applyOutputGating();
    try {
      const health = await api("/api/health");
      const settings = await api("/api/settings");
      setBanner([...(health.warnings || []), ...(settings.warnings || [])]);
      await loadCampaigns();
      await refreshRunEpisodes();
      await loadPromptList();
      await refreshOutputEpisodes();
      log("App", "Ready");
    } catch (err) {
      setBanner([err.message]);
      log("App", err.message);
    }
  }

  boot();
})();
