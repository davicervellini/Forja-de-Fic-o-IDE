"use strict";

// ── Utilidades ──────────────────────────────────────────────

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

const S = {
  slug: null,
  project: null,
  current: null,      // número do capítulo aberto
  chapter: null,      // dados do capítulo aberto
  tab: "final",
  panel: "empty",
  editing: false,
  job: null,          // trabalho em andamento (do servidor)
  liveChapter: null,
  lastSeq: 0,
  liveText: "",
  cast: null,         // personagens e universos em edição
  castTab: "characters",
  castSel: 0,
  castDirty: false,       // texto ao vivo do capítulo em geração, mesmo com outra tela aberta
};

async function api(method, path, body) {
  const opts = { method, headers: {} };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(path, opts);
  let data = null;
  try { data = await res.json(); } catch { /* resposta sem corpo */ }
  if (!res.ok) {
    const detail = data && data.detail;
    throw new Error(typeof detail === "string" ? detail : `Erro ${res.status}`);
  }
  return data;
}

function esc(text) {
  return String(text ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function inline(text) {
  return esc(text)
    .replace(/(\*\*|__)(.+?)\1/g, "<strong>$2</strong>")
    .replace(/(^|[^*\w])[*_](?!\s)(.+?)(?<!\s)[*_](?![*\w])/g, "$1<em>$2</em>");
}

function toast(msg, kind = "ok", ms = 5000) {
  const el = document.createElement("div");
  el.className = `toast ${kind}`;
  el.textContent = msg;
  $("#toasts").appendChild(el);
  setTimeout(() => el.remove(), ms);
}

function fail(err) {
  console.error(err);
  toast(err.message || String(err), "error", 8000);
}

function words(text) {
  return (text || "").split(/\s+/).filter(Boolean).length;
}

// Texto de capítulo em HTML de leitura: título, separadores de cena e linhas [System].
function renderReader(el, text, placeholder = "Nada aqui ainda.") {
  if (!text || !text.trim()) {
    el.innerHTML = `<p class="placeholder">${esc(placeholder)}</p>`;
    return;
  }
  const paras = text.trim().split(/\n\s*\n/);
  const out = [];
  paras.forEach((para, i) => {
    const lines = para.split("\n").map(l => l.trim()).filter(Boolean);
    if (!lines.length) return;
    const first = lines[0].replace(/^#+\s*/, "");
    if (i === 0 && lines.length === 1 && /^(chapter|cap[ií]tulo)\s+\d+/i.test(first)) {
      out.push(`<h1>${inline(first)}</h1>`);
    } else if (lines.length === 1 && /^(\*\s*){3}$/.test(lines[0])) {
      out.push('<p class="break">* * *</p>');
    } else if (lines.every(l => l.startsWith("["))) {
      lines.forEach(l => out.push(`<p class="system">${inline(l)}</p>`));
    } else {
      out.push(`<p>${lines.map(inline).join("<br>")}</p>`);
    }
  });
  el.innerHTML = out.join("\n");
}

// ── Diálogo ─────────────────────────────────────────────────

function openDialog({ title, body, actions, wide = false, onOpen }) {
  const dlg = $("#dlg");
  $("#dlg-title").textContent = title;
  $("#dlg-body").innerHTML = body;
  dlg.classList.toggle("wide", wide);
  const bar = $("#dlg-actions");
  bar.innerHTML = "";
  // Enter num campo não pode fechar o diálogo sem passar pelo botão principal.
  $("#dlg-form").onsubmit = ev => ev.preventDefault();
  return new Promise(resolve => {
    actions.forEach(a => {
      const b = document.createElement("button");
      b.type = "button";
      b.textContent = a.label;
      if (a.cls) b.className = a.cls;
      b.onclick = async () => {
        if (a.handler) {
          try {
            const r = await a.handler();
            if (r === false) return;   // handler pediu para manter aberto
          } catch (e) { fail(e); return; }
        }
        dlg.close();
        resolve(a.value);
      };
      bar.appendChild(b);
    });
    dlg.onclose = () => resolve(undefined);
    dlg.showModal();
    if (onOpen) onOpen(dlg);
  });
}

async function confirmDlg(title, html, okLabel = "Confirmar", danger = false) {
  const r = await openDialog({
    title, body: html,
    actions: [{ label: "Cancelar", value: false }, { label: okLabel, value: true, cls: danger ? "danger" : "primary" }],
  });
  return r === true;
}

// ── Status do Ollama e da geração ───────────────────────────

async function pollStatus() {
  try {
    const st = await api("GET", "/api/status");
    const el = $("#ollama-status");
    const ok = st.uses_ollama ? st.ollama : true;
    el.classList.toggle("on", ok);
    el.classList.toggle("off", !ok);
    const where = p => (p === "ollama" ? "" : `${p}: `);
    const models = `${where(st.providers.drafting)}${st.models.drafting} / ${where(st.providers.refining)}${st.models.refining}`;
    S.statusProviders = st.providers;
    $(".txt", el).textContent = !st.uses_ollama ? `Nuvem · ${models}` : st.ollama ? `Ollama · ${models}` : "Ollama desligado";
    el.title = ok ? `Rascunho: ${st.providers.drafting} · Polimento: ${st.providers.refining} · Resumo: ${st.providers.summarizing}`
                  : "O Ollama não respondeu. Abra o Ollama para gerar capítulos.";
    if (st.job && st.job.running && !S.job) setJob(st.job);
  } catch { /* servidor fechando */ }
}

function setJob(job) {
  S.job = job && job.running !== false ? job : null;
  const bar = $("#jobbar");
  bar.classList.toggle("hidden", !S.job);
  if (S.job) {
    $("#job-label").textContent = S.job.label;
    $("#job-status").textContent = S.job.message || "";
    $("#btn-cancel-job").disabled = false;
  }
  applyBusy();
}

function projectBusy() {
  return !!(S.job && S.job.project === S.slug);
}

function applyBusy() {
  const busyHere = projectBusy();
  const anyBusy = !!S.job;
  ["#btn-add-chapter", "#btn-redo", "#btn-delete-chapter", "#btn-edit-final", "#btn-save-final",
   "#btn-save-premise", "#btn-suggest-premise", "#btn-check-premise", "#btn-refresh-memory", "#btn-save-state", "#btn-save-akashic", "#btn-save-cast"].forEach(sel => {
    const el = $(sel);
    if (el) el.disabled = busyHere;
  });
  $("#btn-generate").disabled = anyBusy;
  $("#btn-redo").disabled = anyBusy;
  $("#btn-refresh-memory").disabled = anyBusy;
  $$("#versions-list button").forEach(b => { b.disabled = busyHere; });
}

function connectEvents() {
  const es = new EventSource("/api/events");
  es.onmessage = ev => handleEvent(JSON.parse(ev.data));
  es.onerror = () => { /* o EventSource reconecta sozinho */ };
}

function handleEvent(e) {
  // O EventSource reconecta sozinho e o servidor manda de novo o histórico: nada é aplicado duas vezes.
  if (e.seq <= S.lastSeq) return;
  S.lastSeq = e.seq;
  const quiet = !!e.replay;
  switch (e.type) {
    case "job_start":
      setJob(e.job);
      if (e.job.kind === "wiki" && e.job.project === S.slug) {
        S.wiki = { results: [], total: (S.wiki && S.wiki.total) || 0 };
        renderWikiReview();
      }
      S.liveChapter = null;
      break;
    case "status":
      if (S.job) {
        S.job.message = e.message;
        $("#job-status").textContent = e.message;
      }
      break;
    case "chapter_start":
      S.liveChapter = e.chapter;
      S.liveText = "";
      if (S.job) S.job.chapter = e.chapter;
      $("#live-view").textContent = "";
      if (S.job && S.job.project === S.slug) {
        refreshProject().then(() => {
          if (S.current !== e.chapter && !S.editing) selectChapter(e.chapter, "live");
          else showLiveTab(true);
        });
      }
      break;
    case "token":
      if (e.chapter === S.liveChapter) S.liveText += e.text;
      if (S.job && S.job.project === S.slug && e.chapter === S.current) {
        const live = $("#live-view");
        const atBottom = $("#content").scrollHeight - $("#content").scrollTop - $("#content").clientHeight < 80;
        live.textContent = S.liveText;
        if (atBottom && S.tab === "live") $("#content").scrollTop = $("#content").scrollHeight;
      }
      break;
    case "phase_complete":
      if (S.job && S.job.project === S.slug) {
        if (e.phase === "drafting" || e.phase === "refining") {
          S.liveText += "\n\n";
          $("#live-view").textContent = S.liveText;
        }
        refreshProject();
      }
      break;
    case "chapter_complete":
      if (!quiet && S.job && S.job.project === S.slug) toast(`Capítulo ${String(e.chapter).padStart(2, "0")} pronto.`);
      break;
    case "premise_result":
    case "premise_check":
      onPremiseEvent(e);
      break;
    case "wiki_result":
      if (e.project === S.slug) {
        if (!S.wiki) S.wiki = { results: [], total: e.total };
        S.wiki.total = e.total;
        S.wiki.results.push({ ...e.result, universe: e.universe, include: e.result.found });
        renderWikiReview();
      }
      break;
    case "error":
      // Cancelamento pedido pelo usuário já ganha aviso próprio no fim do trabalho.
      if (!quiet && !e.cancelled) toast(e.message, "error", 10000);
      break;
    case "job_end": {
      const was = S.job;
      setJob(null);
      showLiveTab(false);
      if (!quiet && e.job && e.job.status === "cancelled") toast("Geração cancelada.", "warn");
      if (was && was.kind && was.kind.startsWith("premise")) break;
      if (was && was.kind === "wiki") {
        if (was.project === S.slug && S.panel === "cast") renderCast();
        break;
      }
      if (was && was.project === S.slug) {
        refreshProject().then(() => { if (S.current) loadChapter(S.current, S.tab === "live" ? "final" : S.tab); });
      }
      break;
    }
  }
}

function showLiveTab(on) {
  $("#tab-live").classList.toggle("hidden", !on);
  if (on) switchTab("live");
  else if (S.tab === "live") switchTab("final");
}

// ── Navegação ───────────────────────────────────────────────

function showView(name) {
  $("#view-projects").classList.toggle("hidden", name !== "projects");
  $("#view-project").classList.toggle("hidden", name !== "project");
}

async function leaveEditing() {
  const castDirty = S.panel === "cast" && S.castDirty;
  if (!S.editing && !premiseDirty() && !castDirty) return true;
  const where = castDirty ? "nos personagens e universos" : "neste capítulo";
  const ok = await confirmDlg("Alterações não salvas", `<p>Há alterações não salvas ${where}. Descartar?</p>`, "Descartar", true);
  if (ok) {
    setEditing(false);
    markCastDirty(false);
    S.premiseFormDirty = false;
    $("#premise-edit").dataset.saved = $("#premise-edit").value;
  }
  return ok;
}

async function goHome() {
  if (!(await leaveEditing())) return;
  S.slug = null; S.project = null; S.current = null;
  $("#crumb").textContent = "";
  showView("projects");
  loadProjects();
}

// ── Projetos ────────────────────────────────────────────────

async function loadProjects() {
  const list = $("#project-list");
  try {
    const projects = await api("GET", "/api/projects");
    if (!projects.length) {
      list.innerHTML = '<p class="muted">Nenhum projeto ainda. Crie o primeiro com <b>+ Novo projeto</b>.</p>';
      return;
    }
    list.innerHTML = "";
    projects.forEach(p => {
      const card = document.createElement("div");
      card.className = "project-card";
      const when = p.last_modified ? new Date(p.last_modified).toLocaleString("pt-BR") : "";
      card.innerHTML = `<h3>${esc(p.name)}</h3>
        <span class="muted">${p.last_chapter ? `${p.last_chapter} capítulo(s) concluído(s)` : "Nenhum capítulo concluído"}</span>
        <span class="muted">${esc(when)}</span>
        <div class="row"><span class="spacer"></span><button class="danger-ghost" data-del>Mover para a lixeira</button></div>`;
      card.onclick = ev => { if (!ev.target.closest("[data-del]")) openProject(p.slug); };
      $("[data-del]", card).onclick = async () => {
        if (!(await confirmDlg("Excluir projeto", `<p>Mover <b>${esc(p.name)}</b> para a lixeira de projetos (<code>projetos/_lixeira</code>)? Dá para recuperar a pasta de lá.</p>`, "Mover para a lixeira", true))) return;
        try { toast((await api("DELETE", `/api/projects/${p.slug}`)).message); loadProjects(); } catch (e) { fail(e); }
      };
      list.appendChild(card);
    });
  } catch (e) { fail(e); }
}

async function newProject() {
  let akashic = "";
  const r = await openDialog({
    title: "Novo projeto",
    body: `<label>Nome da história<input id="np-name" autofocus></label>
      <label>Registro Akáshico (opcional)<input type="file" id="np-file" accept=".md,.txt"></label>
      <p class="muted">O Registro Akáshico é a bíblia da história: mundo, personagens, regras e estilo. Dá para importar agora ou depois, e o assistente de criação guiado ainda está na versão antiga do programa.</p>`,
    actions: [
      { label: "Cancelar", value: null },
      { label: "Criar", cls: "primary", value: "ok", handler: async () => {
        const name = $("#np-name").value.trim();
        if (!name) { toast("Dê um nome ao projeto.", "warn"); return false; }
        const f = $("#np-file").files[0];
        if (f) akashic = await f.text();
        const res = await api("POST", "/api/projects", { name, akashic_text: akashic });
        if (res.message) toast(res.message);
        S.pendingOpen = res.slug;
      } },
    ],
  });
  if (r === "ok" && S.pendingOpen) openProject(S.pendingOpen);
}

async function openProject(slug) {
  S.slug = slug;
  S.current = null;
  showView("project");
  showPanel("empty");
  await refreshProject();
  applyBusy();
  if (S.job && S.job.project === slug && S.job.chapter) selectChapter(S.job.chapter, "live");
}

async function refreshProject() {
  if (!S.slug) return;
  try {
    S.project = await api("GET", `/api/projects/${S.slug}`);
  } catch (e) { fail(e); return; }
  $("#crumb").textContent = `› ${S.project.name}`;
  renderChapterList();
}

const STATUS_TEXT = {
  done: "Pronto", pending: "Pendente", drafting: "Rascunho incompleto", polishing: "Sem polimento",
  summarizing: "Sem resumo", error: "Erro",
};

function renderChapterList() {
  const ol = $("#chapter-list");
  ol.innerHTML = "";
  if (!S.project.chapters.length) {
    ol.innerHTML = '<li class="muted" style="cursor:default">Nenhum capítulo.</li>';
    return;
  }
  S.project.chapters.forEach(c => {
    const li = document.createElement("li");
    const running = S.job && S.job.project === S.slug && S.job.chapter === c.num;
    li.className = c.num === S.current && S.panel === "chapter" ? "active" : "";
    li.innerHTML = `<span class="status-dot ${running ? "st-running" : "st-" + c.status}" title="${esc(STATUS_TEXT[c.status] || c.status)}"></span>
      <span class="num">${String(c.num).padStart(2, "0")}</span>
      <span class="name">${esc(c.title.replace(/^(chapter|cap[ií]tulo)\s+\d+\s*[:.\-–—]?\s*/i, "") || "Sem título")}</span>
      ${c.memory_stale ? '<span class="stale" title="Texto mudou depois da memória">↻</span>' : ""}
      <span class="muted">${c.words ? c.words : ""}</span>`;
    li.onclick = () => selectChapter(c.num);
    ol.appendChild(li);
  });
}

function showPanel(name) {
  S.panel = name;
  ["chapter", "cast", "story", "akashic", "export", "empty"].forEach(p => $(`#panel-${p}`).classList.toggle("hidden", p !== name));
  $$(".side-footer .link").forEach(b => b.classList.toggle("active", b.dataset.panel === name));
  if (S.project) renderChapterList();
}

// ── Capítulo ────────────────────────────────────────────────

async function selectChapter(num, tab) {
  if (num !== S.current && !(await leaveEditing())) return;
  S.current = num;
  showPanel("chapter");
  await loadChapter(num, tab || (S.tab === "live" ? "final" : S.tab));
}

async function loadChapter(num, tab = "final") {
  let c;
  try { c = await api("GET", `/api/projects/${S.slug}/chapters/${num}`); } catch (e) { fail(e); return; }
  if (S.current !== num) return;
  S.chapter = c;
  const meta = S.project.chapters.find(x => x.num === num) || { status: "pending", title: "" };
  $("#ch-title").textContent = meta.title || `Capítulo ${String(num).padStart(2, "0")}`;
  const badge = $("#ch-status");
  badge.textContent = STATUS_TEXT[meta.status] || meta.status;
  badge.className = `badge ${meta.status}`;
  $("#ch-words").textContent = c.final ? `${words(c.final)} palavras` : c.draft ? `rascunho: ${words(c.draft)} palavras` : "";

  const notes = [];
  if (c.info.memory_stale) notes.push("O texto final mudou depois que a memória da história foi atualizada. Use <b>↻ Atualizar memória</b> para refazer o resumo e a memória com o texto novo.");
  if (c.info.memory_note) notes.push(esc(c.info.memory_note));
  $("#ch-notice").innerHTML = notes.join("<br>");
  $("#ch-notice").classList.toggle("hidden", !notes.length);
  $("#btn-refresh-memory").classList.toggle("hidden", !(c.info.memory_stale && c.final));
  $("#btn-redo").textContent = meta.status === "done" || c.final ? "⟲ Refazer" : "▶ Gerar";

  setEditing(false);
  renderReader($("#final-view"), c.final, meta.status === "pending" ? "Capítulo ainda não gerado. Escreva a premissa e clique em ▶ Gerar." : "Sem texto final.");
  renderReader($("#draft-view"), c.draft, "Sem rascunho.");
  renderReader($("#summary-view"), c.summary, "Sem resumo. O resumo é escrito quando o capítulo termina.");
  $("#premise-edit").value = c.premise;
  $("#premise-edit").dataset.saved = c.premise;
  S.premiseFormDirty = false;
  setPremiseMode(S.premiseMode || "form");
  loadPremiseForm(num);
  renderExtras(c);
  renderVersions(c);
  const liveOn = !!(S.job && S.job.project === S.slug && S.job.chapter === num);
  $("#tab-live").classList.toggle("hidden", !liveOn);
  $("#live-view").textContent = liveOn ? S.liveText : "";
  switchTab(liveOn && tab === "live" ? "live" : tab === "live" ? "final" : tab);
  applyBusy();
}

function switchTab(tab) {
  S.tab = tab;
  $$("#ch-tabs button").forEach(b => b.classList.toggle("active", b.dataset.tab === tab));
  $$("#panel-chapter .tab").forEach(t => t.classList.toggle("hidden", t.dataset.tab !== tab));
}

function renderExtras(c) {
  const labels = {
    consistency: ["Checagem de consistência", "Compara o capítulo com o Registro Akáshico e a memória."],
    rejected_polish: ["Polimento descartado", "Cenas em que o polimento encolheu demais o texto. O capítulo final usa o rascunho delas."],
    planned_scenes: ["Cenas planejadas", "Divisão em cenas feita pelo modelo, quando a premissa não trazia cenas numeradas."],
  };
  const box = $("#extras-view");
  const parts = Object.entries(labels).filter(([k]) => c.extras[k] && c.extras[k].trim()).map(([k, [title, hint]]) =>
    `<div class="extra-block"><h3>${title}</h3><p class="muted">${hint}</p><article class="reader small" data-extra="${k}"></article></div>`);
  box.innerHTML = parts.length ? parts.join("") : '<p class="muted">Nenhuma verificação para este capítulo.</p>';
  $$("[data-extra]", box).forEach(el => renderReader(el, c.extras[el.dataset.extra]));
}

function renderVersions(c) {
  const box = $("#versions-list");
  if (!c.versions.length) { box.innerHTML = '<p class="muted">Nenhuma versão guardada ainda.</p>'; return; }
  box.innerHTML = "";
  c.versions.forEach(v => {
    const row = document.createElement("div");
    row.className = "version";
    const when = v.created ? new Date(v.created).toLocaleString("pt-BR") : v.id;
    row.innerHTML = `<span class="when">${esc(when)}</span><span class="muted">${esc(v.reason_label)}</span>
      <span class="muted">${v.words ? v.words + " palavras" : ""}</span><span class="spacer"></span>
      <button data-view>Ver</button><button data-restore>Restaurar</button>`;
    $("[data-view]", row).onclick = () => viewVersion(v);
    $("[data-restore]", row).onclick = () => restoreVersion(v);
    box.appendChild(row);
  });
}

async function viewVersion(v) {
  try {
    const { files } = await api("GET", `/api/projects/${S.slug}/chapters/${S.current}/versions/${encodeURIComponent(v.id)}`);
    const text = files["capitulo_final.md"] || files["rascunho.md"] || files["premissa.md"] || "";
    await openDialog({
      title: `Versão de ${v.created ? new Date(v.created).toLocaleString("pt-BR") : v.id}`,
      body: '<article class="reader small" id="ver-view"></article>',
      wide: true,
      actions: [{ label: "Fechar", value: null }],
      onOpen: () => renderReader($("#ver-view"), text),
    });
  } catch (e) { fail(e); }
}

async function restoreVersion(v) {
  const ok = await confirmDlg("Restaurar versão",
    "<p>Os textos atuais do capítulo viram uma versão nova e os desta versão voltam. O resumo e a memória não são recalculados: se o capítulo estiver pronto, use ↻ Atualizar memória depois.</p>",
    "Restaurar");
  if (!ok) return;
  try {
    toast((await api("POST", `/api/projects/${S.slug}/chapters/${S.current}/versions/${encodeURIComponent(v.id)}/restore`)).message);
    await refreshProject();
    loadChapter(S.current, "final");
  } catch (e) { fail(e); }
}

function setEditing(on) {
  S.editing = on;
  $("#final-view").classList.toggle("hidden", on);
  $("#final-edit").classList.toggle("hidden", !on);
  $("#btn-edit-final").classList.toggle("hidden", on);
  $("#btn-save-final").classList.toggle("hidden", !on);
  $("#btn-cancel-final").classList.toggle("hidden", !on);
  $("#final-hint").textContent = on ? "Markdown simples: *itálico*, **negrito**, * * * entre cenas." : "";
  if (on) {
    $("#final-edit").value = S.chapter.final || "";
    $("#final-edit").focus();
  }
}

async function saveFinal() {
  try {
    const res = await api("PUT", `/api/projects/${S.slug}/chapters/${S.current}/final`, { text: $("#final-edit").value });
    toast(res.message);
    setEditing(false);
    await refreshProject();
    loadChapter(S.current, "final");
  } catch (e) { fail(e); }
}

async function generate(nums) {
  try {
    await api("POST", `/api/projects/${S.slug}/generate`, { chapters: nums || null });
  } catch (e) { fail(e); }
}

async function redoChapter() {
  const c = S.chapter;
  const meta = S.project.chapters.find(x => x.num === S.current);
  if (!c.final && meta.status !== "done") {
    if (premiseDirty()) await savePremise(true);
    return generate([S.current]);
  }
  const later = c.later_done.map(n => String(n).padStart(2, "0")).join(", ");
  const warn = later
    ? `<p class="notice">Os capítulos ${later} vêm depois e foram escritos com a versão atual na memória. O texto novo usa a memória de antes deste capítulo, mas memória, roster e pontas soltas <b>não</b> são recalculados.</p>`
    : `<p class="muted">A memória da história volta ao estado de antes deste capítulo e ele é escrito, polido e resumido de novo.</p>`;
  const r = await openDialog({
    title: `Refazer ${$("#ch-title").textContent}`,
    body: `${warn}<p class="muted">O texto atual fica guardado em Versões. Se a geração falhar ou for cancelada, ele volta.</p>
      <label>Premissa (pode ajustar antes de refazer)<textarea class="editor" id="rd-premise" style="min-height:260px;font:14px/1.5 var(--mono)"></textarea></label>`,
    wide: true,
    actions: [{ label: "Cancelar", value: null }, { label: "Refazer", cls: "primary", value: "ok", handler: () => { S.redoPremise = $("#rd-premise").value; } }],
    onOpen: async () => {
      if (premiseDirty()) await savePremise(true);
      $("#rd-premise").value = $("#premise-edit").value;
    },
  });
  if (r !== "ok") return;
  try {
    await api("POST", `/api/projects/${S.slug}/chapters/${S.current}/redo`, { premise: S.redoPremise });
  } catch (e) { fail(e); }
}

async function refreshMemory() {
  const later = S.chapter.later_done.length;
  const ok = await confirmDlg("Atualizar memória",
    later ? "<p>Há capítulos prontos depois deste: só o resumo dele é refeito. Memória, roster e pontas soltas ficam como estão.</p>"
          : "<p>A memória volta ao estado de antes deste capítulo e é atualizada com o texto final atual (resumo, memória, roster, pontas soltas).</p>",
    "Atualizar");
  if (!ok) return;
  try { await api("POST", `/api/projects/${S.slug}/chapters/${S.current}/refresh-memory`); } catch (e) { fail(e); }
}

async function deleteChapter() {
  const later = S.chapter.later_done.map(n => String(n).padStart(2, "0")).join(", ");
  const ok = await confirmDlg("Excluir capítulo",
    `<p>A pasta do capítulo vai para a lixeira do projeto (<code>_lixeira</code>) e a memória da história é ajustada.</p>
     ${later ? `<p class="notice">Os capítulos ${later} vêm depois e foram escritos com este na memória. O resumo dele sai, mas memória, roster e pontas soltas continuam com fatos dele.</p>` : ""}`,
    "Excluir", true);
  if (!ok) return;
  try {
    toast((await api("DELETE", `/api/projects/${S.slug}/chapters/${S.current}`)).message, "ok", 8000);
    S.current = null;
    showPanel("empty");
    refreshProject();
  } catch (e) { fail(e); }
}

// ── Painéis do projeto ──────────────────────────────────────

async function openPanel(name) {
  if (!(await leaveEditing())) return;
  S.current = null;
  showPanel(name);
  if (name === "cast") {
    await loadCast();
  } else if (name === "story") {
    await refreshProject();
    $$("[data-state]").forEach(t => { t.value = S.project.state[t.dataset.state] || ""; });
    const box = $("#summaries");
    const sums = S.project.state.summaries;
    box.innerHTML = sums.length ? "" : '<p class="muted">Nenhum resumo ainda.</p>';
    sums.forEach(s => {
      const div = document.createElement("div");
      div.className = "extra-block";
      div.innerHTML = `<h3>Capítulo ${String(s.num).padStart(2, "0")}</h3><article class="reader small"></article>`;
      renderReader($("article", div), s.text);
      box.appendChild(div);
    });
  } else if (name === "akashic") {
    try { $("#akashic-edit").value = (await api("GET", `/api/projects/${S.slug}/akashic`)).text; } catch (e) { fail(e); }
    $("#akashic-msg").textContent = "";
  } else if (name === "export") {
    const m = S.project.meta;
    $("#meta-title").value = m.book_title || "";
    $("#meta-author").value = m.author || "";
    $("#meta-lang").value = m.language || "en";
    $("#export-msg").textContent = "";
  }
  applyBusy();
}

async function saveState() {
  const body = {};
  $$("[data-state]").forEach(t => { body[t.dataset.state] = t.value; });
  try { await api("PUT", `/api/projects/${S.slug}/state`, body); toast("Memória da história salva."); } catch (e) { fail(e); }
}

async function saveAkashic() {
  try {
    const res = await api("PUT", `/api/projects/${S.slug}/akashic`, { text: $("#akashic-edit").value });
    $("#akashic-msg").textContent = res.message;
    toast("Registro salvo.");
  } catch (e) { fail(e); }
}

async function exportBook() {
  const fmt = $("#export-format").value;
  const first = parseInt($("#export-first").value, 10) || null;
  const last = parseInt($("#export-last").value, 10) || null;
  try {
    await api("PUT", `/api/projects/${S.slug}/meta`, {
      book_title: $("#meta-title").value, author: $("#meta-author").value, language: $("#meta-lang").value,
    });
    const desktop = window.pywebview && window.pywebview.api;
    if (!desktop) {
      const q = new URLSearchParams({ format: fmt });
      if (first) q.set("first", first);
      if (last) q.set("last", last);
      window.location = `/api/projects/${S.slug}/export/download?${q}`;
      return;
    }
    const path = await desktop.save_dialog(`${S.slug}.${fmt}`, fmt);
    if (!path) return;
    const res = await api("POST", `/api/projects/${S.slug}/export`, { format: fmt, first, last, path });
    $("#export-msg").innerHTML = `${res.chapters} capítulo(s) exportado(s) para <code>${esc(res.path)}</code> <button class="link" id="btn-reveal">Abrir pasta</button>`;
    $("#btn-reveal").onclick = () => desktop.reveal(res.path);
    toast("Livro exportado.");
  } catch (e) { fail(e); }
}

// ── Personagens e universos ─────────────────────────────────

function markCastDirty(on = true) {
  S.castDirty = on;
  $("#cast-dirty").textContent = on ? "alterações não salvas" : "";
}

async function loadCast() {
  try { S.cast = await api("GET", `/api/projects/${S.slug}/cast`); } catch (e) { return fail(e); }
  S.castSel = 0;
  markCastDirty(false);
  renderCast();
}

function castItems() {
  return S.castTab === "universes" ? S.cast.universes : S.cast.characters;
}

function renderCast() {
  const c = S.cast;
  const v1 = $("#cast-v1");
  if (c.format !== "v2") {
    v1.innerHTML = c.format === "none"
      ? "Este projeto ainda não tem Registro Akáshico. Importe um em <b>Registro Akáshico</b> ou crie pelo assistente da versão antiga."
      : 'O Registro Akáshico está no formato antigo (v1): personagens e universos ainda não foram lidos do texto. <button id="btn-cast-migrate">Converter para v2</button>';
    v1.classList.remove("hidden");
    const b = $("#btn-cast-migrate");
    if (b) b.onclick = migrateCast;
  } else {
    v1.classList.add("hidden");
  }
  const editable = c.format === "v2";
  $("#btn-save-cast").disabled = !editable || projectBusy();
  $("#btn-cast-add").disabled = !editable;
  $$("#cast-tabs button").forEach(b => b.classList.toggle("active", b.dataset.ctab === S.castTab));
  renderCastList();
  renderCastForm();
  renderRosterMissing();
  renderWikiReview();
  $("#btn-wiki-import").disabled = !editable || !!S.job;
}

function renderCastList() {
  const ul = $("#cast-items");
  const items = castItems();
  ul.innerHTML = "";
  if (!items.length) { ul.innerHTML = '<li class="muted" style="cursor:default">Nenhum ainda.</li>'; return; }
  items.forEach((it, i) => {
    const li = document.createElement("li");
    li.className = (i === S.castSel ? "active " : "") + (S.castTab === "universes" && !it.active ? "inactive" : "");
    let meta;
    if (S.castTab === "universes") {
      meta = `${esc(S.cast.universe_roles[it.role] || it.role).split(" (")[0]} · ${it.active ? "na história" : "reserva"}${it.active && !it.model_sheet.trim() ? ' · <span class="warn">sem ficha</span>' : ""}`;
    } else {
      const seen = (S.cast.appearances[it.name] || []).length;
      meta = `${esc(S.cast.character_roles[it.role] || it.role)}${it.sheet.trim() ? "" : ' · <span class="warn">sem ficha</span>'}${seen ? ` · ${seen} cap.` : ""}`;
    }
    li.innerHTML = `<span class="cast-name">${esc(it.name || "(sem nome)")}</span><span class="cast-meta">${meta}</span>`;
    li.onclick = () => { S.castSel = i; renderCastList(); renderCastForm(); };
    ul.appendChild(li);
  });
}

function field(label, key, value, kind = "input", extra = "") {
  const v = esc(value ?? "");
  if (kind === "textarea") return `<label>${label}<textarea data-f="${key}" ${extra}>${v}</textarea></label>`;
  return `<label>${label}<input data-f="${key}" value="${v}" ${extra}></label>`;
}

function select(label, key, value, options) {
  const opts = Object.entries(options).map(([k, l]) => `<option value="${esc(k)}" ${k === value ? "selected" : ""}>${esc(l)}</option>`).join("");
  return `<label>${label}<select data-f="${key}">${opts}</select></label>`;
}

function renderCastForm() {
  const box = $("#cast-form");
  const items = castItems();
  const it = items[S.castSel];
  if (!it) { box.innerHTML = '<p class="muted">Escolha um item à esquerda ou adicione um novo.</p>'; return; }
  const ro = S.cast.format !== "v2" ? "disabled" : "";
  if (S.castTab === "characters") {
    const unis = { "": "—", ...Object.fromEntries(S.cast.universes.map(u => [u.id, u.name])) };
    const seen = S.cast.appearances[it.name] || [];
    box.innerHTML = `
      <div class="form-grid">
        ${field("Nome", "name", it.name)}
        ${select("Papel", "role", it.role, S.cast.character_roles)}
        ${field("Origem (nativo, reencarnado, transportado…)", "origin", it.origin)}
        ${field("Idade", "age", it.age)}
        ${select("Universo de origem", "universe", it.universe, unis)}
        ${field("Rótulo na lista do modelo (opcional)", "sheet_label", it.sheet_label, "input", 'placeholder="ex.: Tinaia, the central AI."')}
      </div>
      ${field(`Ficha para o modelo, em inglês <span class="muted" data-count></span>`, "sheet", it.sheet, "textarea", 'class="sheet" placeholder="One paragraph: origin, look, personality, powers, how they speak."')}
      <p class="muted">É o que o modelo lê sobre o personagem em todo capítulo (seção 5.8). Um parágrafo curto: aparência, personalidade, poderes e jeito de falar.${it.role === "protagonist" ? " A ficha do protagonista também vai no fim de cada cena, para segurar a voz dele." : ""}</p>
      ${field("Notas do autor (não vão para o modelo)", "notes", it.notes, "textarea", 'class="notes"')}
      <div class="muted">Aparece nos capítulos:</div>
      <div class="chips">${seen.length ? seen.map(n => `<span class="chip" data-ch="${n}">${String(n).padStart(2, "0")}</span>`).join("") : '<span class="muted">nenhum ainda</span>'}</div>
      <div class="row-actions">
        <button type="button" data-move="-1">↑</button><button type="button" data-move="1">↓</button>
        <button type="button" data-wiki-one title="Busca este personagem na wiki do universo de origem e escreve a ficha">⇩ Buscar na Wiki</button>
        <span class="spacer"></span><button type="button" class="danger-ghost" data-remove>Remover personagem</button>
      </div>`;
  } else {
    box.innerHTML = `
      <div class="form-grid">
        ${field("Nome", "name", it.name)}
        ${select("Papel na história", "role", it.role, S.cast.universe_roles)}
        ${field("Wiki do Fandom (subdomínio, ex.: stargate)", "wiki", it.wiki)}
      </div>
      <label class="check"><input type="checkbox" data-f="active" ${it.active ? "checked" : ""}> Faz parte da história (desmarcado = reserva, fora da lista fechada que o modelo recebe)</label>
      ${field("Ficha para o modelo, em inglês", "model_sheet", it.model_sheet, "textarea", 'class="sheet" placeholder="What exists here, what never appears, tone."')}
      ${field("Personagens permitidos deste universo (separados por vírgula)", "allowed_characters", (it.allowed_characters || []).join(", "))}
      ${field("Notas do autor (não vão para o modelo)", "notes", it.notes, "textarea", 'class="notes"')}
      <div class="row-actions">
        <button type="button" data-move="-1">↑</button><button type="button" data-move="1">↓</button>
        <span class="spacer"></span><button type="button" class="danger-ghost" data-remove>Remover universo</button>
      </div>`;
  }
  if (ro) $$("input, select, textarea, button", box).forEach(el => { el.disabled = true; });
  const count = () => { const c = $("[data-count]", box); if (c) c.textContent = `(${words(it.sheet)} palavras)`; };
  count();
  $$("[data-f]", box).forEach(el => {
    el.addEventListener("input", () => {
      const k = el.dataset.f;
      if (k === "active") it.active = el.checked;
      else if (k === "allowed_characters") it.allowed_characters = el.value.split(",").map(s => s.trim()).filter(Boolean);
      else it[k] = el.value;
      markCastDirty();
      count();
      renderCastList();
    });
  });
  const one = $("[data-wiki-one]", box);
  if (one) {
    one.disabled = !!S.job || !!ro;
    one.onclick = () => {
      const u = S.cast.universes.find(x => x.id === it.universe);
      openWikiDialog([it.name], u && u.wiki ? u.name : null);
    };
  }
  $$("[data-ch]", box).forEach(chip => { chip.onclick = () => selectChapter(parseInt(chip.dataset.ch, 10)); });
  $$("[data-move]", box).forEach(b => {
    b.onclick = () => {
      const j = S.castSel + parseInt(b.dataset.move, 10);
      if (j < 0 || j >= items.length) return;
      [items[S.castSel], items[j]] = [items[j], items[S.castSel]];
      S.castSel = j;
      markCastDirty();
      renderCastList();
    };
  });
  $("[data-remove]", box).onclick = async () => {
    if (!(await confirmDlg("Remover", `<p>Remover <b>${esc(it.name)}</b> do registro? A remoção só vale depois de Salvar.</p>`, "Remover", true))) return;
    items.splice(S.castSel, 1);
    S.castSel = Math.max(0, S.castSel - 1);
    markCastDirty();
    renderCast();
  };
}

function renderRosterMissing() {
  const box = $("#roster-missing");
  const miss = S.castTab === "characters" && S.cast.format === "v2" ? S.cast.roster_missing : [];
  if (!miss.length) { box.innerHTML = ""; return; }
  box.innerHTML = `<div class="roster-box"><h4>Na memória da história, fora do registro</h4>
    <div class="muted">Personagens que surgiram nos capítulos. Adicione ao registro para o modelo ter a ficha deles.</div>
    ${miss.map((r, i) => `<div class="item"><span>${esc(r.name)}</span><button type="button" data-add-roster="${i}">Adicionar</button></div>`).join("")}</div>`;
  $$("[data-add-roster]", box).forEach(b => {
    b.onclick = () => {
      const r = miss[parseInt(b.dataset.addRoster, 10)];
      S.cast.characters.push({ name: r.name, role: "supporting", origin: "", age: "", universe: "", notes: r.text, sheet: "", sheet_label: "" });
      S.cast.roster_missing = miss.filter(x => x !== r);
      S.castSel = S.cast.characters.length - 1;
      markCastDirty();
      renderCast();
      toast(`${r.name} adicionado. O bloco do roster foi para as notas; escreva a ficha para o modelo e salve.`, "ok", 7000);
    };
  });
}

function addCastItem() {
  if (S.castTab === "universes") {
    S.cast.universes.push({ id: "", name: "Novo universo", role: "source", wiki: "", active: true, notes: "", allowed_characters: [], model_sheet: "" });
    S.castSel = S.cast.universes.length - 1;
  } else {
    S.cast.characters.push({ name: "Novo personagem", role: "supporting", origin: "", age: "", universe: "", notes: "", sheet: "", sheet_label: "" });
    S.castSel = S.cast.characters.length - 1;
  }
  markCastDirty();
  renderCast();
  const name = $('#cast-form [data-f="name"]');
  if (name) { name.focus(); name.select(); }
}

async function saveCast() {
  try {
    const res = await api("PUT", `/api/projects/${S.slug}/cast`, {
      characters: S.cast.characters, universes: S.cast.universes, structure: S.cast.structure,
    });
    const sel = S.castSel;
    S.cast = res;
    S.castSel = Math.min(sel, castItems().length - 1);
    markCastDirty(false);
    renderCast();
    toast(`Registro salvo. ${res.message}`, "ok", 7000);
  } catch (e) { fail(e); }
}

async function migrateCast() {
  const ok = await confirmDlg("Converter registro",
    "<p>Converte o Registro Akáshico para o formato v2: universos e personagens são lidos do texto e passam a ser editados nesta tela. O arquivo anterior fica em <code>registro_akashico.anterior.md</code>.</p>", "Converter");
  if (!ok) return;
  try {
    const res = await api("POST", `/api/projects/${S.slug}/cast/migrate`);
    S.cast = res;
    S.castSel = 0;
    renderCast();
    toast(res.message, "ok", 8000);
  } catch (e) { fail(e); }
}


// ── Importação da Wiki ──────────────────────────────────────

function wikiUniverses() {
  return (S.cast ? S.cast.universes : []).filter(u => u.wiki && u.wiki.trim())
    .sort((a, b) => Number(b.active) - Number(a.active));
}

function findCharacter(name) {
  const n = name.trim().toLowerCase();
  return S.cast.characters.find(c => c.name.trim().toLowerCase() === n);
}

async function openWikiDialog(names = [], universe = null) {
  if (S.castDirty) {
    if (!(await confirmDlg("Alterações não salvas", "<p>Salve ou descarte as alterações antes de importar. Descartar agora?</p>", "Descartar", true))) return;
    await loadCast();
  }
  const unis = wikiUniverses();
  if (!unis.length) {
    toast("Nenhum universo tem wiki cadastrada. Preencha o campo Wiki (ex.: stargate) na aba Universos e salve.", "warn", 9000);
    return;
  }
  const opts = unis.map(u => `<option value="${esc(u.name)}" ${u.name === universe ? "selected" : ""}>${esc(u.name)}${u.active ? "" : " (reserva)"} · ${esc(u.wiki)}.fandom.com</option>`).join("");
  const r = await openDialog({
    title: "Importar personagens da Wiki",
    body: `<label>Universo<select id="wk-universe">${opts}</select></label>
      <label>Nomes (um por linha)<textarea id="wk-names" class="editor small" style="min-height:140px">${esc(names.join("\n"))}</textarea></label>
      <p class="muted">Para cada nome, o programa busca a página na wiki do Fandom e o modelo da fase de resumo (${esc((S.statusProviders && S.statusProviders.summarizing) || "configurado")}) escreve a ficha em inglês. Você revisa antes de entrar no registro.</p>`,
    actions: [{ label: "Cancelar", value: null }, { label: "Buscar", cls: "primary", value: "ok", handler: () => {
      S.wikiRequest = { universe: $("#wk-universe").value, names: $("#wk-names").value.split(/[\n,]/).map(s => s.trim()).filter(Boolean) };
      if (!S.wikiRequest.names.length) { toast("Digite pelo menos um nome.", "warn"); return false; }
    } }],
    onOpen: () => $("#wk-names").focus(),
  });
  if (r !== "ok") return;
  S.wiki = { results: [], total: S.wikiRequest.names.length };
  renderWikiReview();
  try { await api("POST", `/api/projects/${S.slug}/wiki/import`, S.wikiRequest); } catch (e) { fail(e); S.wiki = null; renderWikiReview(); }
}

function renderWikiReview() {
  const box = $("#wiki-review");
  if (!S.wiki || S.panel !== "cast" || !S.cast) { box.innerHTML = ""; return; }
  const running = !!(S.job && S.job.kind === "wiki" && S.job.project === S.slug);
  const res = S.wiki.results;
  const rows = res.map((r, i) => {
    const dup = r.found && findCharacter(r.name);
    const info = r.found
      ? `${esc(r.universe)} · página: ${esc(r.page_title)}<br>${esc(r.url)}${dup ? '<br><span class="dup">Já existe no registro: a ficha dele será substituída.</span>' : ""}`
      : `<span class="err">${esc(r.error)}</span>`;
    return `<div class="wiki-row" data-i="${i}">
      <input type="checkbox" data-w="include" ${r.include ? "checked" : ""} ${r.found ? "" : "disabled"}>
      <div><input data-w="name" value="${esc(r.name)}" ${r.found ? "" : "disabled"}><div class="wiki-info">${info}</div></div>
      ${r.found ? `<textarea data-w="sheet">${esc(r.sheet)}</textarea>` : "<div></div>"}
    </div>`;
  }).join("");
  const chosen = res.filter(r => r.found && r.include).length;
  const head = running ? `(${res.length}/${S.wiki.total || "?"}, buscando…)` : `(${res.length} resultado(s))`;
  box.innerHTML = `<div class="wiki-box">
    <h3>Importação da Wiki <span class="muted">${head}</span></h3>
    <p class="muted">Revise nome e ficha. Os marcados entram no registro com o universo de origem e na lista de permitidos do universo.</p>
    ${rows || '<p class="muted">Aguardando o primeiro resultado…</p>'}
    <div class="tab-tools" style="margin-top:10px">
      <button class="primary" id="btn-wiki-apply" ${running || !chosen ? "disabled" : ""}>Adicionar ${chosen} ao registro</button>
      <button id="btn-wiki-discard" ${running ? "disabled" : ""}>Descartar</button>
    </div></div>`;
  $$(".wiki-row", box).forEach(row => {
    const r = res[parseInt(row.dataset.i, 10)];
    $$("[data-w]", row).forEach(el => {
      el.addEventListener("input", () => {
        if (el.dataset.w === "include") { r.include = el.checked; renderWikiReview(); }
        else r[el.dataset.w] = el.value;
      });
      if (el.dataset.w === "name") el.addEventListener("change", renderWikiReview);
    });
  });
  const apply = $("#btn-wiki-apply");
  if (apply) apply.onclick = applyWiki;
  const discard = $("#btn-wiki-discard");
  if (discard) discard.onclick = () => { S.wiki = null; renderWikiReview(); };
}

async function applyWiki() {
  const picked = S.wiki.results.filter(r => r.found && r.include && r.name.trim());
  for (const r of picked) {
    const uni = S.cast.universes.find(u => u.name === r.universe);
    let c = findCharacter(r.name);
    if (c) {
      c.sheet = r.sheet.trim();
      if (!c.universe && uni) c.universe = uni.id;
    } else {
      c = { name: r.name.trim(), role: "supporting", origin: "", age: "", universe: uni ? uni.id : "",
            notes: `Wiki: ${r.url}`, sheet: r.sheet.trim(), sheet_label: "" };
      S.cast.characters.push(c);
    }
    if (uni && !uni.allowed_characters.some(a => a.toLowerCase() === c.name.toLowerCase())) uni.allowed_characters.push(c.name);
  }
  S.castTab = "characters";
  S.castSel = S.cast.characters.length - 1;
  S.wiki = null;
  await saveCast();
  renderWikiReview();
}

// ── Premissa guiada ─────────────────────────────────────────

function premiseDirty() {
  if (S.panel !== "chapter") return false;
  const el = $("#premise-edit");
  return S.premiseFormDirty || el.value !== (el.dataset.saved ?? el.value);
}

function setPremiseMode(mode) {
  S.premiseMode = mode;
  $$("#premise-mode button").forEach(b => b.classList.toggle("active", b.dataset.mode === mode));
  $("#premise-form").classList.toggle("hidden", mode !== "form");
  $("#premise-edit").classList.toggle("hidden", mode !== "text");
}

async function switchPremiseMode(mode) {
  if (mode === S.premiseMode) return;
  if (premiseDirty()) await savePremise(true);
  if (mode === "form") await loadPremiseForm(S.current);
  setPremiseMode(mode);
}

async function loadPremiseForm(num) {
  let data;
  try { data = await api("GET", `/api/projects/${S.slug}/chapters/${num}/premise-form`); } catch (e) { return fail(e); }
  if (S.current !== num) return;
  S.premise = data;
  S.premiseFormDirty = false;
  $("#premise-check").innerHTML = "";
  renderPremiseForm();
}

function markPremiseDirty() {
  S.premiseFormDirty = true;
  $("#premise-dirty").textContent = "alterações não salvas";
}

function renderPremiseForm() {
  const d = S.premise;
  if (!d) return;
  const f = d.form;
  $("#premise-dirty").textContent = S.premiseFormDirty ? "alterações não salvas" : "";
  const plan = d.outline
    ? `<div class="plan-box"><b>Plano da história para este capítulo:</b> ${esc(d.outline.title)} — ${esc(d.outline.content)}${d.outline.extra ? ` <span class="muted">(${esc(d.outline.extra)})</span>` : ""}</div>`
    : "";
  const known = d.characters || [];
  const extra = f.characters.filter(n => !known.some(k => k.toLowerCase() === n.toLowerCase()));
  const chips = [...known, ...extra].map(n => {
    const on = f.characters.some(c => c.toLowerCase() === n.toLowerCase());
    return `<span class="chip toggle ${on ? "on" : ""}" data-char="${esc(n)}">${esc(n)}</span>`;
  }).join("");
  const total = f.scenes.reduce((a, s) => a + (parseInt(s.words, 10) || 0), 0);
  const scenes = f.scenes.map((s, i) => `
    <div class="scene-row" data-scene="${i}">
      <span class="scene-num">${i + 1}</span>
      <textarea data-sf="text" placeholder="Nome da cena. O que acontece, quem está lá, o que muda.">${esc(s.text)}</textarea>
      <div class="scene-side">
        <input data-sf="words" type="number" min="50" step="50" value="${s.words ?? ""}" placeholder="palavras">
        <button type="button" data-sup title="Subir">↑</button>
        <button type="button" data-sdel class="danger-ghost" title="Remover cena">✕</button>
      </div>
    </div>`).join("");
  $("#premise-form").innerHTML = `
    ${plan}
    <div class="form-grid">
      <label>Título<input data-pf="title" value="${esc(f.title)}"></label>
    </div>
    <label>Objetivo do capítulo<textarea data-pf="goal" class="pf-short" placeholder="O que este capítulo precisa conseguir, em uma frase.">${esc(f.goal)}</textarea></label>
    <label>Abertura: onde e como começa<textarea data-pf="opening" class="pf-short" placeholder="Continua de onde o capítulo anterior parou: mesmo lugar, mesma luz, mesma situação.">${esc(f.opening)}</textarea></label>
    <div class="pf-label">Personagens em cena <span class="muted">(clique para marcar; só as fichas deles vão reforçadas no fim de cada cena)</span></div>
    <div class="chips">${chips || '<span class="muted">Cadastre personagens em Personagens e universos.</span>'}<span class="chip add" data-char-add>+ outro</span></div>
    <div class="pf-label">Cenas <span class="muted">${total ? `${total} de ~${d.target_words} palavras` : `meta do capítulo: ~${d.target_words} palavras`}</span></div>
    <div id="scene-rows">${scenes || '<p class="muted">Nenhuma cena. Sem cenas, o modelo divide a premissa sozinho.</p>'}</div>
    <div class="tab-tools"><button type="button" id="btn-scene-add">+ Cena</button>
      ${f.scenes.length ? '<button type="button" id="btn-scene-split">Dividir a meta igualmente</button>' : ""}</div>
    <label>Gancho: como o capítulo termina<textarea data-pf="hook" class="pf-short">${esc(f.hook)}</textarea></label>
    <label>Precisa aparecer<textarea data-pf="must_include" class="pf-short" placeholder="ex.: uma linha [System] com o progresso da construção.">${esc(f.must_include)}</textarea></label>
    <label>Não pode aparecer<textarea data-pf="must_not" class="pf-short" placeholder="Personagens, lugares ou fatos que ainda não podem entrar.">${esc(f.must_not)}</textarea></label>`;

  const box = $("#premise-form");
  $$("[data-pf]", box).forEach(el => el.addEventListener("input", () => { f[el.dataset.pf] = el.value; markPremiseDirty(); }));
  $$("[data-char]", box).forEach(ch => {
    ch.onclick = () => {
      const n = ch.dataset.char;
      const i = f.characters.findIndex(c => c.toLowerCase() === n.toLowerCase());
      if (i >= 0) f.characters.splice(i, 1); else f.characters.push(n);
      markPremiseDirty();
      renderPremiseForm();
    };
  });
  $("[data-char-add]", box).onclick = async () => {
    const r = await openDialog({
      title: "Personagem em cena", body: '<label>Nome<input id="pc-name"></label><p class="muted">Sem ficha no registro, o modelo só recebe o nome.</p>',
      actions: [{ label: "Cancelar", value: null }, { label: "Adicionar", cls: "primary", value: "ok", handler: () => { S.pcName = $("#pc-name").value.trim(); } }],
      onOpen: () => $("#pc-name").focus(),
    });
    if (r === "ok" && S.pcName) { f.characters.push(S.pcName); markPremiseDirty(); renderPremiseForm(); }
  };
  $$(".scene-row", box).forEach(row => {
    const i = parseInt(row.dataset.scene, 10);
    $$("[data-sf]", row).forEach(el => el.addEventListener("input", () => {
      f.scenes[i][el.dataset.sf] = el.dataset.sf === "words" ? (parseInt(el.value, 10) || null) : el.value;
      markPremiseDirty();
      if (el.dataset.sf === "words") {
        const t = f.scenes.reduce((a, s) => a + (parseInt(s.words, 10) || 0), 0);
        $$(".pf-label .muted", box)[1].textContent = `${t} de ~${d.target_words} palavras`;
      }
    }));
    $("[data-sdel]", row).onclick = () => { f.scenes.splice(i, 1); markPremiseDirty(); renderPremiseForm(); };
    $("[data-sup]", row).onclick = () => {
      if (i === 0) return;
      [f.scenes[i - 1], f.scenes[i]] = [f.scenes[i], f.scenes[i - 1]];
      markPremiseDirty();
      renderPremiseForm();
    };
  });
  $("#btn-scene-add").onclick = () => { f.scenes.push({ text: "", words: null }); markPremiseDirty(); renderPremiseForm(); };
  const split = $("#btn-scene-split");
  if (split) split.onclick = () => {
    const each = Math.round(d.target_words / f.scenes.length / 50) * 50;
    f.scenes.forEach(s => { s.words = each; });
    markPremiseDirty();
    renderPremiseForm();
  };
  if (projectBusy()) $$("input, textarea, button", box).forEach(el => { el.disabled = true; });
}

async function savePremise(quiet = false) {
  try {
    let text;
    if (S.premiseMode === "form" && S.premise) {
      text = (await api("PUT", `/api/projects/${S.slug}/chapters/${S.current}/premise-form`, { form: S.premise.form })).text;
      $("#premise-edit").value = text;
    } else {
      text = $("#premise-edit").value;
      await api("PUT", `/api/projects/${S.slug}/chapters/${S.current}/premise`, { text });
    }
    $("#premise-edit").dataset.saved = text;
    S.premiseFormDirty = false;
    $("#premise-dirty").textContent = "";
    if (S.chapter) S.chapter.premise = text;
    if (!quiet) toast("Premissa salva.");
    refreshProject();
  } catch (e) { fail(e); throw e; }
}

async function suggestPremise() {
  const hasContent = S.premise && (S.premise.form.scenes.length || S.premise.form.goal);
  const r = await openDialog({
    title: "Sugerir premissa com IA",
    body: `<p class="muted">O modelo do polimento escreve a premissa a partir do Registro Akáshico, do plano da história, do fim do capítulo anterior e da memória. ${hasContent ? "<b>O formulário atual será substituído</b> (só vale depois de Salvar)." : ""}</p>
      <label>Suas ideias para este capítulo (opcional)<textarea id="sg-notes" class="editor small" placeholder="ex.: quero uma cena em que ele fala sozinho com o terminal."></textarea></label>`,
    actions: [{ label: "Cancelar", value: null }, { label: "Sugerir", cls: "primary", value: "ok", handler: () => { S.sgNotes = $("#sg-notes").value; } }],
    onOpen: () => $("#sg-notes").focus(),
  });
  if (r !== "ok") return;
  try { await api("POST", `/api/projects/${S.slug}/chapters/${S.current}/premise/suggest`, { notes: S.sgNotes || "" }); } catch (e) { fail(e); }
}

async function checkPremise() {
  let text = $("#premise-edit").value;
  if (S.premiseMode === "form" && S.premise) {
    if (S.premiseFormDirty) await savePremise(true);
    text = $("#premise-edit").value;
  }
  if (!text.trim()) { toast("A premissa está vazia.", "warn"); return; }
  $("#premise-check").innerHTML = '<div class="check-box muted">Conferindo…</div>';
  try { await api("POST", `/api/projects/${S.slug}/chapters/${S.current}/premise/check`, { text }); } catch (e) { fail(e); $("#premise-check").innerHTML = ""; }
}

function onPremiseEvent(e) {
  if (e.project !== S.slug || e.chapter !== S.current) {
    if (!e.replay) toast(`Resultado da premissa do capítulo ${String(e.chapter).padStart(2, "0")} pronto: abra o capítulo para ver.`, "ok", 7000);
    return;
  }
  if (e.type === "premise_result") {
    if (e.ok && S.premise) {
      S.premise.form = e.form;
      markPremiseDirty();
      setPremiseMode("form");
      renderPremiseForm();
      if (!e.replay) {
        const cut = (e.removed || []).length ? ` Tirei do elenco quem ainda não apareceu na história: ${e.removed.join(", ")}.` : "";
        toast(`Sugestão pronta. Revise e clique em Salvar premissa.${cut}`, "ok", 9000);
      }
    } else {
      $("#premise-edit").value = e.raw;
      setPremiseMode("text");
      if (!e.replay) toast("O modelo não seguiu o formato. A resposta foi para o modo Texto; revise antes de salvar.", "warn", 9000);
    }
  } else {
    const lines = e.notes.split("\n").filter(l => l.trim());
    $("#premise-check").innerHTML = e.ok
      ? '<div class="check-box ok">Conferida: nenhum problema encontrado.</div>'
      : `<div class="check-box"><b>Problemas encontrados:</b><ul>${lines.map(l => `<li>${esc(l.replace(/^[-*]\s*/, ""))}</li>`).join("")}</ul></div>`;
  }
}

async function addChapter() {
  try {
    const res = await api("POST", `/api/projects/${S.slug}/chapters`, { premise: "" });
    await refreshProject();
    await selectChapter(res.num, "premise");
    toast(`Capítulo ${String(res.num).padStart(2, "0")} criado. Preencha a premissa ou use ✨ Sugerir com IA.`, "ok", 7000);
  } catch (e) { fail(e); }
}

// ── Configurações ───────────────────────────────────────────

const PHASES = [["DRAFTING", "Rascunho"], ["REFINING", "Polimento"], ["SUMMARIZING", "Resumo e memória"]];

const SETTINGS_FIELDS = [
  ["Criatividade (temperatura)", [
    ["DRAFTING_TEMPERATURE", "Rascunho", 0.05], ["REFINING_TEMPERATURE", "Polimento", 0.05], ["SUMMARIZING_TEMPERATURE", "Resumo", 0.05],
  ]],
  ["Contexto do Ollama (tokens)", [
    ["DRAFTING_NUM_CTX", "Rascunho", 1024], ["REFINING_NUM_CTX", "Polimento", 1024], ["SUMMARIZING_NUM_CTX", "Resumo", 1024],
  ]],
  ["Camadas na GPU, só Ollama (vazio = automático)", [
    ["DRAFTING_NUM_GPU", "Rascunho", 1], ["REFINING_NUM_GPU", "Polimento", 1], ["SUMMARIZING_NUM_GPU", "Resumo", 1],
  ]],
  ["Capítulo", [
    ["CHAPTER_TARGET_WORDS", "Palavras por capítulo", 100], ["REQUEST_TIMEOUT", "Tempo máximo de espera (s)", 60],
  ]],
];

async function openSettings() {
  let data;
  try { data = await api("GET", "/api/settings"); } catch (e) { return fail(e); }
  const v = data.values;
  const P = data.providers;
  const cloud = Object.keys(P).filter(k => P[k].cloud);
  // Modelos conhecidos por provedor: sugestões, e depois o que cada teste de conexão devolver.
  const models = { ollama: [] };
  cloud.forEach(k => { models[k] = [...(P[k].suggested || [])]; });

  const profileOpts = Object.entries(data.profiles).map(([k, p]) =>
    `<option value="${k}" ${k === v.HARDWARE_PROFILE ? "selected" : ""}>${esc(p.label)}</option>`).join("");
  const providerOpts = sel => Object.entries(P).map(([k, p]) =>
    `<option value="${k}" ${k === sel ? "selected" : ""}>${esc(p.label)}</option>`).join("");
  const phaseRows = PHASES.map(([ph, name]) => `
    <label>${name} · provedor<select data-key="PROVIDER_${ph}" data-phase="${ph}">${providerOpts(v["PROVIDER_" + ph])}</select></label>
    <label>${name} · modelo<input data-key="MODEL_${ph}" list="dl-${ph}" value="${esc(v["MODEL_" + ph] ?? "")}"><datalist id="dl-${ph}"></datalist></label>
    <span></span>`).join("");
  const keyRows = cloud.map(k => {
    const c = data.credentials[k] || {};
    return `<div class="key-row" data-provider="${k}">
      <div class="key-name">${esc(P[k].label)}<br><span class="muted" data-key-status>${c.configured ? `chave salva ${esc(c.hint)}${c.source && c.source !== "arquivo" ? ` (${esc(c.source)})` : ""}` : "sem chave"}</span></div>
      <input type="password" autocomplete="off" placeholder="${c.configured ? "trocar a chave…" : "cole a chave de API"}" data-key-input>
      <button type="button" data-key-save>Salvar</button>
      <button type="button" data-key-test>Testar</button>
      ${c.configured ? '<button type="button" class="danger-ghost" data-key-del>Apagar</button>' : ""}
      <div class="muted key-msg" data-key-msg>Onde pegar: <code>${esc(P[k].key_url || "")}</code></div>
    </div>`;
  }).join("");
  const sections = SETTINGS_FIELDS.map(([title, fields]) => `<div class="settings-section"><h4>${title}</h4><div class="form-grid">` +
    fields.map(([key, label, step]) => `<label>${label}<input data-key="${key}" type="number" step="${step}" value="${v[key] ?? ""}"></label>`).join("") +
    "</div></div>").join("");

  await openDialog({
    title: "Configurações",
    wide: true,
    body: `<label>Perfil<select data-key="HARDWARE_PROFILE" id="st-profile">${profileOpts}</select></label>
      <p class="muted" id="st-profile-note"></p>
      <div class="settings-section"><h4>Modelos por fase</h4>
        <p class="muted">Cada fase pode usar o Ollama (no seu computador) ou um serviço na nuvem. Na nuvem o texto da história é enviado para a empresa do modelo e cada capítulo gasta créditos da sua conta.</p>
        <div class="phase-grid">${phaseRows}</div>
      </div>
      <div class="settings-section"><h4>Ollama</h4>
        <div class="form-grid">
          <label>Servidor<input data-key="OLLAMA_BASE_URL" value="${esc(v.OLLAMA_BASE_URL)}"></label>
          <div class="inline-row"><button type="button" id="st-test">Testar conexão</button><span class="muted" id="st-test-msg"></span></div>
        </div>
      </div>
      <div class="settings-section"><h4>Chaves de API (nuvem)</h4>
        <p class="muted">Ficam só neste computador, em <code>credenciais.json</code> na pasta de dados. A tela nunca mostra a chave inteira.</p>
        ${keyRows}
        <label>Endereço do serviço compatível com OpenAI<input data-key="OPENAI_COMPAT_BASE_URL" value="${esc(v.OPENAI_COMPAT_BASE_URL)}"></label>
      </div>
      ${sections}
      <label class="check"><input type="checkbox" data-key="CONSISTENCY_CHECK_ENABLED" ${v.CONSISTENCY_CHECK_ENABLED ? "checked" : ""}> Checagem de consistência depois de cada capítulo</label>
      <p class="muted">Pasta de dados: <code>${esc(data.data_dir)}</code><br>Log: <code>${esc(data.log_file)}</code></p>`,
    actions: [
      { label: "Cancelar", value: null },
      { label: "Salvar", cls: "primary", value: "ok", handler: async () => {
        const values = {};
        $$("#dlg [data-key]").forEach(el => { values[el.dataset.key] = el.type === "checkbox" ? el.checked : el.value; });
        await api("PUT", "/api/settings", { values });
        toast("Configurações salvas. Valem a partir da próxima geração.");
        pollStatus();
      } },
    ],
    onOpen: () => {
      const fillModels = ph => {
        const prov = $(`#dlg [data-key="PROVIDER_${ph}"]`).value;
        $(`#dl-${ph}`).innerHTML = (models[prov] || []).map(m => `<option value="${esc(m)}">`).join("");
      };
      const fillAll = () => PHASES.forEach(([ph]) => fillModels(ph));
      $$("#dlg [data-phase]").forEach(sel => { sel.onchange = () => fillModels(sel.dataset.phase); });

      const note = () => { const p = data.profiles[$("#st-profile").value]; $("#st-profile-note").textContent = p ? p.note : ""; };
      note();
      $("#st-profile").onchange = () => {
        const p = data.profiles[$("#st-profile").value];
        Object.entries(p.values).forEach(([k, val]) => { const el = $(`#dlg [data-key="${k}"]`); if (el) el.value = val; });
        note();
        fillAll();
      };

      const testOllama = async () => {
        const url = $('#dlg [data-key="OLLAMA_BASE_URL"]').value;
        $("#st-test-msg").textContent = "testando…";
        try {
          const r = await api("GET", `/api/ollama?base_url=${encodeURIComponent(url)}`);
          $("#st-test-msg").textContent = r.online ? `ok, ${r.models.length} modelo(s) instalado(s)${r.missing.length ? ` · faltam: ${r.missing.join(", ")}` : ""}` : "sem resposta";
          models.ollama = r.models;
          fillAll();
        } catch (e) { $("#st-test-msg").textContent = e.message; }
      };
      $("#st-test").onclick = testOllama;
      testOllama();

      $$("#dlg .key-row").forEach(row => {
        const prov = row.dataset.provider;
        const input = $("[data-key-input]", row);
        const msg = $("[data-key-msg]", row);
        const setStatus = creds => {
          const c = creds[prov];
          $("[data-key-status]", row).textContent = c.configured ? `chave salva ${c.hint}` : "sem chave";
        };
        $("[data-key-save]", row).onclick = async () => {
          if (!input.value.trim()) { msg.textContent = "Cole a chave primeiro."; return; }
          try {
            setStatus((await api("PUT", `/api/credentials/${prov}`, { key: input.value })).credentials);
            input.value = "";
            msg.textContent = "Chave salva.";
          } catch (e) { msg.textContent = e.message; }
        };
        $("[data-key-test]", row).onclick = async () => {
          msg.textContent = "testando…";
          try {
            if (prov === "openai_compat") {
              // O teste usa o endereço salvo: salva o que está no campo antes.
              await api("PUT", "/api/settings", { values: { OPENAI_COMPAT_BASE_URL: $('#dlg [data-key="OPENAI_COMPAT_BASE_URL"]').value } });
            }
            const r = await api("POST", `/api/providers/${prov}/models`, { key: input.value });
            models[prov] = r.models;
            fillAll();
            msg.textContent = `ok, ${r.models.length} modelo(s) disponível(is).`;
          } catch (e) { msg.textContent = e.message; }
        };
        const del = $("[data-key-del]", row);
        if (del) del.onclick = async () => {
          try { setStatus((await api("PUT", `/api/credentials/${prov}`, { key: "" })).credentials); del.remove(); msg.textContent = "Chave apagada."; }
          catch (e) { msg.textContent = e.message; }
        };
      });
      fillAll();
    },
  });
}

// ── Ligações ────────────────────────────────────────────────

function bind() {
  $("#btn-home").onclick = goHome;
  $("#btn-settings").onclick = openSettings;
  $("#btn-new-project").onclick = newProject;
  $("#btn-add-chapter").onclick = addChapter;
  $("#btn-generate").onclick = () => generate(null);
  $("#btn-cancel-job").onclick = async () => {
    $("#btn-cancel-job").disabled = true;
    try { await api("POST", "/api/jobs/cancel"); } catch (e) { fail(e); }
  };
  $$("#ch-tabs button").forEach(b => { b.onclick = () => switchTab(b.dataset.tab); });
  $$(".side-footer .link").forEach(b => { b.onclick = () => openPanel(b.dataset.panel); });
  $("#btn-edit-final").onclick = () => { switchTab("final"); setEditing(true); };
  $("#btn-cancel-final").onclick = () => setEditing(false);
  $("#btn-save-final").onclick = saveFinal;
  $("#btn-save-premise").onclick = () => savePremise().catch(() => {});
  $("#btn-suggest-premise").onclick = suggestPremise;
  $("#btn-check-premise").onclick = checkPremise;
  $$("#premise-mode button").forEach(b => { b.onclick = () => switchPremiseMode(b.dataset.mode); });
  $("#btn-redo").onclick = redoChapter;
  $("#btn-refresh-memory").onclick = refreshMemory;
  $("#btn-delete-chapter").onclick = deleteChapter;
  $("#btn-save-state").onclick = saveState;
  $("#btn-save-akashic").onclick = saveAkashic;
  $("#btn-export").onclick = exportBook;
  $("#btn-save-cast").onclick = saveCast;
  $("#btn-cast-add").onclick = addCastItem;
  $("#btn-wiki-import").onclick = () => openWikiDialog();
  $$("#cast-tabs button").forEach(b => { b.onclick = () => { S.castTab = b.dataset.ctab; S.castSel = 0; renderCast(); }; });
  document.addEventListener("keydown", ev => {
    if ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === "s") {
      if (S.editing) { ev.preventDefault(); saveFinal(); }
      else if (S.panel === "chapter" && S.tab === "premise") { ev.preventDefault(); savePremise().catch(() => {}); }
      else if (S.panel === "cast" && S.castDirty) { ev.preventDefault(); saveCast(); }
    }
  });
  window.addEventListener("beforeunload", ev => {
    if (S.editing || premiseDirty() || S.castDirty) { ev.preventDefault(); ev.returnValue = ""; }
  });
}

bind();
connectEvents();
pollStatus();
setInterval(pollStatus, 15000);
loadProjects();
