"""
memory_ops.py — Atualização da memória da história por mudanças, não por reescrita.

Antes, o modelo de resumo reescrevia a memória, o roster e os threads inteiros a cada capítulo.
Um modelo de 8B nessa tarefa perde coisas (threads antigos somem sem ter sido resolvidos),
traduz nomes oficiais e transforma em cânone o que um capítulo ruim inventou.

Agora o modelo devolve só as mudanças, uma por linha:

    MEMORY ADD: fato permanente novo
    MEMORY REPLACE: fato antigo, copiado => fato novo
    ROSTER NEW: Nome | status | local | papel | voz
    ROSTER UPDATE: Nome | Campo | valor novo
    THREAD NEW: descrição
    THREAD RESOLVED: thread existente, copiado | EVIDENCE: trecho do resumo que mostra a solução
    CALLBACK: detalhe concreto que vale retomar depois (número do ticket, frase repetida)
    END STATE: onde o protagonista está e o que acontece no fim exato do capítulo
    NONE

e o código aplica. O que já existe só sai quando a mudança aponta para ele (com semelhança alta) e,
no caso de thread resolvido, traz uma evidência que está no resumo. Mudanças que citam personagens
que não podem estar no capítulo são recusadas e registradas.
"""

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from pipeline.canon import mentions

MATCH_RATIO = 0.85
_OP = re.compile(r"^\s*[-*]?\s*(MEMORY ADD|MEMORY REPLACE|ROSTER NEW|ROSTER UPDATE|THREAD NEW|THREAD RESOLVED|"
                 r"CALLBACK|END STATE|NONE)\b\s*:?\s*(.*)$", re.I)
_THREAD = re.compile(r"^\s*[-*]?\s*\[Ch\.?\s*(\d+)\]\s*(.+)$", re.I)
_ROSTER_FIELDS = ("Name", "Status", "Location", "Role", "Voice")
# Rótulos antigos (roster escrito em português ou espanhol) que valem como os em inglês.
_FIELD_ALIASES = {
    "name": ("name", "nome", "nombre"),
    "status": ("status", "estado", "situação"),
    "location": ("location", "current location", "localização", "localização atual", "local", "last known place",
                 "current location or last known place", "localização atual ou último lugar conhecido"),
    "role": ("role", "papel", "função", "brief role", "relationship", "brief role / relationship to protagonist",
             "papel / relação com o protagonista"),
    "voice": ("voice", "voz", "personality", "voice note", "personalidade", "one-line personality or voice note",
              "nota de personalidade ou voz"),
}
CALLBACKS_MAX = 25


@dataclass
class Op:
    kind: str
    args: list[str]
    raw: str


@dataclass
class Result:
    memory: str
    roster: str
    threads: str
    callbacks: str
    applied: list[str] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)


def parse_ops(raw: str) -> list[Op]:
    ops = []
    for line in (raw or "").splitlines():
        m = _OP.match(line)
        if not m:
            continue
        kind = m.group(1).upper()
        rest = m.group(2).strip()
        if kind == "MEMORY REPLACE":
            args = [p.strip() for p in rest.split("=>", 1)]
        elif kind == "THREAD RESOLVED":
            parts = re.split(r"\|\s*EVIDENCE\s*:", rest, maxsplit=1, flags=re.I)
            args = [p.strip() for p in parts]
        elif kind.startswith("ROSTER"):
            args = [p.strip() for p in rest.split("|")]
        else:
            args = [rest]
        ops.append(Op(kind, args, line.strip()))
    return ops


def _similar(a: str, b: str) -> float:
    return SequenceMatcher(None, _clean(a), _clean(b)).ratio()


def _clean(s: str) -> str:
    s = re.sub(r"^\s*[-*]\s*", "", s or "")
    s = re.sub(r"^\[Ch\.?\s*\d+\]\s*", "", s, flags=re.I)
    return re.sub(r"\s+", " ", s).strip().lower()


def _best(target: str, lines: list[str]) -> int | None:
    scored = [(_similar(target, l), i) for i, l in enumerate(lines) if l.strip()]
    if not scored:
        return None
    ratio, i = max(scored)
    return i if ratio >= MATCH_RATIO else None


def evidence_ok(evidence: str, summary: str) -> bool:
    """A evidência está no resumo: pelo menos 60% das palavras dela, e no mínimo três."""
    words = [w for w in re.findall(r"[a-zà-ÿ0-9']+", (evidence or "").lower()) if len(w) >= 3]
    if len(words) < 3:
        return False
    text = (summary or "").lower()
    return sum(w in text for w in words) / len(words) >= 0.6


# ── Roster em blocos ─────────────────────────────────────────

def roster_blocks(roster: str) -> list[dict]:
    """[{name, fields: {campo: valor}, lines: [...]}] a partir do roster em Markdown."""
    blocks: list[dict] = []
    current = None
    for line in (roster or "").splitlines():
        header = re.match(r"^\s*[-*]?\s*\*\*([^*]{2,60})\*\*\s*:?\s*$", line)
        if header:
            current = {"name": header.group(1).strip(), "fields": {}, "order": [], "header": True}
            blocks.append(current)
            continue
        m = re.match(r"^\s*[-*]\s*([^:]{2,60}):\s*(.*)$", line)
        if m and _field_key(m.group(1)) == "name":
            if current is not None and current.get("header") and "name" not in current["fields"]:
                current["name"] = m.group(2).strip()
            else:
                current = {"name": m.group(2).strip(), "fields": {}, "order": []}
                blocks.append(current)
        if current is None:
            continue
        if m:
            key = _field_key(m.group(1)) or m.group(1).strip()
            current["fields"][key] = m.group(2).strip()
            if key not in current["order"]:
                current["order"].append(key)
    return blocks


def _field_key(label: str) -> str:
    label = label.strip().lower()
    return next((k for k, names in _FIELD_ALIASES.items() if label in names), "")


def render_roster(blocks: list[dict]) -> str:
    out = []
    for b in blocks:
        lines = [f"**{b['name'].upper()}**"]
        keys = ["name"] + [k for k in b["order"] if k != "name"]
        for k in keys:
            label = k.capitalize() if k in _FIELD_ALIASES else k
            value = b["name"] if k == "name" else b["fields"].get(k, "")
            if value:
                lines.append(f"- {label}: {value}")
        out.append("\n".join(lines))
    return "\n\n".join(out)


def _same_name(a: str, b: str) -> bool:
    na, nb = _clean(a), _clean(b)
    return na == nb or na.startswith(nb + " ") or nb.startswith(na + " ")


# ── Aplicação ────────────────────────────────────────────────

def _lines(text: str) -> list[str]:
    return [l for l in (text or "").splitlines() if l.strip()]


def apply_ops(ops: list[Op], memory: str, roster: str, threads: str, callbacks: str, summary: str,
              chapter_num: int, allowed: list[str], forbidden: list[str], max_threads: int = 15,
              max_roster: int = 40) -> Result:
    """
    Aplica as mudanças. `allowed`: personagens que podem estar na memória a partir deste capítulo
    (elenco da premissa, quem já estreou, quem já está no roster). `forbidden`: nomes que não
    podem entrar (proibidos na premissa ou que ainda não estrearam).
    """
    mem = _lines(memory)
    thr = _lines(threads)
    cbs = _lines(callbacks)
    blocks = roster_blocks(roster)
    res = Result(memory, roster, threads, callbacks)

    def bad_names(text: str) -> list[str]:
        return [n for n in forbidden if mentions(n, text) and not any(_same_name(n, a) for a in allowed)]

    def reject(op: Op, why: str):
        res.rejected.append(f"{op.raw} — {why}")

    for op in ops:
        a = op.args
        body = " ".join(a)
        if op.kind != "NONE" and bad_names(body):
            reject(op, "cita quem não pode estar na história ainda: " + ", ".join(bad_names(body)))
            continue
        if op.kind == "MEMORY ADD" and a[0]:
            if _best(a[0], mem) is None:
                mem.append(f"- {a[0].lstrip('- ')}")
                res.applied.append(op.raw)
        elif op.kind == "MEMORY REPLACE" and len(a) == 2 and a[1]:
            i = _best(a[0], mem)
            if i is None:
                reject(op, "o fato antigo não está na memória")
                continue
            mem[i] = f"- {a[1].lstrip('- ')}"
            res.applied.append(op.raw)
        elif op.kind == "ROSTER NEW" and a and a[0]:
            name = a[0]
            if any(_same_name(name, b["name"]) for b in blocks):
                continue
            if not any(_same_name(name, x) for x in allowed) and forbidden and mentions(name, " ".join(forbidden)):
                reject(op, "personagem fora do elenco permitido")
                continue
            values = dict(zip(("status", "location", "role", "voice"), a[1:5]))
            blocks.append({"name": name, "fields": {k: v for k, v in values.items() if v},
                           "order": ["name"] + [k for k in ("status", "location", "role", "voice") if values.get(k)]})
            res.applied.append(op.raw)
        elif op.kind == "ROSTER UPDATE" and len(a) >= 3 and a[2]:
            block = next((b for b in blocks if _same_name(a[0], b["name"])), None)
            if block is None:
                reject(op, "personagem não está no roster")
                continue
            key = _field_key(a[1]) or a[1].strip().lower()
            if key == "name":
                continue
            block["fields"][key] = a[2]
            if key not in block["order"]:
                block["order"].append(key)
            res.applied.append(op.raw)
        elif op.kind == "THREAD NEW" and a[0]:
            desc = re.sub(r"^\[Ch\.?\s*\d+\]\s*", "", a[0].lstrip("- "), flags=re.I)
            if _best(desc, thr) is None:
                thr.append(f"[Ch.{chapter_num:02d}] {desc}")
                res.applied.append(op.raw)
        elif op.kind == "THREAD RESOLVED" and a[0]:
            i = _best(a[0], thr)
            if i is None:
                reject(op, "o thread não está na lista")
                continue
            if len(a) < 2 or not evidence_ok(a[1], summary):
                reject(op, "sem evidência no resumo do capítulo")
                continue
            res.applied.append(op.raw + f"  (saiu: {thr[i]})")
            del thr[i]
        elif op.kind == "CALLBACK" and a[0]:
            if _best(a[0], cbs) is None:
                cbs.append(f"- [Ch.{chapter_num:02d}] {a[0].lstrip('- ')}")
                res.applied.append(op.raw)
        elif op.kind == "END STATE" and a[0]:
            mem = [l for l in mem if not re.match(r"^\s*-?\s*End of Ch\.?\s*\d+", l, re.I)]
            mem.insert(0, f"- End of Ch.{chapter_num:02d}: {a[0].lstrip('- ')}")
            res.applied.append(op.raw)

    if len(thr) > max_threads:
        # Os mais antigos ficam: são as promessas longas da história. Os novos além do limite saem.
        dropped = thr[max_threads:]
        thr = thr[:max_threads]
        res.rejected += [f"THREAD além do limite de {max_threads}: {t}" for t in dropped]
    if len(blocks) > max_roster:
        res.rejected += [f"ROSTER além do limite de {max_roster}: {b['name']}" for b in blocks[max_roster:]]
        blocks = blocks[:max_roster]
    if len(cbs) > CALLBACKS_MAX:
        cbs = cbs[-CALLBACKS_MAX:]

    res.memory = "\n".join(mem)
    res.threads = "\n".join(thr)
    res.callbacks = "\n".join(cbs)
    # Roster que não estava em blocos (texto livre antigo) só é regravado se alguma mudança o tocou.
    touched = any(op.kind.startswith("ROSTER") for op in ops if op.raw in res.applied)
    res.roster = render_roster(blocks) if (blocks and (touched or roster_blocks(roster))) else roster
    return res


def diff_report(chapter_num: int, result: Result) -> str:
    """memoria_diff.md: o que mudou na memória com o capítulo e o que foi recusado."""
    lines = [f"# Mudanças na memória — capítulo {chapter_num:02d}", ""]
    if result.applied:
        lines += ["## Aplicadas", ""] + [f"- {a}" for a in result.applied] + [""]
    else:
        lines += ["Nenhuma mudança aplicada.", ""]
    if result.rejected:
        lines += ["## Recusadas", ""] + [f"- {r}" for r in result.rejected] + [""]
    return "\n".join(lines).strip() + "\n"


def roster_for_chapter(roster: str, keep: list[str]) -> str:
    """
    Roster do prompt: ficha inteira de quem está no capítulo ou é citado na premissa/threads (`keep`),
    uma linha (nome e status) para os outros.
    """
    blocks = roster_blocks(roster)
    if not blocks:
        return roster
    full, short = [], []
    for b in blocks:
        if any(_same_name(b["name"], k) or mentions(b["name"], k) for k in keep):
            full.append(b)
        else:
            status = b["fields"].get("status", "")
            short.append(f"- {b['name']}" + (f" ({status})" if status else ""))
    out = render_roster(full)
    if short:
        out += ("\n\n" if out else "") + "Others (not in this chapter):\n" + "\n".join(short)
    return out
