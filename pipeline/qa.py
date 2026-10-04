"""
qa.py — Conferência do texto gerado: o que a premissa pede, o que ela proíbe e os desvios típicos
de modelos pequenos.

Duas camadas:
1. Checagens sem modelo (rápidas e exatas): nomes proibidos ou que ainda não estrearam, conversa
   de assistente ("Please provide the draft…"), formato de roteiro, comentário sobre a cena, laço,
   idioma errado, palavras da premissa em português vazando para a história, linhas [System]
   além da conta.
2. Juiz (modelo, temperatura 0, resposta em JSON): itens da premissa que faltam e violações. Toda
   violação precisa de uma citação exata da cena; a que não aparece no texto é descartada, o que
   elimina a classe de falsos positivos dos juízes pequenos.

Também ficam aqui a lista de itens da premissa distribuída pelas cenas (checklist), a nota usada
para escolher entre versões de uma cena e a lista de trechos que o polimento deve reescrever.
"""

import json
import re
from dataclasses import asdict, dataclass, field

from pipeline.canon import mentions, name_pattern
from pipeline.languages import wrong_language
from pipeline.scenes import (
    _SCRIPT_LINE,
    _META_LINE,
    _sentences,
    count_words,
    paragraphs,
    remove_repetition,
    similarity,
)

# ── Itens da premissa ─────────────────────────────────────────

_STOP = {
    # inglês
    "the", "and", "with", "that", "this", "from", "into", "onto", "they", "them", "their", "there", "where",
    "when", "what", "which", "while", "have", "has", "had", "been", "were", "will", "would", "could", "should",
    "about", "after", "before", "only", "just", "more", "most", "some", "than", "then", "each", "every", "over",
    "under", "your", "his", "her", "him", "its", "it's", "does", "doesn't", "never", "scene", "chapter", "words",
    # português
    "para", "com", "uma", "como", "mais", "pela", "pelo", "dele", "dela", "isso", "esse", "essa", "este", "esta",
    "ainda", "quando", "onde", "sobre", "depois", "antes", "cena", "capítulo", "palavras", "sem", "não", "que",
}
_NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "um": 1, "uma": 1, "dois": 2, "duas": 2,
                 "três": 3, "tres": 3, "quatro": 4, "cinco": 5}
_SYSTEM_CAP = re.compile(r"(?:at most|no more than|up to|no máximo|máximo de|até)\s+(\w+)\s+(?:\w+\s+){0,2}\[System\]", re.I)


def split_items(text: str) -> list[str]:
    """Itens de "Must include"/"Must not appear": separados por ';' ou fim de frase, fora de parênteses."""
    items, buf, depth = [], [], 0
    text = (text or "").strip()
    for i, ch in enumerate(text):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        end = depth == 0 and (ch == ";" or (ch in ".!?" and (i + 1 == len(text) or text[i + 1] == " ")))
        buf.append(ch)
        if end:
            items.append("".join(buf).strip(" ;"))
            buf = []
    if "".join(buf).strip():
        items.append("".join(buf).strip(" ;"))
    return [i.rstrip(".").strip() for i in items if len(i.strip(" .;")) > 2]


def keywords(text: str) -> list[str]:
    """Palavras de conteúdo de um item (raiz de até 5 letras), para conferir se ele está no texto."""
    words = re.findall(r"[a-zà-ÿ0-9']+", (text or "").lower())
    return list(dict.fromkeys(w[:5] for w in words if len(w) >= 4 and w not in _STOP))


def coverage(item: str, text: str) -> float:
    """Fração das palavras de conteúdo do item que aparecem no texto (pela raiz)."""
    keys = keywords(item)
    if not keys:
        return 1.0
    stems = {w[:5] for w in re.findall(r"[a-zà-ÿ0-9']+", (text or "").lower()) if len(w) >= 4}
    return sum(k in stems for k in keys) / len(keys)


@dataclass
class Checklist:
    items: list[str] = field(default_factory=list)          # "Must include", um item por linha
    scene_of: list[int] = field(default_factory=list)       # cena de cada item (0 = capítulo inteiro)
    forbidden: list[str] = field(default_factory=list)      # "Must not appear", um item por linha
    forbidden_names: list[str] = field(default_factory=list)  # nomes proibidos ou que ainda não estrearam
    max_system_lines: int = 3

    def for_scene(self, num: int) -> list[tuple[int, str]]:
        return [(i, it) for i, (it, s) in enumerate(zip(self.items, self.scene_of)) if s == num]

    def before_scene(self, num: int) -> list[str]:
        return [it for it, s in zip(self.items, self.scene_of) if 0 < s < num]

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Checklist":
        return cls(**{k: v for k, v in (d or {}).items() if k in cls.__dataclass_fields__})


def build_checklist(must_include: str, must_not: str, scene_texts: list[str], names: list[str],
                    hidden: list[str], cast: list[str]) -> Checklist:
    """
    Distribui os itens obrigatórios pelas cenas (cada item vai para a cena cuja descrição mais se
    parece com ele) e junta os nomes proibidos: os do registro que estão no "não pode aparecer"
    e os personagens que ainda não estrearam, menos quem está no elenco da premissa.
    """
    items = split_items(must_include)
    scene_of = []
    for it in items:
        scores = [similarity(it, s) for s in scene_texts]
        best = max(range(len(scores)), key=lambda k: scores[k]) if scores else -1
        scene_of.append(best + 1 if best >= 0 and scores[best] >= 0.04 else 0)
    def in_cast(name: str) -> bool:
        # "Kargen" é o Kargen Ironbreaker do elenco: o primeiro nome também conta.
        return any(name.lower() == c.lower() or mentions(name, c) or mentions(c, name) for c in cast)

    hard = [n for n in names if mentions(n, must_not) and not in_cast(n)]
    hard += [h for h in hidden if not in_cast(h) and h not in hard]
    cap = 3
    m = _SYSTEM_CAP.search(f"{must_include}\n{must_not}")
    if m:
        word = m.group(1).lower()
        cap = int(word) if word.isdigit() else _NUMBER_WORDS.get(word, cap)
    return Checklist(items=items, scene_of=scene_of, forbidden=split_items(must_not),
                     forbidden_names=hard, max_system_lines=cap)


# ── Checagens sem modelo ──────────────────────────────────────

_CHATTER = re.compile(
    r"\b(?:please provide|as an ai\b|as a language model|here is the (?:scene|chapter|text|edited|revised|draft)|"
    r"here's the (?:scene|chapter|text|edited|revised)|i cannot (?:continue|write|help)|let me know if|i hope this|"
    r"feel free to|i've (?:edited|revised|polished)|i have (?:edited|revised|polished))",
    re.I,
)
SYSTEM_LINE = re.compile(r"^\s*\[System\]", re.M)

# Frases gastas de modelo pequeno (além das que o rascunho já evita): o polimento reescreve.
CLICHES = [
    "a sense of", "eyes widened", "heart skipped a beat", "heart beat faster", "a mix of", "shiver ran down",
    "shiver run down", "felt a surge", "little did he know", "couldn't help but", "sent shivers", "his mind raced",
    "her mind raced", "newfound", "eerie glow", "eerie silence", "breath he didn't know", "a testament to",
    "the weight of the world", "palpable", "deafening silence", "time seemed to", "for what felt like",
    "in that moment", "unbeknownst",
]


@dataclass
class Issue:
    kind: str
    text: str
    hard: bool = True
    quote: str = ""

    def __str__(self) -> str:
        return f"{self.kind}: {self.text}" + (f' ("{self.quote}")' if self.quote else "")


def system_lines(text: str) -> int:
    return len(SYSTEM_LINE.findall(text or ""))


def scene_issues(text: str, allowed_source: str, checklist: Checklist, story_code: str = "en",
                 leak_terms: list[str] | None = None, system_left: int | None = None) -> list[Issue]:
    """
    Problemas de uma cena que dá para achar sem modelo. `allowed_source`: texto em que os nomes
    já valiam (rascunho, quando se confere um polimento). `leak_terms`: termos da premissa em outro
    idioma que não podem vazar para a história. `system_left`: linhas [System] que ainda cabem.
    """
    issues: list[Issue] = []
    for n in checklist.forbidden_names:
        if mentions(n, text) and not mentions(n, allowed_source):
            issues.append(Issue("proibido", f"{n} não pode aparecer neste capítulo"))
    for line in (text or "").splitlines():
        m = _CHATTER.search(line)
        if m:
            issues.append(Issue("conversa de assistente", "o modelo falou com o usuário", quote=line.strip()[:120]))
            break
    if any(_META_LINE.match(l) for l in (text or "").splitlines()):
        issues.append(Issue("comentário", "o modelo comentou a cena em vez de escrevê-la"))
    script = sum(bool(_SCRIPT_LINE.match(l)) and not l.lstrip().startswith(("[", ">")) for l in (text or "").splitlines())
    if script >= 3:
        issues.append(Issue("roteiro", f"{script} linhas no formato \"Nome: fala\""))
    words = count_words(text)
    lost = words - count_words(remove_repetition(text or "", min_words=3))
    if words and lost > words * 0.1:
        issues.append(Issue("laço", f"{lost} palavras repetidas"))
    wrong = [p for p in paragraphs(text) if count_words(p) >= 25 and wrong_language(p, story_code)]
    if wrong:
        issues.append(Issue("idioma", f"{len(wrong)} parágrafo(s) fora do idioma da história",
                            quote=" ".join(wrong[0].split()[:12])))
    for term in leak_terms or []:
        m = re.search(rf"(?<!\w){re.escape(term)}(?!\w)", text or "", re.I)
        if m and not re.search(rf"(?<!\w){re.escape(term)}(?!\w)", allowed_source or "", re.I):
            issues.append(Issue("idioma", f"palavra da premissa sem traduzir: {term}"))
            break
    if system_left is not None:
        extra = system_lines(text) - max(system_left, 0)
        if extra > 0:
            issues.append(Issue("[System]", f"{extra} linha(s) [System] além do limite do capítulo", hard=extra >= 2))
    return issues


def leak_terms(glossary: list[tuple[str, str]], story_code: str) -> list[str]:
    """Termos em português do glossário que denunciam vazamento (só quando a história não é em português)."""
    if story_code.startswith("pt"):
        return []
    out = []
    for pt, en in glossary:
        core = re.sub(r"^(?:a|o|as|os)\s+", "", pt, flags=re.I).strip()
        # Nome próprio igual nos dois idiomas não denuncia nada.
        if len(core) >= 4 and core.lower() not in en.lower():
            out.append(core)
    return out


def missing_items(text: str, items: list[tuple[int, str]], threshold: float = 0.5) -> list[int]:
    """Itens (índice no checklist) cujas palavras de conteúdo não aparecem no texto."""
    return [i for i, it in items if coverage(it, text) < threshold]


# ── Nota de uma versão ────────────────────────────────────────

def cliche_count(text: str) -> int:
    low = (text or "").lower()
    return sum(low.count(c) for c in CLICHES)


def echo_count(text: str, seen: set) -> int:
    """Frases que repetem 8 palavras seguidas de algo já escrito (laço ou recomeço)."""
    n = 0
    for para in paragraphs(text):
        for s in _sentences(para):
            w = [x.lower() for x in re.findall(r"[a-zà-ÿ0-9']+", s, re.I)]
            if any(tuple(w[k:k + 8]) in seen for k in range(len(w) - 7)):
                n += 1
    return n


def score(text: str, target: int, items: list[tuple[int, str]], issues: list[Issue], seen: set | None = None) -> float:
    """Nota da versão: itens cobertos, frases gastas, ecos, repetição, distância da meta e problemas graves."""
    words = count_words(text)
    covered = len(items) - len(missing_items(text, items))
    repeated = words - count_words(remove_repetition(text or "", min_words=3))
    return (3 * covered - 2 * cliche_count(text) - echo_count(text, seen or set()) - repeated / 50
            - 5 * abs(words / max(target, 1) - 1) - 100 * sum(i.hard for i in issues)
            - 3 * sum(not i.hard for i in issues))


# ── Respostas do juiz ─────────────────────────────────────────

def _norm_quote(s: str) -> str:
    s = re.sub(r"[“”\"]", '"', s or "")
    s = re.sub(r"[‘’']", "'", s)
    return re.sub(r"\s+", " ", s).strip(" .…\"'").lower()


def quote_in(quote: str, text: str) -> bool:
    """A citação está no texto (ignorando aspas tipográficas, espaços e caixa)? Citação curta não vale."""
    q = _norm_quote(quote)
    return len(q.split()) >= 2 and q in _norm_quote(text)


def parse_json(raw: str) -> dict:
    """Primeiro objeto JSON da resposta (o modelo às vezes põe texto ou ``` em volta)."""
    raw = (raw or "").strip()
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except ValueError:
        pass
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        return {}
    try:
        data = json.loads(m.group(0))
        return data if isinstance(data, dict) else {}
    except ValueError:
        return {}


def parse_judge(raw: str, text: str, n_items: int) -> tuple[list[int], list[Issue]]:
    """
    (itens que faltam, violações com citação conferida). Ids fora da faixa e citações que não estão
    no texto são descartados.
    """
    data = parse_json(raw)
    missing = []
    for v in data.get("missing", []) or []:
        try:
            k = int(str(v).strip().lstrip("#")) - 1
        except ValueError:
            continue
        if 0 <= k < n_items and k not in missing:
            missing.append(k)
    issues = []
    for v in data.get("violations", []) or []:
        if not isinstance(v, dict):
            continue
        quote = str(v.get("quote", ""))
        if quote_in(quote, text):
            issues.append(Issue("juiz", str(v.get("rule") or v.get("problem") or "violação"), quote=quote.strip()))
    return missing, issues


def parse_edits(raw: str, text: str, max_edits: int = 6) -> list[dict]:
    """
    Correções da revisão: [{kind, quote, replacement, reason}] com citação conferida. A troca não
    pode ser muito maior que o trecho (a revisão corrige; não escreve cena nova).
    """
    data = parse_json(raw)
    out = []
    for e in (data.get("edits") or [])[:max_edits * 2]:
        if not isinstance(e, dict):
            continue
        quote = str(e.get("quote", "")).strip()
        repl = str(e.get("replacement", "")).strip()
        if not quote_in(quote, text):
            continue
        if count_words(repl) > count_words(quote) * 3 + 30:
            continue
        out.append({"kind": str(e.get("kind", "")).upper(), "quote": quote, "replacement": repl,
                    "reason": str(e.get("reason", ""))})
        if len(out) >= max_edits:
            break
    return out


def apply_edits(text: str, edits: list[dict]) -> tuple[str, list[dict]]:
    """Troca cada citação pelo texto novo (a primeira ocorrência). Retorna (texto, edições aplicadas)."""
    applied = []
    for e in edits:
        span = _find_span(text, e["quote"])
        if span is None:
            continue
        a, b = span
        text = text[:a] + e["replacement"] + text[b:]
        applied.append(e)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip(), applied


def _find_span(text: str, quote: str) -> tuple[int, int] | None:
    """Posição da citação no texto, tolerando aspas tipográficas e espaços diferentes."""
    if quote in text:
        a = text.index(quote)
        return a, a + len(quote)
    pattern = re.escape(quote.strip(" .…\"'“”‘’"))
    pattern = re.sub(r"(?:\\ )+", r"\\s+", pattern)
    pattern = re.sub(r"\\?[\"“”]", "[\"“”]", pattern)
    pattern = re.sub(r"\\?['‘’]", "['‘’]", pattern)
    m = re.search(pattern, text, re.I)
    return (m.start(), m.end()) if m else None


def parse_pairwise(raw: str) -> str:
    """'A', 'B' ou '' da resposta do juiz de pares."""
    w = str(parse_json(raw).get("winner", "")).strip().upper()[:1]
    return w if w in ("A", "B") else ""


# ── O que o polimento deve reescrever ────────────────────────

_TAG_TIC = re.compile(r'["“][^"”]{1,40}[,!?]["”]\s+(?:he|she|they|[A-Z]\w+)\s+(muttered|murmured|mumbled|grumbled|rasped|growled)\b')


def polish_targets(scene: str, earlier: str, exempt: list[str] | None = None, max_items: int = 8) -> list[tuple[str, str]]:
    """
    Trechos exatos da cena que o polimento deve reescrever, com o motivo: frases gastas, repetição
    de cenas anteriores (ou do fim do capítulo anterior) e dentro da cena, aspas sem fechar e o
    mesmo tique de fala repetido. Palavras de itens obrigatórios (`exempt`) só contam a partir da
    terceira vez, para o polimento não apagar o que a premissa pede.
    """
    out: list[tuple[str, str]] = []
    exempt_stems = {k for e in (exempt or []) for k in keywords(e)}
    earlier_grams = _grams(earlier, 5)
    seen_here: set = set()
    for para in paragraphs(scene):
        for s in _sentences(para):
            low = s.lower()
            words = [w.lower() for w in re.findall(r"[a-zà-ÿ0-9']+", s, re.I)]
            grams = {tuple(words[k:k + 5]) for k in range(len(words) - 4)}
            hit = next((c for c in CLICHES if c in low), None)
            if hit:
                out.append((s.strip(), f'frase gasta ("{hit}")'))
                continue
            rep = grams & earlier_grams
            if rep and not _exempt(rep, exempt_stems, scene):
                out.append((s.strip(), "repete uma cena anterior"))
                continue
            if grams & seen_here and not _exempt(grams & seen_here, exempt_stems, scene):
                out.append((s.strip(), "repete esta cena"))
            seen_here |= grams
    for para in paragraphs(scene):
        if para.count('"') % 2 or para.count("“") != para.count("”"):
            last = _sentences(para)[-1] if _sentences(para) else para
            out.append((last.strip(), "aspas abertas sem fechar"))
    tics = _TAG_TIC.findall(earlier)
    for m in _TAG_TIC.finditer(scene):
        if m.group(1) in tics:
            out.append((m.group(0).strip(), f'tique de fala repetido ("{m.group(1)}")'))
    uniq, seen = [], set()
    for q, why in out:
        if q not in seen and count_words(q) >= 3:
            seen.add(q)
            uniq.append((q, why))
    return uniq[:max_items]


def _grams(text: str, n: int) -> set:
    grams: set = set()
    for para in paragraphs(text):
        for s in _sentences(para):
            w = [x.lower() for x in re.findall(r"[a-zà-ÿ0-9']+", s, re.I)]
            grams |= {tuple(w[k:k + n]) for k in range(len(w) - n + 1)}
    return grams


def _exempt(grams: set, stems: set, scene: str) -> bool:
    """Repetição de um motivo que a premissa pede: só vale apontar a partir da terceira vez."""
    for g in grams:
        hit = [w for w in g if w[:5] in stems]
        if hit and (scene or "").lower().count(hit[0]) < 3:
            return True
    return False


# ── Nomes do registro na cena ────────────────────────────────

def names_in(text: str, names: list[str]) -> list[str]:
    return [n for n in names if name_pattern(n).search(text or "")]


def report(sections: list[tuple[str, list]]) -> str:
    """Relatório em Markdown (qa.md / consistencia.md) com os problemas de cada parte."""
    lines = []
    for title, problems in sections:
        if not problems:
            continue
        lines.append(f"## {title}")
        lines += [f"- {p}" for p in problems]
        lines.append("")
    return "\n".join(lines).strip()
