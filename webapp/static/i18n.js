"use strict";
// Tradução da interface.
//
// A interface é escrita em português (pt-BR) e traduzida na tela: cada texto visível que
// for igual a uma chave do dicionário (static/i18n/<idioma>.json) é trocado pela tradução.
// Chaves com {x} valem como molde ("Capítulo {x} pronto." casa com "Capítulo 03 pronto.").
// Um observador traduz também o que aparecer depois (avisos, listas, diálogos, mensagens
// do servidor). Textos da história nunca são tocados: leitor, editores, campos de texto e
// qualquer elemento marcado com data-no-i18n ficam de fora.
//
// Para adicionar um idioma: copie static/i18n/en.json para <código>.json, traduza os valores
// e acrescente o código em pipeline/languages.py (UI_TRANSLATED).

const I18N = { lang: "pt-BR", exact: new Map(), patterns: [], observer: null };

const I18N_SKIP = ".reader, textarea, [contenteditable], [data-no-i18n], code, .chapter-list .name, .cast-name, #ch-title, .wiki-row input, script, style";

function i18nNormalize(s) {
  return s.replace(/\s+/g, " ").trim();
}

function i18nCompile(key, value) {
  const parts = key.split("{x}").map(p => p.replace(/[.*+?^$()|[\]\\]/g, "\\$&"));
  return { re: new RegExp("^" + parts.join("(.+?)") + "$"), value };
}

function tr(text) {
  if (I18N.lang === "pt-BR" || !text) return text;
  const norm = i18nNormalize(text);
  if (!norm) return text;
  const lead = text.match(/^\s*/)[0];
  const trail = text.match(/\s*$/)[0];
  const hit = I18N.exact.get(norm);
  if (hit !== undefined) return lead + hit + trail;
  const one = i18nOne(norm);
  if (one !== null) return lead + one + trail;
  // Textos montados com " · " (ex.: "Base · na história"): cada pedaço é traduzido sozinho.
  if (norm.includes(" · ")) {
    const parts = norm.split(" · ").map(p => I18N.exact.get(p) ?? i18nOne(p) ?? p);
    const joined = parts.join(" · ");
    if (joined !== norm) return lead + joined + trail;
  }
  return text;
}

function i18nOne(norm) {
  for (const p of I18N.patterns) {
    const m = norm.match(p.re);
    if (m) {
      let i = 1;
      return p.value.replace(/\{x\}/g, () => m[i++] ?? "");
    }
  }
  return null;
}

function i18nSkip(el) {
  return !el || (el.closest && el.closest(I18N_SKIP));
}

// Atributos (placeholder, title) são traduzidos também em campos de texto; só o conteúdo deles fica de fora.
const I18N_SKIP_ATTRS = ".reader, [data-no-i18n], script, style";

function i18nNode(node) {
  if (node.nodeType === Node.TEXT_NODE) {
    if (i18nSkip(node.parentElement)) return;
    const out = tr(node.nodeValue);
    if (out !== node.nodeValue) node.nodeValue = out;
    return;
  }
  if (node.nodeType !== Node.ELEMENT_NODE) return;
  if (node.closest(I18N_SKIP_ATTRS)) return;
  i18nAttrs(node);
  if (i18nSkip(node)) {
    node.querySelectorAll("[placeholder], [title]").forEach(i18nAttrs);
    return;
  }
  node.childNodes.forEach(i18nNode);
}

function i18nAttrs(node) {
  for (const attr of ["placeholder", "title", "aria-label"]) {
    const v = node.getAttribute(attr);
    if (v) {
      const out = tr(v);
      if (out !== v) node.setAttribute(attr, out);
    }
  }
}

async function initI18n(lang) {
  I18N.lang = lang || "pt-BR";
  document.documentElement.lang = I18N.lang;
  if (I18N.lang === "pt-BR") return;
  try {
    const res = await fetch(`i18n/${I18N.lang}.json`);
    if (!res.ok) throw new Error(`i18n ${res.status}`);
    const dict = await res.json();
    for (const [k, v] of Object.entries(dict)) {
      if (k.startsWith("_")) continue;
      if (k.includes("{x}")) I18N.patterns.push(i18nCompile(i18nNormalize(k), v));
      else I18N.exact.set(i18nNormalize(k), v);
    }
    // Moldes mais longos primeiro: o mais específico ganha.
    I18N.patterns.sort((a, b) => b.re.source.length - a.re.source.length);
  } catch (e) {
    console.error("Tradução da interface indisponível:", e);
    I18N.lang = "pt-BR";
    return;
  }
  document.title = tr(document.title);
  i18nNode(document.body);
  I18N.observer = new MutationObserver(muts => {
    for (const m of muts) {
      if (m.type === "characterData") i18nNode(m.target);
      else m.addedNodes.forEach(i18nNode);
      if (m.type === "attributes" && m.target.nodeType === Node.ELEMENT_NODE && !m.target.closest(I18N_SKIP_ATTRS)) {
        const v = m.target.getAttribute(m.attributeName);
        const out = v && tr(v);
        if (out && out !== v) m.target.setAttribute(m.attributeName, out);
      }
    }
  });
  I18N.observer.observe(document.body, {
    childList: true, subtree: true, characterData: true, attributes: true,
    attributeFilter: ["placeholder", "title", "aria-label"],
  });
}
