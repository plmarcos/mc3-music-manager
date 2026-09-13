// Front-end controller. Two channels to Python:
//   pull  -> pywebview.api.<method>()  returns a Promise (JS calls Python)
//   push  -> window.__mc3.on(event, fn) (Python calls JS via bridge.emit)

// ---- push channel: event bus the Python Bridge talks to -------------------
window.__mc3 = {
  _handlers: {},
  on(event, fn) {
    (this._handlers[event] ||= []).push(fn);
  },
  _dispatch(event, payload) {
    (this._handlers[event] || []).forEach((fn) => {
      try { fn(payload); } catch (e) { console.error(e); }
    });
  },
};

// ---- DOM helpers ----------------------------------------------------------
const $ = (id) => document.getElementById(id);

// escapeHtml — shared helper (playlist rendering, ISO validation result, etc.)
function escapeHtml(text) {
  const el = document.createElement("div");
  el.textContent = text == null ? "" : String(text);
  return el.innerHTML;
}

// ---- i18n (interface language) --------------------------------------------
// PT-BR is the base language; the JSON tables (served by Python, since file://
// blocks fetch) map stable keys -> translated text. t(key) falls back to the key
// so any element without a translation simply keeps its original text.
let I18N = { language: "pt-BR", tables: {}, dict: {} };

function t(key) {
  return (I18N.dict && I18N.dict[key]) || key;
}

function setLanguageTables(cfg) {
  I18N.tables = (cfg && cfg.translations) || {};
  I18N.language = (cfg && cfg.language) || "pt-BR";
  I18N.dict = I18N.tables[I18N.language] || {};
}

function applyStaticTranslations() {
  document.querySelectorAll("[data-i18n]").forEach((el) => {
    const k = el.getAttribute("data-i18n");
    if (k) el.textContent = t(k);
  });
  document.querySelectorAll("[data-i18n-placeholder]").forEach((el) => {
    const k = el.getAttribute("data-i18n-placeholder");
    if (k) el.setAttribute("placeholder", t(k));
  });
}

async function loadI18n() {
  try {
    setLanguageTables(await window.pywebview.api.get_i18n());
    applyStaticTranslations();
    const sel = $("settings-language");
    if (sel) sel.value = I18N.language;
  } catch (e) {
    console.error(e);  // i18n failure must never blank the app — keep PT fallback
  }
}

// ---- Recompilar DATs + Backup ---------------------------------------------
let rbReady = { streams: false, assets: false, all: false };

function makeConsoleLogger(boxId) {
  return (line, kind) => {
    const el = document.createElement("div");
    el.className = "console-line" + (kind ? " " + kind : "");
    el.textContent = line;
    const box = $(boxId);
    box.appendChild(el);
    box.scrollTop = box.scrollHeight;
  };
}
const rbLog = makeConsoleLogger("rb-console");
const bkLog = makeConsoleLogger("bk-console");

function setRbProgress(pct) {
  $("rb-progress-fill").style.width = pct + "%";
  $("rb-progress-label").textContent = Math.round(pct) + "%";
}

function setRbButtons(enabled) {
  $("btn-rb-streams").disabled = !enabled || !rbReady.streams;
  $("btn-rb-assets").disabled = !enabled || !rbReady.assets;
  $("btn-rb-all").disabled = !enabled || !rbReady.all;
}

function setRbBusy(busy) {
  $("rb-progress").classList.toggle("busy", busy);
  $("btn-rb-cancel").hidden = !busy;
  setRbButtons(!busy);
  if (busy) {
    const tag = $("rb-status-tag");
    tag.className = "tag busy";
    tag.textContent = "recompilando…";
  }
}

function setBkBusy(busy) {
  $("btn-bk-create").disabled = busy;
  $("bk-confirm").disabled = busy;
  $("btn-bk-restore").disabled = busy || !$("bk-confirm").checked;
  if (busy) {
    const tag = $("bk-status-tag");
    tag.className = "tag busy";
    tag.textContent = "trabalhando…";
  }
}

async function loadRebuildStatus() {
  try {
    const s = await window.pywebview.api.get_rebuild_status();
    const tools = s.tools || {};
    rbReady.streams = !!(tools.hash_build && s.has_streams);
    rbReady.assets = !!(tools.dave && s.has_assets);
    rbReady.all = !!s.rebuild_ready;
    setRbButtons(true);

    const el = $("rb-tools-status");
    if (s.rebuild_ready) {
      el.className = "tools ok";
      el.textContent = "✔ ferramentas e workspace prontos para recompilar";
    } else {
      el.className = "tools bad";
      const missing = [];
      if (!tools.hash_build) missing.push("hash_build");
      if (!tools.dave) missing.push("dave");
      if (!s.has_streams) missing.push("pasta STREAMS");
      if (!s.has_assets) missing.push("pasta ASSETS");
      el.textContent = "✖ faltando: " + missing.join(", ");
    }

    const bk = $("bk-latest");
    if (s.latest_backup) {
      bk.className = "tools ok";
      bk.textContent = "último backup: " + s.latest_backup;
    } else {
      bk.className = "tools";
      bk.textContent = "nenhum backup ainda";
    }
  } catch (e) {
    console.error(e);
    showLoadError("rb-tools-status", "✖ falha ao consultar o backend");
  }
}

window.__mc3.on("rb_status", (p) => {
  if (typeof p.progress === "number") setRbProgress(p.progress);
});
window.__mc3.on("rb_log", (p) => rbLog(p.line));
window.__mc3.on("rb_busy", (p) => setRbBusy(!!p.busy));
window.__mc3.on("rb_done", (p) => {
  const tag = $("rb-status-tag");
  tag.className = "tag " + (p.ok ? "done" : "");
  tag.textContent = p.ok ? "concluído" : (p.cancelled ? "cancelado" : "falhou");
  if (p.ok) rbLog("✔ recompilação concluída", "ok");
  else { rbLog("✖ falhou: " + (p.error || ""), "err"); if (!p.cancelled) noteError("Recompilar DATs", p.error, "rb-console"); }
  loadRebuildStatus();
});

window.__mc3.on("bk_log", (p) => bkLog(p.line));
window.__mc3.on("bk_busy", (p) => setBkBusy(!!p.busy));
window.__mc3.on("bk_done", (p) => {
  const tag = $("bk-status-tag");
  tag.className = "tag " + (p.ok ? "done" : "");
  tag.textContent = p.ok ? "concluído" : (p.cancelled ? "cancelado" : "falhou");
  if (p.ok) {
    const r = p.result || {};
    if (p.kind === "restore") {
      bkLog("✔ restaurado: " + (r.restored || 0) + " arquivo(s); removido(s): " + (r.removed || 0), "ok");
    } else {
      bkLog("✔ backup criado (" + (r.count || 0) + " arquivo(s))", "ok");
    }
  } else {
    bkLog("✖ falhou: " + (p.error || ""), "err");
    if (!p.cancelled) noteError("Backup", p.error, "bk-console");
  }
  // reset the destructive confirm gate after any restore/backup
  $("bk-confirm").checked = false;
  $("btn-bk-restore").disabled = true;
  loadRebuildStatus();
});

// ---- Adicionar música -----------------------------------------------------
let addHasSource = false;
let addToolsReady = false; // ffmpeg + rstm_build + strtbl present
let addPlaylists = []; // [{rel, name, city}]
let addGenreDefaults = {};
let addCityPlaylists = []; // e.g. ["atlanta.play", "detroit.play", ...]
let addPreviewTimer = null;
let addMode = "single";
let batchRowCount = 0;
const addLog = makeConsoleLogger("add-console");

function setAddProgress(pct) {
  $("add-progress-fill").style.width = pct + "%";
  $("add-progress-label").textContent = Math.round(pct) + "%";
}

function selectedPlaylistRels() {
  return Array.from(document.querySelectorAll("#add-playlists input[type=checkbox]:checked"))
    .map((el) => el.getAttribute("data-rel"));
}

function updateAddButton(busy) {
  const confirmOk = $("add-confirm").checked;
  const playlistsOk = selectedPlaylistRels().length > 0;
  // Gate on the tools too — without ffmpeg/rstm_build/strtbl the add would only
  // fail midway through the conversion.
  const singleReady = addToolsReady && addHasSource && confirmOk && playlistsOk;
  const batchReady = addToolsReady && batchRowCount > 0 && confirmOk && playlistsOk;
  $("btn-add").disabled = !!busy || addMode !== "single" || !singleReady;
  $("btn-add-batch").disabled = !!busy || addMode !== "batch" || !batchReady;
}

function setAddMode(mode) {
  addMode = mode;
  $("mode-single").classList.toggle("active", mode === "single");
  $("mode-batch").classList.toggle("active", mode === "batch");
  $("add-single").hidden = mode !== "single";
  $("add-batch").hidden = mode !== "batch";
  $("btn-add").hidden = mode !== "single";
  $("btn-add-batch").hidden = mode !== "batch";
  updateAddButton();
}

function collectBatchRows() {
  return Array.from(document.querySelectorAll("#batch-rows .batch-row")).map((row) => ({
    index: parseInt(row.getAttribute("data-index"), 10),
    title: row.querySelector(".batch-title").value,
    artist: row.querySelector(".batch-artist").value,
    genre: row.querySelector(".batch-genre").value,
    asset_name: "",
  }));
}

function updatePlCount() {
  const checked = Array.from(document.querySelectorAll("#add-playlists input[type=checkbox]:checked"));
  $("pl-count").textContent = checked.length + " selecionada(s)";
  $("pl-selected-count").textContent = checked.length;
  const box = $("pl-selected");
  if (!checked.length) {
    box.innerHTML = '<span class="muted" style="font-size:12px">nenhuma playlist selecionada</span>';
  } else {
    box.innerHTML = checked.map((el) =>
      '<span class="pl-chip"><b>' + escapeHtml(el.getAttribute("data-city") || "?") + "</b> · " +
      escapeHtml(el.getAttribute("data-name") || "") + "</span>").join("");
  }
  updateAddButton();
}

// Exclusive preset: set each playlist checkbox to exactly predicate(el).
function setPlaylistPreset(predicate) {
  document.querySelectorAll("#add-playlists input[type=checkbox]").forEach((el) => {
    el.checked = predicate(el);
  });
  updatePlCount();
}

function setAddBusy(busy) {
  $("add-progress").classList.toggle("busy", busy);
  $("btn-add-cancel").hidden = !busy;
  $("btn-pick-add").disabled = busy;
  $("btn-add-listen").disabled = busy || !addHasSource;
  $("btn-pick-batch").disabled = busy;
  $("mode-single").disabled = busy;
  $("mode-batch").disabled = busy;
  updateAddButton(busy);
  if (busy) {
    const tag = $("add-status-tag");
    tag.className = "tag busy";
    tag.textContent = "adicionando…";
  }
}

function renderPlaylists() {
  const box = $("add-playlists");
  if (!addPlaylists.length) {
    box.innerHTML = '<div class="console-line muted">— nenhuma playlist (workspace sem game files) —</div>';
    updatePlCount();
    return;
  }
  const byCity = {};
  addPlaylists.forEach((p) => { (byCity[p.city] ||= []).push(p); });
  let html = "";
  Object.keys(byCity).sort().forEach((city) => {
    html += '<div class="pl-city">' + escapeHtml(city) + "</div>";
    byCity[city].forEach((p) => {
      html += '<label class="pl-item"><input type="checkbox" data-rel="' +
        escapeHtml(p.rel) + '" data-name="' + escapeHtml(p.name) + '" data-city="' + escapeHtml(p.city) +
        '" /> ' + escapeHtml(p.name) + "</label>";
    });
  });
  box.innerHTML = html;
  box.querySelectorAll("input[type=checkbox]").forEach((el) => el.addEventListener("change", updatePlCount));
  updatePlCount();
}

async function loadAddStatus() {
  try {
    const s = await window.pywebview.api.get_add_status();
    addGenreDefaults = s.genre_defaults || {};
    addCityPlaylists = s.city_playlists || [];
    // Fill the single-mode genre AND the batch "apply to all" genre from one list.
    [$("add-genre"), $("batch-bulk-genre")].forEach((sel) => {
      if (!sel) return;
      sel.innerHTML = "";
      (s.genres || []).forEach((g) => {
        const opt = document.createElement("option");
        opt.value = g;
        opt.textContent = g;
        sel.appendChild(opt);
      });
    });
    // Also fill the Remover genre filter at boot, so all 6 genres show up even
    // before "Atualizar lista" is clicked (matches the original).
    fillRemoveGenreFilter(s.genres || []);
    addPlaylists = s.playlists || [];
    renderPlaylists();
    addToolsReady = !!s.tools_ready;
    const el = $("add-tools-status");
    if (s.tools_ready) {
      el.className = "tools ok";
      el.textContent = "✔ ferramentas de conversão prontas" + (s.has_playlists ? "" : " · workspace sem playlists ainda");
    } else {
      el.className = "tools bad";
      const missing = [];
      if (!s.has_ffmpeg) missing.push("FFmpeg (converte o áudio)");
      if (!s.has_rstm_build) missing.push("rstm_build");
      if (!s.has_strtbl) missing.push("strtbl");
      el.textContent = "✖ faltando: " + missing.join(", ") + " — adicionar está bloqueado";
    }
    updateAddButton();
  } catch (e) {
    console.error(e);
    showLoadError("add-tools-status", "✖ falha ao consultar o backend");
  }
}

function schedulePreview() {
  if (addPreviewTimer) clearTimeout(addPreviewTimer);
  addPreviewTimer = setTimeout(doPreview, 250);
}

async function doPreview() {
  if (!addHasSource) return;
  try {
    const r = await window.pywebview.api.preview_add(
      $("add-title").value, $("add-artist").value, $("add-genre").value, $("add-asset").value);
    if (r.ok) {
      $("prev-key").textContent = r.string_key;
      $("prev-entry").textContent = r.playlist_entry;
      $("prev-target").textContent = r.stream_target + (r.exists ? "   ⚠ já existe" : "");
      $("add-asset").placeholder = r.asset_name || "(automático)";
    } else {
      $("prev-key").textContent = "--";
      $("prev-entry").textContent = r.error || "--";
      $("prev-target").textContent = "--";
    }
  } catch (e) {
    console.error(e);
  }
}

window.__mc3.on("add_selected", (p) => {
  addHasSource = true;
  $("add-path").textContent = p.name || p.path;
  $("btn-add-listen").disabled = false;
  updateAddButton();
});
window.__mc3.on("batch_loaded", (p) => {
  const rows = (p && p.rows) || [];
  batchRowCount = rows.length;
  $("batch-count").textContent = rows.length ? rows.length + " arquivo(s)" : "nenhum arquivo selecionado";
  const box = $("batch-rows");
  $("batch-bulk").hidden = !rows.length;  // "aplicar a todas" só faz sentido com faixas
  $("batch-bulk-info").textContent = "";
  if (!rows.length) {
    box.innerHTML = '<div class="console-line muted">— nenhum arquivo —</div>';
    updateAddButton();
    return;
  }
  const genres = Array.from($("add-genre").options).map((o) => o.value);
  box.innerHTML = rows.map((r) => {
    const opts = genres
      .map((g) => '<option value="' + escapeHtml(g) + '"' + (g === r.genre ? " selected" : "") + ">" + escapeHtml(g) + "</option>")
      .join("");
    return '<div class="batch-row" data-index="' + r.index + '">' +
      '<span class="batch-name" title="' + escapeHtml(r.name) + '">' + escapeHtml(r.name) + "</span>" +
      '<input class="batch-title" placeholder="Título" value="' + escapeHtml(r.title || "") + '" />' +
      '<input class="batch-artist" placeholder="Artista" value="' + escapeHtml(r.artist || "") + '" />' +
      '<select class="batch-genre">' + opts + "</select>" +
      "</div>";
  }).join("");
  updateAddButton();
});
window.__mc3.on("add_guess", (g) => {
  if (g.title && !$("add-title").value) $("add-title").value = g.title;
  if (g.artist && !$("add-artist").value) $("add-artist").value = g.artist;
  $("add-detected").textContent = g.detected_by
    ? "detectado por: " + g.detected_by + (g.asset_name ? " · nome sugerido: " + g.asset_name : "")
    : "";
  doPreview();
});
window.__mc3.on("add_status", (p) => { if (typeof p.progress === "number") setAddProgress(p.progress); });
window.__mc3.on("add_log", (p) => addLog(p.line));
window.__mc3.on("add_busy", (p) => setAddBusy(!!p.busy));
window.__mc3.on("add_done", (p) => {
  const tag = $("add-status-tag");
  tag.className = "tag " + (p.ok ? "done" : "");
  tag.textContent = p.ok ? "concluído" : (p.cancelled ? "cancelado" : "falhou");
  if (p.ok) {
    const r = p.result || {};
    addLog("✔ " + (r.added || 1) + " música(s) adicionada(s) — playlists alteradas: " +
      (r.playlist_changes || 0) + " · backup: " + (r.backup || "?"), "ok");
    (r.skipped || []).forEach((s) => addLog("• pulado: " + s, "err"));
    $("add-confirm").checked = false;
    updateAddButton();
  } else {
    addLog("✖ falhou: " + (p.error || ""), "err");
    logBackupHint(addLog, p);
    if (!p.cancelled) noteError("Adicionar música", p.error, "add-console");
  }
  loadOverview();
  loadRemoveStatus(); // added songs should show up in the Remover list
});

// ---- Preparar Projeto (extrair ISO -> workspace) --------------------------
const ppLog = makeConsoleLogger("pp-console");
let ppStatus = {};
let ppHasIso = false;

function setPpProgress(pct) {
  $("pp-progress-fill").style.width = pct + "%";
  $("pp-progress-label").textContent = Math.round(pct) + "%";
}

function updatePpButtons(busy) {
  const tools = !!ppStatus.tools_ready;
  const needConfirm = !!(ppStatus.prepared || ppStatus.has_game_files);
  const confirmOk = !needConfirm || $("pp-confirm").checked;
  $("pp-danger").hidden = !needConfirm;
  $("btn-pick-project-iso").disabled = !!busy;
  $("btn-validate-iso").disabled = !!busy || !ppHasIso;
  $("btn-pp-copy").disabled = !!busy || !tools || !ppHasIso || !confirmOk;
  $("btn-pp-prepare").disabled = !!busy || !tools || !ppStatus.has_game_files;
  $("btn-pp-all").disabled = !!busy || !tools || !ppHasIso || !confirmOk;
  // Reset needs something to wipe + its own confirm; independent of the ISO pick.
  $("btn-pp-reset").disabled = !!busy || !needConfirm || !$("pp-reset-confirm").checked;
}

function setPpBusy(busy) {
  $("pp-progress").classList.toggle("busy", busy);
  $("btn-pp-cancel").hidden = !busy;
  updatePpButtons(busy);
  if (busy) {
    const tag = $("pp-status-tag");
    tag.className = "tag busy";
    tag.textContent = "preparando…";
  }
}

function toggleOk(id, ok) {
  $(id).classList.toggle("ok", !!ok);
}

async function loadPrepareStatus() {
  try {
    const s = await window.pywebview.api.get_prepare_status();
    ppStatus = s;
    if (s.iso) {
      ppHasIso = true;
      $("pp-iso").textContent = s.iso;
    } else {
      // No ISO selected (fresh boot, or right after a reset) — reflect it.
      ppHasIso = false;
      $("pp-iso").textContent = "nenhuma ISO escolhida";
    }
    toggleOk("pp-c-gamefiles", s.has_game_files);
    toggleOk("pp-c-assets", s.has_assets);
    toggleOk("pp-c-streams", s.has_streams);
    toggleOk("pp-c-strings", s.has_strings_json);
    const el = $("pp-tools-status");
    if (!s.tools_ready) {
      el.className = "tools bad";
      el.textContent = "✖ faltam ferramentas (dave/hash_build/strtbl/.lst em tools/)";
    } else if (s.prepared) {
      el.className = "tools ok";
      el.textContent = "✔ workspace pronto para editar";
    } else {
      el.className = "tools";
      el.textContent = "ferramentas OK · workspace ainda não preparado";
    }
    updatePpButtons(false);
  } catch (e) {
    console.error(e);
    showLoadError("pp-tools-status", "✖ falha ao consultar o backend");
  }
}

window.__mc3.on("pp_iso_selected", (p) => {
  ppHasIso = true;
  $("pp-iso").textContent = p.name || p.path;
  $("pp-validation").textContent = "";
  $("pp-validation").className = "tools";
  updatePpButtons();
});
window.__mc3.on("pp_validation", (p) => {
  const el = $("pp-validation");
  if (p && p.error) {
    el.className = "tools bad";
    el.textContent = "✖ validação falhou: " + p.error;
  } else if (p && p.supported) {
    el.className = "tools ok";
    el.textContent = "✔ ISO suportada — " + (p.game_name || "MC3") + " · BOOT2: " + (p.boot_id || "?") +
      " · ASSETS.DAT " + (p.has_assets ? "✓" : "✗") + " · STREAMS.DAT " + (p.has_streams ? "✓" : "✗");
  } else {
    el.className = "tools bad";
    el.textContent = "⚠️ ISO não suportada" + (p && p.boot_id ? " (BOOT2: " + p.boot_id + ")" : "");
  }
});
window.__mc3.on("pp_status", (p) => { if (typeof p.progress === "number") setPpProgress(p.progress); });
window.__mc3.on("pp_log", (p) => ppLog(p.line));
window.__mc3.on("pp_busy", (p) => setPpBusy(!!p.busy));
window.__mc3.on("pp_done", (p) => {
  const tag = $("pp-status-tag");
  tag.className = "tag " + (p.ok ? "done" : "");
  tag.textContent = p.ok ? "concluído" : (p.cancelled ? "cancelado" : "falhou");
  if (p.ok) ppLog("✔ " + (p.kind || "preparação") + " concluído", "ok");
  else { ppLog("✖ falhou: " + (p.error || ""), "err"); if (!p.cancelled) noteError("Preparar Projeto", p.error, "pp-console"); }
  $("pp-confirm").checked = false;
  $("pp-reset-confirm").checked = false;
  loadPrepareStatus();
  // the workspace changed — refresh the edit screens so they pick up the data
  loadRebuildStatus();
  loadAddStatus();
  loadRemoveStatus();
  loadIsoStatus();
  loadOverview();
});

// ---- Remover música -------------------------------------------------------
let rmSongs = [];
const rmLog = makeConsoleLogger("rm-console");

function setRmProgress(pct) {
  $("rm-progress-fill").style.width = pct + "%";
  $("rm-progress-label").textContent = Math.round(pct) + "%";
}

function selectedSongs() {
  return Array.from(document.querySelectorAll("#rm-songs input[type=checkbox]:checked"))
    .map((el) => ({ genre: el.getAttribute("data-genre"), asset_name: el.getAttribute("data-asset") }));
}

function updateRmButton(busy) {
  const anyAction = $("rm-opt-audio").checked || $("rm-opt-playlists").checked || $("rm-opt-strings").checked;
  $("btn-rm-remove").disabled = !!busy || selectedSongs().length === 0 || !$("rm-confirm").checked || !anyAction;
}

function updateRmCount() {
  $("rm-count").textContent = selectedSongs().length + " selecionada(s)";
  updateRmButton();
}

function setRmBusy(busy) {
  $("rm-progress").classList.toggle("busy", busy);
  $("btn-rm-cancel").hidden = !busy;
  $("btn-rm-refresh").disabled = busy;
  updateRmButton(busy);
  if (busy) {
    const tag = $("rm-status-tag");
    tag.className = "tag busy";
    tag.textContent = "removendo…";
  }
}

function applyFilters() {
  const q = ($("rm-search").value || "").trim().toLowerCase();
  const g = $("rm-genre-filter").value;
  document.querySelectorAll("#rm-songs .song-item").forEach((row) => {
    const text = row.getAttribute("data-text") || "";
    const genre = row.getAttribute("data-genre") || "";
    const show = (!q || text.indexOf(q) !== -1) && (g === "__all__" || genre === g);
    row.classList.toggle("hidden", !show);
  });
}

function fillRemoveGenreFilter(genres) {
  const gsel = $("rm-genre-filter");
  if (!gsel) return;
  const list = genres && genres.length ? genres : [];
  const prev = gsel.value;
  gsel.innerHTML = '<option value="__all__">Todos os gêneros</option>' +
    list.map((g) => '<option value="' + escapeHtml(g) + '">' + escapeHtml(g) + "</option>").join("");
  gsel.value = prev === "__all__" || list.indexOf(prev) !== -1 ? prev : "__all__";
}

function renderSongs(songs, playlistCount, genres) {
  rmSongs = songs || [];
  $("rm-stats").textContent = rmSongs.length + " música(s)" +
    (playlistCount != null ? " · " + playlistCount + " playlists" : "");

  // Full genre list (like the original) — falls back to the genres present.
  const genreOptions = genres && genres.length ? genres : Array.from(new Set(rmSongs.map((s) => s.genre))).sort();
  fillRemoveGenreFilter(genreOptions);

  const box = $("rm-songs");
  if (!rmSongs.length) {
    box.innerHTML = '<div class="console-line muted">— nenhuma música (workspace vazio ou não preparado) —</div>';
    updateRmCount();
    return;
  }
  const head = '<div class="song-head"><span></span><span>Gênero</span><span>Nome interno</span><span>Pl</span><span>Str</span></div>';
  box.innerHTML = head + rmSongs.map((s) => {
    const text = (s.genre + " " + s.asset_name).toLowerCase();
    return '<label class="song-item" data-text="' + escapeHtml(text) + '" data-genre="' + escapeHtml(s.genre) + '">' +
      '<input type="checkbox" data-genre="' + escapeHtml(s.genre) + '" data-asset="' + escapeHtml(s.asset_name) + '" />' +
      '<span class="song-genre">' + escapeHtml(s.genre) + "</span>" +
      '<span class="song-name">' + escapeHtml(s.asset_name) + "</span>" +
      '<span class="song-pl">' + (s.playlist_count || 0) + "</span>" +
      '<span class="song-str' + (s.has_strings ? "" : " warn") + '">' + (s.has_strings ? "✓" : "✗") + "</span>" +
      "</label>";
  }).join("");
  box.querySelectorAll("input[type=checkbox]").forEach((el) => el.addEventListener("change", updateRmCount));
  applyFilters();
  updateRmCount();
}

async function loadRemoveStatus() {
  try {
    const s = await window.pywebview.api.get_prepare_status();
    const el = $("rm-tools-status");
    if (s.prepared) {
      el.className = "tools ok";
      el.textContent = "✔ workspace pronto";
      refreshSongs(); // auto-load the song list (like the original)
    } else {
      el.className = "tools bad";
      el.textContent = "✖ prepare o projeto primeiro (aba Preparar Projeto)";
    }
  } catch (e) {
    console.error(e);
    showLoadError("rm-tools-status", "✖ falha ao consultar o backend");
  }
}

async function refreshSongs() {
  try {
    const r = await window.pywebview.api.list_songs();
    renderSongs(r.songs || [], r.playlist_count, r.genres);
    rmLog((r.songs || []).length + " música(s) na lista.");
  } catch (e) {
    console.error(e);
  }
}

window.__mc3.on("rm_status", (p) => { if (typeof p.progress === "number") setRmProgress(p.progress); });
window.__mc3.on("rm_log", (p) => rmLog(p.line));
window.__mc3.on("rm_busy", (p) => setRmBusy(!!p.busy));
window.__mc3.on("rm_done", (p) => {
  const tag = $("rm-status-tag");
  tag.className = "tag " + (p.ok ? "done" : "");
  tag.textContent = p.ok ? "concluído" : (p.cancelled ? "cancelado" : "falhou");
  if (p.ok) {
    const r = p.result || {};
    rmLog("✔ " + (r.removed || 0) + " removida(s) — áudio: " + (r.removed_audio || 0) +
      " · playlists: " + (r.playlist_changes || 0) + " · strings: " + (r.removed_strings || 0) +
      (r.rebuilt ? " · DATs recompilados" : "") + " · backup: " + (r.backup || "?"), "ok");
    $("rm-confirm").checked = false;
  } else {
    rmLog("✖ falhou: " + (p.error || ""), "err");
    logBackupHint(rmLog, p);
    if (!p.cancelled) noteError("Remover música", p.error, "rm-console");
  }
  refreshSongs(); // reflect the new on-disk state
  loadOverview();
});

// ---- Gerar ISO final ------------------------------------------------------
const giLog = makeConsoleLogger("gi-console");
let giReady = false;

function setGiProgress(pct) {
  $("gi-progress-fill").style.width = pct + "%";
  $("gi-progress-label").textContent = Math.round(pct) + "%";
}

function updateGiButton(busy) {
  $("btn-gi-generate").disabled = !!busy || !giReady;
}

function setGiBusy(busy) {
  $("gi-progress").classList.toggle("busy", busy);
  $("btn-gi-cancel").hidden = !busy;
  $("btn-gi-output").disabled = busy;
  updateGiButton(busy);
  if (busy) {
    const tag = $("gi-status-tag");
    tag.className = "tag busy";
    tag.textContent = "gerando…";
  }
}

async function loadIsoStatus() {
  try {
    const s = await window.pywebview.api.get_iso_status();
    giReady = !!s.ready;
    if (s.output) $("gi-output").textContent = s.output;
    const el = $("gi-tools-status");
    if (s.ready) {
      el.className = "tools ok";
      el.textContent = "✔ ImgBurn e Arquivos da ISO prontos";
    } else {
      el.className = "tools bad";
      const missing = [];
      if (!s.imgburn) missing.push("ImgBurn (instale-o no Início)");
      if (!s.has_rebuild_tools) missing.push("ferramentas PS2 (dave/hash_build) — a ISO recompila os DATs antes");
      if (!s.has_game_files) missing.push("Arquivos da ISO (prepare o projeto)");
      else if (!s.has_system_cnf) missing.push("SYSTEM.CNF");
      el.textContent = "✖ faltando: " + missing.join(", ");
    }
    updateGiButton(false);
  } catch (e) {
    console.error(e);
    showLoadError("gi-tools-status", "✖ falha ao consultar o backend");
  }
}

window.__mc3.on("gi_output", (p) => { $("gi-output").textContent = p.path || "—"; });
window.__mc3.on("gi_status", (p) => { if (typeof p.progress === "number") setGiProgress(p.progress); });
window.__mc3.on("gi_log", (p) => giLog(p.line));
window.__mc3.on("gi_busy", (p) => setGiBusy(!!p.busy));
window.__mc3.on("gi_done", (p) => {
  const tag = $("gi-status-tag");
  tag.className = "tag " + (p.ok ? "done" : "");
  tag.textContent = p.ok ? "concluído" : (p.cancelled ? "cancelado" : "falhou");
  if (p.ok) {
    const r = p.result || {};
    const mb = r.bytes ? (r.bytes / 1048576).toFixed(1) + " MB" : "";
    giLog("✔ ISO gerada → " + (r.output || "?") + (mb ? " (" + mb + ")" : ""), "ok");
  } else {
    giLog("✖ falhou: " + (p.error || ""), "err");
    if (!p.cancelled) noteError("Gerar ISO", p.error, "gi-console");
  }
  loadIsoStatus();
});

// ---- Instalar ferramentas do PC (winget) ----------------------------------
const instLog = makeConsoleLogger("settings-console");

function setInstProgress(pct) {
  $("inst-progress-fill").style.width = pct + "%";
  $("inst-progress-label").textContent = Math.round(pct) + "%";
}
function setInstBusy(busy) {
  $("inst-progress").classList.toggle("busy", busy);
  $("btn-inst-cancel").hidden = !busy;
}

// Render the program list (used by BOTH Início #ov-programs and Configurações
// #settings-programs) with an Install / Open-page action for missing tools.
function renderPrograms(programs, boxId) {
  const box = $(boxId);
  if (!box) return;
  box.innerHTML = (programs || []).map((p) => {
    let cls, txt;
    if (p.found) { cls = "ok"; txt = "✔ instalado"; }
    else if (p.essential) { cls = "bad"; txt = "✖ faltando"; }
    else { cls = "opt"; txt = "opcional (ausente)"; }
    let action = "";
    if (!p.found && p.kind) {
      action = p.installable
        ? '<button class="btn ghost small" data-install="' + escapeHtml(p.kind) + '">⬇ Instalar</button>'
        : '<button class="btn ghost small" data-openpage="' + escapeHtml(p.kind) + '">Abrir página</button>';
    }
    return '<div class="prog-item"><span class="prog-name">' + escapeHtml(p.name) +
      '</span><span class="prog-role">' + escapeHtml(p.role) +
      '</span><span class="prog-status ' + cls + '">' + txt + "</span>" + action + "</div>";
  }).join("");
  box.querySelectorAll("[data-install]").forEach((b) =>
    b.addEventListener("click", () => startInstall(b.getAttribute("data-install"))));
  box.querySelectorAll("[data-openpage]").forEach((b) =>
    b.addEventListener("click", () => window.pywebview.api.open_download_page(b.getAttribute("data-openpage"))));
}

function startInstall(kind) {
  $("settings-console").innerHTML = "";
  setInstProgress(0);
  goToCard("card-settings");  // so the user sees the winget log + progress
  window.pywebview.api.install_tool(kind);
}

window.__mc3.on("inst_status", (p) => { if (typeof p.progress === "number") setInstProgress(p.progress); });
window.__mc3.on("inst_log", (p) => instLog(p.line));
window.__mc3.on("inst_busy", (p) => setInstBusy(!!p.busy));
window.__mc3.on("inst_done", (p) => {
  const r = (p && p.result) || {};
  if (p.ok && r.reason === "no-winget") {
    instLog("winget não encontrado — use “Abrir página” para baixar " + (r.label || "a ferramenta") + " manualmente.", "err");
  } else if (p.ok) {
    instLog("✔ " + (r.label || "ferramenta") + (r.installed ? " instalada." : " — processo concluído."), "ok");
  } else if (p.cancelled) {
    instLog("✖ instalação cancelada", "");
  } else {
    instLog("✖ falhou: " + (p.error || ""), "err");
    if (!p.cancelled) noteError("Instalar ferramentas", p.error, "settings-console");
  }
  // Detections changed — refresh the screens that depend on the tools.
  loadOverview();
  loadIsoStatus();
  loadAddStatus();
  loadRebuildStatus();
});

// ---- Início (painel + passo a passo) --------------------------------------
function goToCard(id) {
  document.querySelectorAll(".rail-item").forEach((i) => i.classList.remove("active"));
  const rail = Array.from(document.querySelectorAll(".rail-item")).find((i) => i.getAttribute("data-target") === id);
  if (rail) rail.classList.add("active");
  const el = $(id);
  if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
}

async function loadOverview() {
  try {
    const s = await window.pywebview.api.get_overview();
    const tag = $("ov-tag");
    tag.className = "tag " + (s.prepared ? "done" : "");
    tag.textContent = s.prepared ? "workspace pronto" : "não preparado";

    $("ov-stats").innerHTML = [
      ["Músicas", s.song_count],
      ["Playlists", s.playlist_count],
      ["Workspace", s.prepared ? "pronto" : "não preparado"],
      ["Backup", s.last_backup ? "sim" : "nenhum"],
    ].map(([l, v]) => '<div class="stat-tile"><div class="label">' + escapeHtml(l) +
      '</div><div class="value">' + escapeHtml(String(v)) + "</div></div>").join("");

    renderPrograms(s.programs, "ov-programs");
    renderPrograms(s.programs, "settings-programs");

    const stateText = { ok: "✔ concluído", pending: "⬜ pendente", available: "→ disponível", blocked: "🔒 prepare primeiro" };
    $("ov-steps").innerHTML = (s.steps || []).map((st) => {
      const btn = (st.target && st.target !== "card-inicio")
        ? '<button class="btn ghost small" data-goto="' + escapeHtml(st.target) + '">Ir para</button>' : "";
      return '<div class="step-item ' + (st.state === "ok" ? "ok" : "") + '">' +
        '<span class="step-num">' + st.n + "</span>" +
        '<span class="step-title">' + escapeHtml(st.title) + "</span>" +
        '<span class="step-state ' + st.state + '">' + (stateText[st.state] || "") + "</span>" +
        btn + "</div>";
    }).join("");
    $("ov-steps").querySelectorAll("[data-goto]").forEach((b) =>
      b.addEventListener("click", () => goToCard(b.getAttribute("data-goto"))));
  } catch (e) {
    console.error(e);
    showLoadError("ov-tag", "✖ falha ao carregar o painel");
  }
}

// ---- pull actions ---------------------------------------------------------
function wireActions() {
  // Recompilar DATs
  $("btn-rb-streams").addEventListener("click", () => {
    $("rb-console").innerHTML = "";
    setRbProgress(0);
    window.pywebview.api.rebuild_streams();
  });
  $("btn-rb-assets").addEventListener("click", () => {
    $("rb-console").innerHTML = "";
    setRbProgress(0);
    window.pywebview.api.rebuild_assets();
  });
  $("btn-rb-all").addEventListener("click", () => {
    $("rb-console").innerHTML = "";
    setRbProgress(0);
    window.pywebview.api.rebuild_all_dats();
  });

  // Backup / restore (restore is gated by the confirm checkbox)
  $("btn-bk-create").addEventListener("click", () => {
    $("bk-console").innerHTML = "";
    window.pywebview.api.create_manual_backup();
  });
  $("bk-confirm").addEventListener("change", (e) => {
    $("btn-bk-restore").disabled = !e.target.checked;
  });
  $("btn-bk-restore").addEventListener("click", () => {
    if (!$("bk-confirm").checked) return;
    $("bk-console").innerHTML = "";
    window.pywebview.api.restore_latest_backup();
  });

  // Preparar Projeto
  $("btn-pick-project-iso").addEventListener("click", () => window.pywebview.api.pick_project_iso());
  $("btn-validate-iso").addEventListener("click", () => {
    if ($("btn-validate-iso").disabled) return;
    $("pp-console").innerHTML = "";
    setPpProgress(0);
    window.pywebview.api.validate_project_iso();
  });
  $("pp-confirm").addEventListener("change", () => updatePpButtons());
  $("btn-pp-copy").addEventListener("click", () => {
    if ($("btn-pp-copy").disabled) return;
    $("pp-console").innerHTML = "";
    setPpProgress(0);
    window.pywebview.api.copy_iso_files();
  });
  $("btn-pp-prepare").addEventListener("click", () => {
    if ($("btn-pp-prepare").disabled) return;
    $("pp-console").innerHTML = "";
    setPpProgress(0);
    window.pywebview.api.prepare_files();
  });
  $("btn-pp-all").addEventListener("click", () => {
    if ($("btn-pp-all").disabled) return;
    $("pp-console").innerHTML = "";
    setPpProgress(0);
    window.pywebview.api.prepare_all();
  });
  $("pp-reset-confirm").addEventListener("change", () => updatePpButtons());
  $("btn-pp-reset").addEventListener("click", () => {
    if ($("btn-pp-reset").disabled) return;
    $("pp-console").innerHTML = "";
    $("pp-validation").textContent = "";
    $("pp-validation").className = "tools";
    setPpProgress(0);
    window.pywebview.api.reset_project();
  });

  // Adicionar música
  $("btn-pick-add").addEventListener("click", () => window.pywebview.api.pick_add_audio());
  $("btn-add-listen").addEventListener("click", () => window.pywebview.api.preview_add_audio());
  ["add-title", "add-artist", "add-asset"].forEach((id) =>
    $(id).addEventListener("input", schedulePreview));
  $("add-genre").addEventListener("change", schedulePreview);
  $("btn-pl-all").addEventListener("click", () => setPlaylistPreset(() => true));
  $("btn-pl-clear").addEventListener("click", () => setPlaylistPreset(() => false));
  $("btn-pl-genre").addEventListener("click", () => {
    const def = addGenreDefaults[$("add-genre").value];
    setPlaylistPreset((el) => {
      const rel = el.getAttribute("data-rel") || "";
      return !!def && (rel === def || rel.endsWith("/" + def));
    });
  });
  $("btn-pl-cities").addEventListener("click", () => {
    setPlaylistPreset((el) => {
      const name = (el.getAttribute("data-rel") || "").split("/").pop();
      return addCityPlaylists.indexOf(name) !== -1;
    });
  });
  $("add-confirm").addEventListener("change", () => updateAddButton());
  $("btn-add").addEventListener("click", () => {
    if ($("btn-add").disabled) return;
    $("add-console").innerHTML = "";
    setAddProgress(0);
    window.pywebview.api.add_selected({
      title: $("add-title").value,
      artist: $("add-artist").value,
      genre: $("add-genre").value,
      asset_name: $("add-asset").value,
      playlists: selectedPlaylistRels(),
      allow_overwrite: $("add-overwrite").checked,
    });
  });

  // Adicionar música — modo lote
  $("mode-single").addEventListener("click", () => setAddMode("single"));
  $("mode-batch").addEventListener("click", () => setAddMode("batch"));
  $("btn-pick-batch").addEventListener("click", () => window.pywebview.api.pick_batch_audio());
  // Aplicar um gênero a TODAS as faixas do lote de uma vez (em vez de dropdown a dropdown).
  $("btn-batch-bulk-apply").addEventListener("click", () => {
    const genre = $("batch-bulk-genre").value;
    const selects = document.querySelectorAll("#batch-rows .batch-genre");
    selects.forEach((sel) => { sel.value = genre; });
    $("batch-bulk-info").textContent = selects.length
      ? `✔ ${selects.length} faixa(s) → ${genre}`
      : "nenhuma faixa para aplicar";
  });
  $("btn-add-batch").addEventListener("click", () => {
    if ($("btn-add-batch").disabled) return;
    $("add-console").innerHTML = "";
    setAddProgress(0);
    window.pywebview.api.add_batch({
      rows: collectBatchRows(),
      playlists: selectedPlaylistRels(),
      allow_overwrite: $("add-overwrite").checked,
    });
  });

  // Remover música
  $("btn-rm-refresh").addEventListener("click", refreshSongs);
  $("rm-search").addEventListener("input", applyFilters);
  $("rm-genre-filter").addEventListener("change", applyFilters);
  $("btn-rm-all").addEventListener("click", () => {
    document.querySelectorAll("#rm-songs .song-item:not(.hidden) input[type=checkbox]").forEach((el) => (el.checked = true));
    updateRmCount();
  });
  $("btn-rm-none").addEventListener("click", () => {
    document.querySelectorAll("#rm-songs input[type=checkbox]").forEach((el) => (el.checked = false));
    updateRmCount();
  });
  ["rm-opt-audio", "rm-opt-playlists", "rm-opt-strings", "rm-confirm"].forEach((id) =>
    $(id).addEventListener("change", () => updateRmButton()));
  $("btn-rm-remove").addEventListener("click", () => {
    if ($("btn-rm-remove").disabled) return;
    $("rm-console").innerHTML = "";
    setRmProgress(0);
    window.pywebview.api.remove_selected({
      songs: selectedSongs(),
      remove_audio: $("rm-opt-audio").checked,
      remove_playlists: $("rm-opt-playlists").checked,
      remove_strings: $("rm-opt-strings").checked,
      rebuild_after: $("rm-opt-rebuild").checked,
    });
  });

  // Gerar ISO final
  $("btn-gi-output").addEventListener("click", () => window.pywebview.api.pick_iso_output());
  $("btn-gi-generate").addEventListener("click", () => {
    if ($("btn-gi-generate").disabled) return;
    $("gi-console").innerHTML = "";
    setGiProgress(0);
    window.pywebview.api.generate_iso({ volume_label: $("gi-label").value });
  });

  // Cancelar: mata o subprocesso da tarefa em andamento (o erro resultante flui
  // pelo on_error, que solta o busy-lock). Recarregar: sai do banner de erro.
  ["pp", "add", "rm", "rb", "gi", "inst"].forEach((k) => {
    const b = $("btn-" + k + "-cancel");
    if (b) b.addEventListener("click", () => window.pywebview.api.cancel_current());
  });
  $("btn-app-reload").addEventListener("click", () => location.reload());

  // Relatório de erro (bar que aparece no erro + botões em Configurações).
  $("btn-report-send").addEventListener("click", sendErrorReport);
  $("btn-report-copy").addEventListener("click", copyReport);
  $("btn-report-dismiss").addEventListener("click", () => { $("error-report-bar").hidden = true; });
  $("btn-report-send2").addEventListener("click", sendErrorReport);
  $("btn-report-copy2").addEventListener("click", copyReport);

  // Barra de título própria (janela sem moldura). Guardado: se a bridge ainda não
  // subiu, o clique não pode explodir — o botão simplesmente não faz nada.
  const winCall = (method) => () => {
    try {
      if (window.pywebview && window.pywebview.api && window.pywebview.api[method]) {
        window.pywebview.api[method]();
      }
    } catch (e) { console.error(e); }
  };
  $("btn-win-min").addEventListener("click", winCall("window_minimize"));
  $("btn-win-max").addEventListener("click", winCall("window_toggle_maximize"));
  $("btn-win-close").addEventListener("click", winCall("window_close"));

  // Configurações — persistir preferências (options.json) quando o usuário muda.
  const persistOpt = (id, key) => {
    const el = $(id);
    if (!el) return;
    el.addEventListener("change", (e) =>
      window.pywebview.api.set_option(key, e.target.type === "checkbox" ? e.target.checked : e.target.value));
  };
  $("settings-language").addEventListener("change", async (e) => {
    const lang = e.target.value;
    try { await window.pywebview.api.set_language(lang); } catch (err) { console.error(err); }
    I18N.language = lang;
    I18N.dict = I18N.tables[lang] || {};
    applyStaticTranslations();
    loadOverview();  // repaint the one dynamic area that renders labels
  });
  persistOpt("settings-remove-audio", "remove_audio");
  persistOpt("settings-remove-playlists", "remove_playlists");
  persistOpt("settings-remove-strings", "remove_strings");
  persistOpt("settings-rebuild-remove", "rebuild_after_remove");
  $("settings-volume").addEventListener("change", (e) => {
    const v = e.target.value.trim() || "MClub";
    window.pywebview.api.set_option("iso_volume_label", v);
    $("gi-label").value = v;  // keep the Gerar ISO screen in sync
  });

  // Rail navigation: highlight the clicked item; scroll to its card if it has one.
  document.querySelectorAll(".rail-item").forEach((item) => {
    item.addEventListener("click", () => {
      document.querySelectorAll(".rail-item").forEach((i) => i.classList.remove("active"));
      item.classList.add("active");
      const target = item.getAttribute("data-target");
      if (target) {
        const el = $(target);
        if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
      }
    });
  });
}

// Uma operacao destrutiva que falha (ou e cancelada) no meio deixa estado
// PARCIAL: parte das faixas ja foi gravada, playlists ja foram editadas. O
// backend manda junto o backup daquela operacao — dizer isso na hora poupa o
// usuario de descobrir sozinho que a saida esta em Recompilar & Backup.
function logBackupHint(logFn, payload) {
  if (!payload || !payload.backup) return;
  logFn("↩ para desfazer: Recompilar & Backup → marque a confirmação → " +
    "“Restaurar último backup”. Backup desta operação: " + payload.backup, "err");
}

// ---- error visibility (F1C): surface backend/loader failures on-screen -----
function showBackendError(msg) {
  const pill = $("backend-pill");
  if (pill) { pill.textContent = msg || "backend indisponível"; pill.classList.add("error"); }
  const banner = $("app-banner");
  if (banner) banner.hidden = false;
}

function showLoadError(elId, msg) {
  const el = $(elId);
  if (el) { el.className = "tools bad"; el.textContent = msg || "✖ falha ao consultar o backend"; }
}

// ---- error report: one click -> pre-filled email to the developer ----------
let lastError = null;

// Called whenever a task fails: remembers what/where + the screen's console text,
// and reveals the report bar so the user can send it in one click.
function noteError(screen, error, consoleBoxId) {
  const box = consoleBoxId ? $(consoleBoxId) : null;
  lastError = { screen: screen, error: error || "", log: box ? box.textContent : "" };
  const bar = $("error-report-bar");
  if (bar) {
    $("error-report-text").textContent = "Ocorreu um erro em: " + screen + ".";
    bar.hidden = false;
  }
}

function reportPayload() {
  return lastError || { screen: "geral", error: "(sem erro específico — diagnóstico geral)", log: "" };
}

function showReport(text) {
  const ta = $("report-text");
  if (ta) { ta.hidden = false; ta.value = text || ""; }
}

async function sendErrorReport() {
  try {
    const r = await window.pywebview.api.send_error_report(reportPayload());
    showReport(r.report);
    const msg = r.opened
      ? "E-mail aberto para " + r.email + " — revise e clique enviar."
      : "Não abriu o e-mail. Use 📋 Copiar e cole numa mensagem para " + r.email + ".";
    const info = $("report-info"); if (info) info.textContent = msg;
    const bar = $("error-report-text"); if (bar) bar.textContent = msg;
  } catch (e) { console.error(e); }
}

function copyToClipboard(text) {
  const ta = $("report-text");
  if (!ta) return false;
  ta.hidden = false; ta.value = text; ta.focus();
  ta.setSelectionRange(0, ta.value.length);
  let ok = false;
  try { ok = document.execCommand("copy"); } catch (e) { ok = false; }
  if (!ok && navigator.clipboard) { navigator.clipboard.writeText(text).catch(() => {}); }
  return ok;
}

async function copyReport() {
  try {
    const r = await window.pywebview.api.get_error_report(reportPayload());
    const ok = copyToClipboard(r.report);
    const info = $("report-info");
    if (info) info.textContent = ok
      ? "Relatório copiado — cole numa mensagem para " + r.email + "."
      : "Selecionado abaixo — copie com Ctrl+C e cole para " + r.email + ".";
  } catch (e) { console.error(e); }
}

// ---- Configurações: carregar preferências salvas + aplicá-las nas telas -----
async function loadSettings() {
  try {
    const o = await window.pywebview.api.get_options();
    $("settings-language").value = o.language || "pt-BR";
    $("settings-volume").value = o.iso_volume_label || "MClub";
    $("settings-remove-audio").checked = o.remove_audio !== false;
    $("settings-remove-playlists").checked = o.remove_playlists !== false;
    $("settings-remove-strings").checked = o.remove_strings !== false;
    $("settings-rebuild-remove").checked = !!o.rebuild_after_remove;
    // Apply the saved defaults to the work screens (so they actually take effect).
    $("gi-label").value = o.iso_volume_label || "MClub";
    $("rm-opt-audio").checked = o.remove_audio !== false;
    $("rm-opt-playlists").checked = o.remove_playlists !== false;
    $("rm-opt-strings").checked = o.remove_strings !== false;
    $("rm-opt-rebuild").checked = !!o.rebuild_after_remove;
  } catch (e) {
    console.error(e);
    showLoadError("settings-tag", "✖ falha ao carregar");
  }
}

async function loadBackendInfo() {
  try {
    await loadI18n();  // translate the static chrome before anything renders
    const info = await window.pywebview.api.get_app_info();
    $("backend-pill").textContent = info.backend;
    $("backend-pill").classList.remove("error");
    $("app-banner").hidden = true;
    $("subtitle").textContent = info.name + " · " + info.version;

    // Load the screens' status, now that the bridge is ready.
    loadOverview();
    loadPrepareStatus();
    loadRebuildStatus();
    loadAddStatus();
    loadRemoveStatus();
    loadIsoStatus();
    loadSettings();
  } catch (e) {
    showBackendError("backend indisponível — recarregue");
    console.error(e);
  }
}

// Wire buttons immediately — this <script> is at the end of <body>, so the DOM
// is parsed. Handlers only *call* the api later, when the bridge is ready.
wireActions();

// Boot once the bridge is ready. Robust against EVERY ordering of the
// pywebviewready event vs. api injection (the documented boot race): try now,
// listen for the event, AND poll as a safety net — because on http-served
// pywebview the event can fire before this listener attaches, which left the
// UI stuck on "verificando ferramentas…".
let __booted = false;
let __bootWarned = false;
function bootWhenReady() {
  if (__booted) return;
  if (window.pywebview && window.pywebview.api && window.pywebview.api.get_app_info) {
    __booted = true;
    loadBackendInfo();  // this hides the banner on success
  }
}
window.addEventListener("pywebviewready", bootWhenReady);
bootWhenReady();
let __bootTries = 0;
const __bootPoll = setInterval(() => {
  bootWhenReady();
  if (__booted) { clearInterval(__bootPoll); return; }  // booted → stop; banner stays hidden
  __bootTries++;
  // A cold WebView2 on a busy machine can take a while, so only warn (once) after
  // ~30s — but KEEP polling, so a late-ready bridge still boots and clears the
  // banner. This avoids the false "sem backend" alarm that used to stick at 10s.
  if (__bootTries === 300 && !__bootWarned) {
    __bootWarned = true;
    showBackendError("backend demorando a conectar — se persistir, recarregue");
  }
  if (__bootTries > 1200) clearInterval(__bootPoll);  // truly give up after ~2 min
}, 100);
