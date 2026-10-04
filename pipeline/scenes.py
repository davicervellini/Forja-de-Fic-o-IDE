"""
scenes.py — Divide a premissa em cenas e monta o capítulo a partir delas.

Modelos pequenos (7B a 12B) não escrevem 2.000 palavras de uma vez: resumem todas as
cenas em 600 palavras e param. O pipeline por isso gera e pole uma cena por vez. Este
módulo tem só funções puras, sem chamadas ao modelo, para poder ser testado sozinho.

Formatos de premissa aceitos (inglês ou português), por exemplo:

    Chapter Premise: Chapter 2: The Empty City
    Scenes:
    1. The Dark Corridors. Arthur wanders ...
    2. The Genetic Key. ...
    Hook: ...

    SCENE 1 (about 350 words) - Tuesday morning.
    texto da cena em várias linhas ...
    SCENE 2 (about 900 words) - The white waiting room.
    ...
"""

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

SCENE_BREAK = "* * *"


@dataclass
class Scene:
    num: int
    text: str
    target_words: int | None = None


@dataclass
class PremisePlan:
    title: str | None = None
    hook: str = ""
    scenes: list[Scene] = field(default_factory=list)


_HEADER = re.compile(r"^\s*(?:scenes|cenas)\b", re.I)
_SCENE_WORD = re.compile(r"^\s*(?:[-*]\s*)?(?:scene|cena)\s+(\d{1,2})\b\s*[\.\):\-–—]?\s*(.*)$", re.I)
_SCENE_NUM = re.compile(r"^\s*(?:[-*]\s*)?(\d{1,2})\s*[\.\):\-–—]\s+(.*)$")
_SECTION_END = re.compile(
    r"^\s*(?:hook|gancho|termina com|ends? with|ending|must|precisa|n[ãa]o pode|tamanho|target|"
    r"length|goal|objetivo|personagens em cena|characters in scene|universos em cena|regras em foco|"
    r"forbidden|do not|n[ãa]o escrever)\b",
    re.I,
)
_HOOK = re.compile(r"^\s*(?:hook|gancho|termina com|ends? with)\s*:\s*(.+)$", re.I)
_TITLE = re.compile(r"(?:chapter|cap[ií]tulo|chapitre|kapitel|capitolo)\s+(\d+)\s*:\s*([^\n]+)", re.I)
# "about 600 words", "~600 words" ou "(600 words)".
_WORDS = re.compile(r"(?:(?:about|around|~|cerca de|aprox\.?)\s*|\(\s*)(\d[\d.,]*)\s*(?:words|palavras)", re.I)
_BREAK_LINE = re.compile(r"^\s*(?:\*\s*){3}\s*$")
_LEADING_JUNK = re.compile(
    r"^\s*(?:#+\s.*|(?:chapter|cap[ií]tulo|chapitre|kapitel|capitolo)\s+\d+\b.*|(?:scene|cena)\s+\d+\b.*|\*\*[^*]{1,60}\*\*|(?:\*\s*){3})\s*$",
    re.I,
)


def count_words(text: str) -> int:
    return len((text or "").split())


def tail_words(text: str, n: int) -> str:
    """
    Fim do texto com pelo menos `n` palavras, em parágrafos inteiros e com as quebras de linha:
    o ritmo dos parágrafos e as linhas de terminal ([System], "> comando") são o melhor exemplo
    de formato que o modelo vê. Só o primeiro parágrafo pode vir cortado, se for enorme.
    """
    text = (text or "").strip()
    if count_words(text) <= n:
        return text
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    kept: list[str] = []
    total = 0
    for para in reversed(paras):
        if total >= n:
            break
        words = para.split()
        if total + len(words) > n * 1.5 and kept:
            # Parágrafo enorme: entra só o fim dele, a partir de um começo de frase.
            need = n - total
            if need <= 0:
                break
            cut = " ".join(words[-need:])
            m = re.search(r"(?<=[.!?…])\s+(?=\S)", cut)
            kept.append("… " + (cut[m.end():] if m and m.end() < len(cut) else cut))
            total += need
            break
        kept.append(para)
        total += len(words)
    out = "\n\n".join(reversed(kept))
    return out if out.startswith("…") or len(kept) == len(paras) else "… " + out


def parse_premise(premise: str) -> PremisePlan:
    """Extrai título, gancho e cenas numeradas. Menos de duas cenas: lista vazia."""
    plan = PremisePlan()
    text = premise or ""

    m = _TITLE.search(text)
    if m:
        plan.title = f"Chapter {int(m.group(1))}: {m.group(2).strip().rstrip('.')}"

    lines = text.splitlines()
    has_header = any(_HEADER.match(l) for l in lines)
    collecting = not has_header
    scenes: list[Scene] = []

    for line in lines:
        hook = _HOOK.match(line)
        if hook and not plan.hook:
            plan.hook = hook.group(1).strip()
        if has_header and not collecting:
            if _HEADER.match(line):
                collecting = True
            continue
        if not collecting:
            continue
        start = _SCENE_WORD.match(line) or _SCENE_NUM.match(line)
        if start and int(start.group(1)) == len(scenes) + 1:
            scenes.append(Scene(num=int(start.group(1)), text=start.group(2).strip()))
            continue
        if scenes and _SECTION_END.match(line):
            collecting = False
            continue
        if scenes and line.strip():
            scenes[-1].text = (scenes[-1].text + "\n" + line.strip()).strip()

    for s in scenes:
        w = _WORDS.search(s.text)
        if w:
            try:
                s.target_words = int(w.group(1).replace(",", "").replace(".", ""))
            except ValueError:
                s.target_words = None

    plan.scenes = scenes if len(scenes) >= 2 else []
    return plan


def clean_scene(text: str) -> str:
    """
    Tira título, cabeçalho de cena e separadores que o modelo às vezes põe no começo ou no fim,
    e as linhas em que ele comenta a cena ("This scene will continue…").
    """
    lines = (text or "").strip().splitlines()
    while lines and (not lines[0].strip() or _LEADING_JUNK.match(lines[0])):
        lines.pop(0)
    while lines and (not lines[-1].strip() or _BREAK_LINE.match(lines[-1])):
        lines.pop()
    return drop_meta_lines("\n".join(lines))


def assemble_chapter(title: str | None, scenes: list[str], scene_break: str = SCENE_BREAK) -> str:
    body = f"\n\n{scene_break}\n\n".join(s.strip() for s in scenes if s and s.strip())
    return f"{title}\n\n{body}" if title else body


def split_chapter(text: str) -> tuple[str | None, list[str]]:
    """Inverso de assemble_chapter: (título, cenas). Aceita capítulo sem título ou sem separadores."""
    lines = (text or "").strip().splitlines()
    title = None
    if lines and re.match(r"^\s*(?:chapter|cap[ií]tulo|chapitre|kapitel|capitolo)\s+\d+\b", lines[0], re.I):
        title = lines.pop(0).strip()
    scenes, buf = [], []
    for line in lines:
        if _BREAK_LINE.match(line):
            if "\n".join(buf).strip():
                scenes.append("\n".join(buf).strip())
            buf = []
        else:
            buf.append(line)
    if "\n".join(buf).strip():
        scenes.append("\n".join(buf).strip())
    return title, scenes


# ── Limpeza de repetição (modelos pequenos entram em laço) ────

_WORD = re.compile(r"[a-zà-ÿ0-9']+", re.I)
_SENTENCE_END = re.compile(r"[.!?…][\"'”’)\]]*\s*$")


def _words_set(text: str) -> set[str]:
    return {w.lower() for w in _WORD.findall(text)}


def similarity(a: str, b: str) -> float:
    """Semelhança entre dois trechos pelas palavras em comum (Jaccard, de 0 a 1)."""
    sa, sb = _words_set(a), _words_set(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def paragraphs(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"\n\s*\n", text or "") if p.strip()]


def _sentences(paragraph: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?…])\s+(?=[\"'“A-ZÀ-Ý])", paragraph) if s.strip()]


def remove_repetition(text: str, para_threshold: float = 0.6, min_words: int = 8) -> str:
    """
    Tira parágrafos quase iguais a um anterior da mesma cena e frases repetidas palavra
    por palavra. Mantém sempre a primeira ocorrência.
    """
    kept: list[str] = []
    seen_sentences: set[str] = set()
    for para in paragraphs(text):
        if count_words(para) >= min_words and any(similarity(para, k) >= para_threshold for k in kept):
            continue
        out = []
        for s in _sentences(para):
            key = " ".join(_WORD.findall(s.lower()))
            if count_words(s) >= 6 and key in seen_sentences:
                continue
            seen_sentences.add(key)
            out.append(s.strip())
        if out:
            kept.append(" ".join(out))
    return "\n\n".join(kept)


REPEAT_NGRAM = 8


def ngrams_of(text: str, n: int = REPEAT_NGRAM) -> set[tuple[str, ...]]:
    grams: set[tuple[str, ...]] = set()
    for para in paragraphs(text):
        for s in _sentences(para):
            w = [x.lower() for x in _WORD.findall(s)]
            grams |= {tuple(w[k:k + n]) for k in range(len(w) - n + 1)}
    return grams


def remove_repeated_sentences(text: str, seen: set[tuple[str, ...]], n: int = REPEAT_NGRAM) -> str:
    """
    Tira as frases que repetem uma sequência de `n` palavras já usada antes (no capítulo
    ou mais cedo nesta cena). Falas entre aspas ficam, porque personagens repetem de propósito.
    Atualiza `seen` com as frases mantidas.
    """
    out_paras = []
    for para in paragraphs(text):
        kept = []
        for s in _sentences(para):
            w = [x.lower() for x in _WORD.findall(s)]
            grams = {tuple(w[k:k + n]) for k in range(len(w) - n + 1)}
            if grams & seen and not s.lstrip().startswith(('"', "“")):
                continue
            seen |= grams
            kept.append(s.strip())
        if kept:
            out_paras.append(" ".join(kept))
    return "\n\n".join(out_paras)


def drop_overlap(new_scene: str, previous_text: str, threshold: float = 0.45, lookback: int = 4) -> str:
    """Tira do começo da cena nova os parágrafos que repetem o final do texto anterior."""
    prev = paragraphs(previous_text)[-lookback:]
    paras = paragraphs(new_scene)
    while paras and prev and count_words(paras[0]) >= 8 and any(similarity(paras[0], p) >= threshold for p in prev):
        paras.pop(0)
    return "\n\n".join(paras)


def trim_incomplete_ending(text: str) -> str:
    """Corta a frase final pela metade (texto interrompido pelo limite de tokens)."""
    text = (text or "").rstrip()
    if not text or _SENTENCE_END.search(text):
        return text
    cut = max(text.rfind(ch) for ch in ".!?…")
    if cut <= 0:
        return text
    end = cut + 1
    while end < len(text) and text[end] in "\"'”’)]":
        end += 1
    return text[:end].rstrip()


PARAGRAPH_MAX_WORDS = 140
PARAGRAPH_TARGET_WORDS = 80


def split_long_paragraphs(text: str, max_words: int = PARAGRAPH_MAX_WORDS,
                          target: int = PARAGRAPH_TARGET_WORDS) -> str:
    """
    Quebra parágrafos de narração muito longos em pedaços de ~`target` palavras, sempre no
    fim de uma frase. Web novel se lê no celular: blocos de 300 palavras cansam. Falas e
    linhas [System] ficam como estão.
    """
    out = []
    for para in paragraphs(text):
        if count_words(para) <= max_words or para.lstrip().startswith(("\"", "“", "[")):
            out.append(para)
            continue
        chunk: list[str] = []
        for sent in _sentences(para):
            chunk.append(sent.strip())
            if count_words(" ".join(chunk)) >= target:
                out.append(" ".join(chunk))
                chunk = []
        if chunk:
            # Sobra curta junta com o pedaço anterior para não deixar uma frase solta.
            if out and count_words(" ".join(chunk)) < target // 3:
                out[-1] = f"{out[-1]} {' '.join(chunk)}"
            else:
                out.append(" ".join(chunk))
    return "\n\n".join(out)


def last_sentence(text: str) -> str:
    paras = paragraphs(text)
    if not paras:
        return ""
    sents = _sentences(paras[-1])
    return sents[-1].strip() if sents else paras[-1]


def last_prose_sentence(text: str) -> str:
    """
    Última frase de narração: pula linhas [System] e "> comando" no fim do texto. Citar a linha
    do Sistema como "última frase" faz o modelo reimprimi-la no começo da cena seguinte.
    """
    paras = [p for p in paragraphs(text) if not re.match(r"^\s*(?:\[System\]|>)", p)]
    while paras:
        para = re.sub(r"[\"“'‘]?\[System\][^\n]*$", "", paras[-1]).strip()
        if para and not para.endswith(":"):
            sents = _sentences(para)
            return sents[-1].strip() if sents else para
        if para:
            # Frase que termina em ":" anuncia a linha do Sistema; a anterior é um ponto de partida melhor.
            sents = _sentences(para)
            return (sents[-2] if len(sents) > 1 else sents[-1] if sents else para).strip()
        paras.pop()
    return ""


def trim_next_scene_leak(text: str, next_brief: str, min_words: int, threshold: float = 0.2) -> str:
    """
    Corta do fim da cena os parágrafos que já contam a cena seguinte (parecidos com a descrição
    dela), desde que a cena não fique abaixo de `min_words`.
    """
    if not next_brief.strip():
        return text
    paras = paragraphs(text)
    cut = None
    for k in range(max(0, len(paras) - 3), len(paras)):
        if count_words(paras[k]) >= 12 and similarity(paras[k], next_brief) >= threshold:
            cut = k
            break
    if cut is None or count_words("\n\n".join(paras[:cut])) < min_words:
        return text
    return "\n\n".join(paras[:cut])


def trim_after_hook(text: str, hook_end: str, max_share: float = 0.25, threshold: float = 0.2) -> str:
    """
    Última cena: tira o comentário depois da imagem final do gancho (reflexão, resumo, "e então,
    nada"), se for curto. O capítulo seguinte começa exatamente nessa imagem.
    """
    if not hook_end.strip():
        return text
    paras = paragraphs(text)
    hit = None
    for k in range(len(paras) - 1, -1, -1):
        if similarity(paras[k], hook_end) >= threshold:
            hit = k
            break
    if hit is None or hit == len(paras) - 1:
        return text
    tail = count_words("\n\n".join(paras[hit + 1:]))
    if tail > count_words(text) * max_share:
        return text
    return "\n\n".join(paras[:hit + 1])


def system_spans(text: str) -> set[str]:
    """Mensagens [System] e comandos "> x" do texto, normalizados (aspas, espaços, ponto final)."""
    def norm(s: str) -> str:
        return re.sub(r"[\s\"'“”‘’.]+", " ", s).strip().casefold()
    spans = {norm(m) for m in re.findall(r"\[System\][^\n\"”’]*", text or "")}
    spans |= {norm(m) for m in re.findall(r"(?m)^\s*>\s*\S[^\n]*", text or "")}
    return {s for s in spans if s}


def lost_system_spans(polished: str, draft: str) -> list[str]:
    """Linhas [System] e comandos do rascunho que sumiram no polimento."""
    have = system_spans(polished)
    return sorted(s for s in system_spans(draft) if s not in have)


def fix_capitalized_names(polished: str, draft: str, names: list[str]) -> tuple[str, list[str]]:
    """
    Desfaz a maiúscula que o polimento pôs numa palavra comum para casar com um nome do registro
    ("the heart of the city" → "the Heart of the city"), quando o rascunho tinha a palavra em
    minúscula. Começo de frase fica como está. Retorna (texto, nomes corrigidos).
    """
    fixed = []
    for name in names:
        core = re.sub(r"^the\s+", "", name, flags=re.I)
        if not core or core.lower() == core or " " in core:
            continue
        if re.search(rf"(?<!\w){re.escape(core)}(?!\w)", draft):
            continue  # o rascunho já usava o nome
        if not re.search(rf"(?<!\w){re.escape(core.lower())}(?!\w)", draft):
            continue  # nem a palavra comum estava lá: é nome novo, e o guarda trata

        def lower(m: re.Match) -> str:
            before = polished[:m.start()].rstrip()
            if not before or before[-1] in ".!?…\"“":
                return m.group(0)
            return m.group(0).lower()
        new = re.sub(rf"(?<!\w){re.escape(core)}(?!\w)", lower, polished)
        if new != polished:
            polished = new
            fixed.append(name)
    return polished, fixed


def revert_paragraphs(polished: str, draft: str, bad, min_sim: float = 0.3) -> tuple[str, int]:
    """
    Troca pelo parágrafo correspondente do rascunho cada parágrafo do polimento em que `bad(p)` é
    verdade (nome proibido, gente inventada), e devolve ao texto os parágrafos do rascunho com
    [System] ou comando que sumiram. Retorna (texto, parágrafos trocados); -1 quando um parágrafo
    ruim não tem par no rascunho (aí a cena inteira volta ao rascunho).
    """
    dparas = paragraphs(draft)
    pparas = paragraphs(polished)
    changed = 0
    for k, p in enumerate(pparas):
        if not bad(p):
            continue
        best = max(dparas, key=lambda d: similarity(p, d), default="")
        if not best or similarity(p, best) < min_sim:
            return polished, -1
        pparas[k] = best
        changed += 1
    for span in lost_system_spans("\n\n".join(pparas), draft):
        j = next((j for j, d in enumerate(dparas) if span in system_spans(d)), None)
        if j is None:
            return polished, -1
        src = dparas[j]
        if re.match(r"^\s*(?:\[System\]|>)", src):
            # Linha própria: volta logo depois do parágrafo que a antecedia no rascunho.
            if j == 0:
                pparas.insert(0, src)
            else:
                k = max(range(len(pparas)), key=lambda i: similarity(pparas[i], dparas[j - 1]), default=None)
                if k is None or similarity(pparas[k], dparas[j - 1]) < min_sim:
                    return polished, -1
                pparas.insert(k + 1, src)
        else:
            k = max(range(len(pparas)), key=lambda i: similarity(pparas[i], src), default=None)
            if k is None or similarity(pparas[k], src) < min_sim:
                return polished, -1
            pparas[k] = src
        changed += 1
    return "\n\n".join(pparas), changed


_QUOTE = re.compile(r"[\"“]([^\"”]{8,160})[\"”]")


def voice_samples(texts: list[str], name: str, n: int = 4) -> list[str]:
    """
    Falas curtas e reais do personagem nos capítulos já escritos (do mais recente para o mais
    antigo): trechos entre aspas em parágrafos que citam o nome dele, ou em que ele é o único
    nome. Escolhidas para variar: tamanhos diferentes, sem repetir o começo.
    """
    first = (name or "").split()[0] if (name or "").split() else ""
    if not first:
        return []
    found: list[str] = []
    for text in texts:
        for para in paragraphs(text):
            if not re.search(rf"\b{re.escape(first)}\b", para):
                continue
            for m in _QUOTE.finditer(para):
                line = m.group(1).strip()
                words = count_words(line)
                if 3 <= words <= 22 and not line.startswith("[") and line not in found:
                    found.append(line)
    picked: list[str] = []
    for line in sorted(found, key=lambda l: (-len(set(_words_set(l))), l))[:n * 4]:
        if any(similarity(line, p) > 0.3 or line.split()[0] == p.split()[0] for p in picked):
            continue
        picked.append(line)
        if len(picked) >= n:
            break
    return picked


def drop_new_system_lines(edited: str, original: str) -> str:
    """Remove linhas [System] que o polimento inventou (as que não existiam no rascunho)."""
    # O rascunho às vezes traz a caixa no meio da frase; o polimento pode movê-la para linha própria.
    def norm(s: str) -> str:
        return re.sub(r"[\s\"'“”‘’.]+", " ", s).strip().casefold()
    allowed = {norm(m) for m in re.findall(r"\[System\][^\n\"”’]*", original)}
    kept = [l for l in edited.splitlines() if not (l.strip().startswith("[System]") and norm(l) not in allowed)]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()


# ── Desvios do polimento ─────────────────────────────────────
# Modelos de narração (os de roleplay principalmente) às vezes saem do texto: trazem para a cena
# gente do registro que não estava lá, trocam a prosa por formato de roteiro ("Nome: fala"),
# comentam a cena ("This scene will continue…") ou entram em laço. O polimento que faz isso é
# descartado e fica o rascunho da cena.

_META_LINE = re.compile(
    r"^\s*[\(\[]?\s*(?:this|the) scene (?:will|continues|ends|is)\b|"
    r"^\s*[\(\[]?\s*(?:end of (?:the )?(?:scene|chapter)|to be continued|in the next (?:scene|chapter)|"
    r"\(?\s*(?:author'?s? )?note\s*:)",
    re.I,
)
_SCRIPT_LINE = re.compile(r"^\s*[A-ZÀ-Ý][\w'’.\-]*(?: [A-ZÀ-Ý][\w'’.\-]*){0,3}\s*:\s*\S")
_CAPITALIZED = re.compile(r"(?<![.!?…\"“'‘:\n])\s([A-ZÀ-Ý][a-zà-ÿ'’]+(?:[-][A-ZÀ-Ý][a-zà-ÿ]+)?)\b")


def drop_meta_lines(text: str) -> str:
    """Tira as linhas em que o modelo comenta a cena em vez de escrevê-la."""
    kept = [l for l in (text or "").splitlines() if not _META_LINE.match(l)]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()


def official_names(akashic_text: str) -> list[str]:
    """Nomes próprios da seção "Official spellings" do registro (pessoas, lugares, termos com maiúscula)."""
    m = re.search(r"^#+\s*[\d.]*\s*Official spellings.*?$(.*?)(?=^#|\Z)", akashic_text or "", re.M | re.S | re.I)
    if not m:
        return []
    names: list[str] = []
    for line in m.group(1).splitlines():
        if ":" not in line:
            continue
        for item in line.split(":", 1)[1].split(";"):
            item = item.strip().rstrip(".")
            if item and not item.startswith("[") and re.search(r"[A-ZÀ-Ý]", item) and item not in names:
                names.append(item)
    return names


def _name_pattern(name: str) -> re.Pattern:
    if name.lower().startswith("the "):
        return re.compile(r"\b[Tt]he " + re.escape(name[4:]) + r"\b")
    return re.compile(r"\b" + re.escape(name) + r"\b")


def new_registry_names(polished: str, allowed: str, names: list[str]) -> list[str]:
    """Nomes do registro que aparecem no polimento e não estão no rascunho nem na premissa."""
    return [n for n in names if _name_pattern(n).search(polished) and not _name_pattern(n).search(allowed)]


def new_proper_nouns(polished: str, allowed: str) -> list[str]:
    """Palavras com maiúscula no meio da frase que o texto de origem não tem (gente inventada)."""
    known = {re.sub(r"['’]s$", "", w) for w in _words_set(allowed)}
    found: list[str] = []
    for w in _CAPITALIZED.findall(polished or ""):
        w = re.sub(r"['’]s$", "", w)
        if "'" in w or "’" in w:  # contrações: I'm, Don't
            continue
        if w.lower() not in known and w not in found:
            found.append(w)
    return found


_ANY_CAPITALIZED = re.compile(r"\b[A-ZÀ-Ý][a-zà-ÿ'’]*\b")


def garbled_names(text: str, names: list[str], source: str = "") -> list[tuple[str, str]]:
    """
    Palavras do texto parecidas com um nome oficial do elenco, mas não exatamente iguais a ele nem
    a nenhuma palavra do `source` (rascunho) — o polimento, de vez em quando, troca uma letra do
    nome de um personagem ("Alexei" → "Alexelli"). Olha qualquer posição na frase, inclusive o
    começo, onde `new_proper_nouns` não procura (lá uma maiúscula de início de frase é normal).
    Retorna [(palavra encontrada, nome oficial mais parecido)].
    """
    if not names:
        return []
    known_words = _words_set(source) if source else set()
    found: list[tuple[str, str]] = []
    seen: set[str] = set()
    for w in _ANY_CAPITALIZED.findall(text or ""):
        core = re.sub(r"['’]s$", "", w)
        if len(core) < 4 or core.lower() in seen or core.lower() in known_words:
            continue
        seen.add(core.lower())
        if any(core == n or core.lower() == n.lower() for n in names):
            continue  # é o próprio nome oficial
        best = max(names, key=lambda n: SequenceMatcher(None, core.lower(), n.lower()).ratio())
        ratio = SequenceMatcher(None, core.lower(), best.lower()).ratio()
        # Perto o bastante do nome oficial para ser o mesmo nome desfigurado, mas não uma palavra à toa.
        if ratio >= 0.8 and core.lower()[:2] == best.lower()[:2]:
            found.append((core, best))
    return found


def refine_problems(polished: str, draft: str, premise: str = "", names: list[str] | None = None,
                    max_ratio: float = 1.25, forbidden: list[str] | None = None) -> list[str]:
    """
    Motivos para descartar o polimento de uma cena; lista vazia quando está tudo certo.
    `premise`: o que a premissa permite (sem o "não pode aparecer"). `forbidden`: nomes que a
    premissa proíbe; o polimento que os traz é recusado mesmo que estejam no texto da premissa.
    """
    problems: list[str] = []
    allowed = f"{draft}\n{premise}"
    before, after = count_words(draft), count_words(polished)
    if before and after > before * max_ratio:
        problems.append(f"cresceu demais ({before} → {after} palavras)")
    banned = [n for n in (forbidden or []) if _name_pattern(n).search(polished) and not _name_pattern(n).search(draft)]
    if banned:
        problems.append("trouxe o que a premissa proíbe: " + ", ".join(banned))
    invented = [n for n in new_registry_names(polished, allowed, names or []) if n not in banned]
    if invented:
        problems.append("trouxe do registro quem não estava na cena: " + ", ".join(invented))
    lost_lines = lost_system_spans(polished, draft)
    if lost_lines:
        problems.append(f"perdeu {len(lost_lines)} linha(s) [System] ou comando")
    garbled = garbled_names(polished, names or [], allowed)
    if garbled:
        problems.append("inventou nome parecido com um oficial: "
                        + ", ".join(f'{w} (≈ {official})' for w, official in garbled[:4]))
    strangers = new_proper_nouns(polished, allowed)
    if len(strangers) >= 2:
        problems.append("inventou nomes: " + ", ".join(strangers[:6]))
    script = sum(bool(_SCRIPT_LINE.match(l)) and not l.lstrip().startswith("[") for l in polished.splitlines())
    if script > sum(bool(_SCRIPT_LINE.match(l)) for l in draft.splitlines()) + 1:
        problems.append(f"virou roteiro ({script} linhas \"Nome: fala\")")
    if any(_META_LINE.match(l) for l in polished.splitlines()):
        problems.append("comentou a cena em vez de escrevê-la")
    lost = after - count_words(remove_repetition(polished, min_words=3))
    if after and lost > after * 0.1:
        problems.append(f"entrou em laço ({lost} palavras repetidas)")
    return problems
