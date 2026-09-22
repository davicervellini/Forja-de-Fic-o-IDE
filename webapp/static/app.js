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
  liveText: "",       // texto ao vivo do capítulo em geração, mesmo com outra tela aberta
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
    el.classList.toggle("on", st.ollama);
    el.classList.toggle("off", !st.ollama);
    $(".txt", el).textContent = st.ollama ? `Ollama · ${st.models.drafting} / ${st.models.refining}` : "Ollama desligado";
    el.title = st.ollama ? "Ollama respondendo" : "O Ollama não respondeu. Abra o Ollama para gerar capítulos.";
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
   "#btn-save-premise", "#btn-refresh-memory", "#btn-save-state", "#btn-save-akashic"].forEach(sel => {
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
    case "error":
      // Cancelamento pedido pelo usuário já ganha aviso próprio no fim do trabalho.
      if (!quiet && !e.cancelled) toast(e.message, "error", 10000);
      break;
    case "job_end": {
      const was = S.job;
      setJob(null);
      showLiveTab(false);
      if (!quiet && e.job && e.job.status === "cancelled") toast("Geração cancelada.", "warn");
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
  if (!S.editing && !premiseDirty()) return true;
  const ok = await confirmDlg("Alterações não salvas", "<p>Há alterações não salvas neste capítulo. Descartar?</p>", "Descartar", true);
  if (ok) setEditing(false);
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
  ["chapter", "story", "akashic", "export", "empty"].forEach(p => $(`#panel-${p}`).classList.toggle("hidden", p !== name));
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

function premiseDirty() {
  const el = $("#premise-edit");
  return S.panel === "chapter" && el.value !== (el.dataset.saved ?? el.value);
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

async function savePremise() {
  try {
    const text = $("#premise-edit").value;
    await api("PUT", `/api/projects/${S.slug}/chapters/${S.current}/premise`, { text });
    $("#premise-edit").dataset.saved = text;
    toast("Premissa salva.");
  } catch (e) { fail(e); }
}

async function addChapter() {
  const r = await openDialog({
    title: `Novo capítulo (${String(S.project.next_chapter).padStart(2, "0")})`,
    body: `<p class="muted">Descreva o que acontece. Funciona melhor com cenas numeradas, por exemplo:<br>
      <code>Chapter 3: Title</code> / <code>Scenes:</code> / <code>1. ...</code> / <code>2. ...</code> / <code>Hook: ...</code><br>
      Sem cenas numeradas, o modelo divide a premissa em cenas sozinho.</p>
      <textarea class="editor" id="nc-premise" style="min-height:300px;font:14px/1.5 var(--mono)"></textarea>
      <label class="check" style="margin-top:10px"><input type="checkbox" id="nc-generate"> Gerar logo depois de criar</label>`,
    wide: true,
    actions: [
      { label: "Cancelar", value: null },
      { label: "Criar", cls: "primary", value: "ok", handler: async () => {
        const premise = $("#nc-premise").value.trim();
        if (!premise) { toast("Escreva a premissa.", "warn"); return false; }
        const res = await api("POST", `/api/projects/${S.slug}/chapters`, { premise });
        S.newChapter = { num: res.num, generate: $("#nc-generate").checked };
      } },
    ],
    onOpen: () => $("#nc-premise").focus(),
  });
  if (r !== "ok") return;
  await refreshProject();
  await selectChapter(S.newChapter.num, "premise");
  if (S.newChapter.generate) generate([S.newChapter.num]);
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
    if (premiseDirty()) await savePremise();
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
    onOpen: () => { $("#rd-premise").value = $("#premise-edit").value; },
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
  if (name === "story") {
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

// ── Configurações ───────────────────────────────────────────

const SETTINGS_FIELDS = [
  ["Modelos", [
    ["MODEL_DRAFTING", "Rascunho", "model"], ["MODEL_REFINING", "Polimento", "model"], ["MODEL_SUMMARIZING", "Resumo e memória", "model"],
  ]],
  ["Criatividade (temperatura)", [
    ["DRAFTING_TEMPERATURE", "Rascunho", "number", 0.05], ["REFINING_TEMPERATURE", "Polimento", "number", 0.05], ["SUMMARIZING_TEMPERATURE", "Resumo", "number", 0.05],
  ]],
  ["Contexto (tokens)", [
    ["DRAFTING_NUM_CTX", "Rascunho", "number", 1024], ["REFINING_NUM_CTX", "Polimento", "number", 1024], ["SUMMARIZING_NUM_CTX", "Resumo", "number", 1024],
  ]],
  ["Camadas na GPU (vazio = automático)", [
    ["DRAFTING_NUM_GPU", "Rascunho", "number", 1], ["REFINING_NUM_GPU", "Polimento", "number", 1], ["SUMMARIZING_NUM_GPU", "Resumo", "number", 1],
  ]],
  ["Capítulo", [
    ["CHAPTER_TARGET_WORDS", "Palavras por capítulo", "number", 100], ["REQUEST_TIMEOUT", "Tempo máximo de espera (s)", "number", 60],
  ]],
];

async function openSettings() {
  let data;
  try { data = await api("GET", "/api/settings"); } catch (e) { return fail(e); }
  const v = data.values;
  const profileOpts = Object.entries(data.profiles).map(([k, p]) => `<option value="${k}" ${k === v.HARDWARE_PROFILE ? "selected" : ""}>${esc(p.label)}</option>`).join("");
  const sections = SETTINGS_FIELDS.map(([title, fields]) => `<div class="settings-section"><h4>${title}</h4><div class="form-grid">` +
    fields.map(([key, label, kind, step]) => kind === "model"
      ? `<label>${label}<input data-key="${key}" list="dl-models" value="${esc(v[key] ?? "")}"></label>`
      : `<label>${label}<input data-key="${key}" type="number" step="${step}" value="${v[key] ?? ""}"></label>`).join("") +
    "</div></div>").join("");
  await openDialog({
    title: "Configurações",
    wide: true,
    body: `<div class="form-grid">
        <label>Servidor Ollama<input data-key="OLLAMA_BASE_URL" value="${esc(v.OLLAMA_BASE_URL)}"></label>
        <div class="inline-row"><button type="button" id="st-test">Testar conexão</button><span class="muted" id="st-test-msg"></span></div>
      </div>
      <label>Perfil de hardware<select data-key="HARDWARE_PROFILE" id="st-profile">${profileOpts}</select></label>
      <p class="muted" id="st-profile-note"></p>
      ${sections}
      <label class="check"><input type="checkbox" data-key="CONSISTENCY_CHECK_ENABLED" ${v.CONSISTENCY_CHECK_ENABLED ? "checked" : ""}> Checagem de consistência depois de cada capítulo</label>
      <datalist id="dl-models"></datalist>
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
      const note = () => { const p = data.profiles[$("#st-profile").value]; $("#st-profile-note").textContent = p ? p.note : ""; };
      note();
      $("#st-profile").onchange = () => {
        const p = data.profiles[$("#st-profile").value];
        Object.entries(p.values).forEach(([k, val]) => { const el = $(`#dlg [data-key="${k}"]`); if (el) el.value = val; });
        note();
      };
      const test = async () => {
        const url = $('#dlg [data-key="OLLAMA_BASE_URL"]').value;
        $("#st-test-msg").textContent = "testando…";
        try {
          const r = await api("GET", `/api/ollama?base_url=${encodeURIComponent(url)}`);
          $("#st-test-msg").textContent = r.online ? `ok, ${r.models.length} modelo(s) instalado(s)${r.missing.length ? ` · faltam: ${r.missing.join(", ")}` : ""}` : "sem resposta";
          $("#dl-models").innerHTML = r.models.map(m => `<option value="${esc(m)}">`).join("");
        } catch (e) { $("#st-test-msg").textContent = e.message; }
      };
      $("#st-test").onclick = test;
      test();
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
  $("#btn-save-premise").onclick = savePremise;
  $("#btn-redo").onclick = redoChapter;
  $("#btn-refresh-memory").onclick = refreshMemory;
  $("#btn-delete-chapter").onclick = deleteChapter;
  $("#btn-save-state").onclick = saveState;
  $("#btn-save-akashic").onclick = saveAkashic;
  $("#btn-export").onclick = exportBook;
  document.addEventListener("keydown", ev => {
    if ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === "s") {
      if (S.editing) { ev.preventDefault(); saveFinal(); }
      else if (S.panel === "chapter" && S.tab === "premise") { ev.preventDefault(); savePremise(); }
    }
  });
  window.addEventListener("beforeunload", ev => {
    if (S.editing || premiseDirty()) { ev.preventDefault(); ev.returnValue = ""; }
  });
}

bind();
connectEvents();
pollStatus();
setInterval(pollStatus, 15000);
loadProjects();
