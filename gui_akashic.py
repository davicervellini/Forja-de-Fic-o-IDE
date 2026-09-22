"""
gui_akashic.py — Telas do Registro Akáshico.

- AkashicWizard: assistente de criação (árvore de escolhas em cascata) aberto ao criar um projeto novo.
- AkashicEditor: tela de edição do Registro Akáshico dentro do projeto (universos, personagens e texto).

A lógica das perguntas fica em pipeline/akashic_tree.py e a geração do arquivo em pipeline/akashic_builder.py; aqui
só há interface.
"""

import re
import shutil
from dataclasses import asdict
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

from pipeline import akashic_tree as tree
from pipeline.akashic import build_registro_modelo
from pipeline.akashic_builder import build_akashic, catalog_universe
from pipeline.akashic_catalog import BY_NAME
from pipeline.akashic_migrate import migrate_text
from pipeline.akashic_schema import (
    ROLES, STRUCTURES, AkashicMeta, Character, Universe, read_meta, slugify, write_meta,
)
from pipeline.io_utils import read_file, write_file

BG_DARK, BG_PANEL, BG_SIDE, BG_CARD, BG_HOVER = "#0f1115", "#161a22", "#12151c", "#1c212b", "#252b38"
ACCENT, GREEN, RED, AMBER = "#3b82f6", "#10b981", "#ef4444", "#f59e0b"
TEXT, TEXT_DIM, BORDER = "#e5e7eb", "#9ca3af", "#2a3140"

ORIGINS = ["nativo", "reencarnado", "transportado", "personagem original", "canônico"]
CHAR_ROLES = {"protagonist": "Protagonista", "supporting": "Apoio", "antagonist": "Antagonista"}


def _font(size=12, bold=False, mono=False):
    return ctk.CTkFont(family="Consolas" if mono else None, size=size, weight="bold" if bold else "normal")


def _invert(d: dict) -> dict:
    return {v: k for k, v in d.items()}


# ── Editores reutilizáveis (assistente e tela de edição) ─────────────────────────────

class UniversesEditor(ctk.CTkFrame):
    """Lista editável de universos: papel, wiki, ativo/reserva, personagens permitidos e ficha para o modelo."""

    def __init__(self, parent, show_sheet: bool = False):
        super().__init__(parent, fg_color="transparent")
        self.rows: list[dict] = []
        self.show_sheet = show_sheet
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        self.catalog_cb = ctk.CTkComboBox(top, values=sorted(BY_NAME), width=230, fg_color=BG_CARD, border_color=BORDER)
        self.catalog_cb.set("Escolha no catálogo…")
        self.catalog_cb.pack(side="left", padx=(0, 6))
        ctk.CTkButton(top, text="＋ Do catálogo", width=110, command=self._add_catalog, fg_color=GREEN).pack(side="left", padx=3)
        self.custom_entry = ctk.CTkEntry(top, width=200, placeholder_text="Universo novo (nome)", fg_color=BG_DARK, border_color=BORDER)
        self.custom_entry.pack(side="left", padx=(14, 6))
        ctk.CTkButton(top, text="＋ Novo", width=80, command=self._add_custom, fg_color=BG_CARD, hover_color=BG_HOVER).pack(side="left", padx=3)
        self.scroll = ctk.CTkScrollableFrame(self, fg_color=BG_DARK, scrollbar_button_color=BORDER)
        self.scroll.grid(row=1, column=0, sticky="nsew")
        self.scroll.grid_columnconfigure(0, weight=1)

    def _add_catalog(self):
        name = self.catalog_cb.get()
        if name not in BY_NAME:
            return
        if any(r["name"].get() == name for r in self.rows):
            messagebox.showinfo("Universo", f"“{name}” já está na lista.")
            return
        self._add_row(catalog_universe(name))

    def _add_custom(self):
        name = self.custom_entry.get().strip()
        if not name:
            return
        self.custom_entry.delete(0, "end")
        self._add_row(Universe(id=slugify(name), name=name, role="source"))

    def _add_row(self, u: Universe):
        card = ctk.CTkFrame(self.scroll, fg_color=BG_CARD, corner_radius=8)
        card.grid(row=len(self.rows), column=0, sticky="ew", padx=2, pady=4)
        card.grid_columnconfigure(1, weight=1)
        row = {"card": card, "id": u.id, "notes": u.notes, "model_sheet": u.model_sheet, "sheet_tb": None,
               "name": ctk.StringVar(value=u.name),
               "role": ctk.StringVar(value=ROLES.get(u.role, u.role)), "wiki": ctk.StringVar(value=u.wiki),
               "active": ctk.BooleanVar(value=u.active), "allowed": ctk.StringVar(value=", ".join(u.allowed_characters))}
        ctk.CTkEntry(card, textvariable=row["name"], font=_font(13, True), fg_color=BG_DARK, border_color=BORDER).grid(row=0, column=0, columnspan=2, padx=10, pady=(8, 4), sticky="ew")
        ctk.CTkComboBox(card, values=list(ROLES.values()), variable=row["role"], width=250, fg_color=BG_DARK, border_color=BORDER).grid(row=0, column=2, padx=6, pady=(8, 4))
        ctk.CTkCheckBox(card, text="Ativo na história", variable=row["active"], width=140).grid(row=0, column=3, padx=6, pady=(8, 4))
        ctk.CTkButton(card, text="✕", width=30, fg_color=BG_HOVER, hover_color=RED, command=lambda r=row: self._remove(r)).grid(row=0, column=4, padx=(0, 10), pady=(8, 4))
        ctk.CTkLabel(card, text="Wiki (subdomínio)", text_color=TEXT_DIM, font=_font(11)).grid(row=1, column=0, padx=(10, 6), sticky="w")
        ctk.CTkEntry(card, textvariable=row["wiki"], width=170, fg_color=BG_DARK, border_color=BORDER, placeholder_text="ex.: stargate").grid(row=1, column=1, sticky="w", pady=2)
        ctk.CTkLabel(card, text="Personagens permitidos (separe por vírgula)", text_color=TEXT_DIM, font=_font(11)).grid(row=2, column=0, columnspan=2, padx=10, sticky="w")
        ctk.CTkEntry(card, textvariable=row["allowed"], fg_color=BG_DARK, border_color=BORDER).grid(row=3, column=0, columnspan=5, padx=10, pady=(0, 10), sticky="ew")
        if self.show_sheet:
            ctk.CTkLabel(card, text="Ficha para o modelo, em inglês: o que existe aqui e o que nunca aparece (só universos ativos vão para o modelo)",
                         text_color=TEXT_DIM, font=_font(11)).grid(row=4, column=0, columnspan=5, padx=10, sticky="w")
            tb = ctk.CTkTextbox(card, height=70, wrap="word", font=_font(12), fg_color=BG_DARK, border_color=BORDER, border_width=1)
            tb.insert("1.0", u.model_sheet)
            tb.grid(row=5, column=0, columnspan=5, padx=10, pady=(0, 10), sticky="ew")
            row["sheet_tb"] = tb
        row["name_value"] = u.name
        self.rows.append(row)

    def _remove(self, row: dict):
        row["card"].destroy()
        self.rows.remove(row)

    def set(self, universes: list[Universe]):
        for r in list(self.rows):
            self._remove(r)
        for u in universes:
            self._add_row(u)

    def get(self) -> list[Universe]:
        roles = _invert(ROLES)
        out = []
        for r in self.rows:
            name = r["name"].get().strip()
            if not name:
                continue
            allowed = [a.strip() for a in r["allowed"].get().split(",") if a.strip()]
            sheet = r["sheet_tb"].get("1.0", "end").strip() if r["sheet_tb"] else r["model_sheet"]
            out.append(Universe(id=r["id"], name=name, role=roles.get(r["role"].get(), "source"),
                                wiki=r["wiki"].get().strip(), active=bool(r["active"].get()),
                                notes=r["notes"], allowed_characters=allowed, model_sheet=sheet))
        return out


class CharactersEditor(ctk.CTkFrame):
    """Lista editável de personagens (nome, origem, idade, papel e ficha para o modelo)."""

    def __init__(self, parent, default_role="protagonist", show_role=True, show_sheet: bool = False):
        super().__init__(parent, fg_color="transparent")
        self.default_role, self.show_role, self.rows = default_role, show_role, []
        self.show_sheet = show_sheet
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        ctk.CTkButton(self, text="＋ Personagem", width=130, command=lambda: self._add_row(Character(name="", role=self.default_role)), fg_color=GREEN).grid(row=0, column=0, sticky="w", pady=(0, 6))
        self.scroll = ctk.CTkScrollableFrame(self, fg_color=BG_DARK, scrollbar_button_color=BORDER)
        self.scroll.grid(row=1, column=0, sticky="nsew")
        self.scroll.grid_columnconfigure(0, weight=1)

    def _add_row(self, c: Character):
        card = ctk.CTkFrame(self.scroll, fg_color=BG_CARD, corner_radius=8)
        card.grid(row=len(self.rows), column=0, sticky="ew", padx=2, pady=4)
        card.grid_columnconfigure(0, weight=1)
        row = {"card": card, "universe": c.universe, "notes": c.notes, "sheet": c.sheet, "sheet_label": c.sheet_label,
               "sheet_tb": None, "name": ctk.StringVar(value=c.name),
               "origin": ctk.StringVar(value=c.origin), "age": ctk.StringVar(value=c.age),
               "role": ctk.StringVar(value=CHAR_ROLES.get(c.role, c.role))}
        ctk.CTkEntry(card, textvariable=row["name"], placeholder_text="Nome", font=_font(13, True), fg_color=BG_DARK, border_color=BORDER).grid(row=0, column=0, padx=10, pady=8, sticky="ew")
        ctk.CTkComboBox(card, values=ORIGINS, variable=row["origin"], width=170, fg_color=BG_DARK, border_color=BORDER).grid(row=0, column=1, padx=4)
        ctk.CTkEntry(card, textvariable=row["age"], placeholder_text="Idade", width=70, fg_color=BG_DARK, border_color=BORDER).grid(row=0, column=2, padx=4)
        col = 3
        if self.show_role:
            ctk.CTkComboBox(card, values=list(CHAR_ROLES.values()), variable=row["role"], width=130, fg_color=BG_DARK, border_color=BORDER).grid(row=0, column=col, padx=4)
            col += 1
        ctk.CTkButton(card, text="✕", width=30, fg_color=BG_HOVER, hover_color=RED, command=lambda r=row: self._remove(r)).grid(row=0, column=col, padx=(4, 10))
        if self.show_sheet:
            ctk.CTkLabel(card, text="Ficha para o modelo, em inglês: origem, aparência, personalidade, poderes, jeito de falar",
                         text_color=TEXT_DIM, font=_font(11)).grid(row=1, column=0, columnspan=col + 1, padx=10, sticky="w")
            tb = ctk.CTkTextbox(card, height=90, wrap="word", font=_font(12), fg_color=BG_DARK, border_color=BORDER, border_width=1)
            tb.insert("1.0", c.sheet)
            tb.grid(row=2, column=0, columnspan=col + 1, padx=10, pady=(0, 10), sticky="ew")
            row["sheet_tb"] = tb
        self.rows.append(row)

    def _remove(self, row: dict):
        row["card"].destroy()
        self.rows.remove(row)

    def set(self, chars: list[Character]):
        for r in list(self.rows):
            self._remove(r)
        for c in chars:
            self._add_row(c)

    def get(self) -> list[Character]:
        roles = _invert(CHAR_ROLES)
        out = []
        for r in self.rows:
            name = r["name"].get().strip()
            if name:
                sheet = r["sheet_tb"].get("1.0", "end").strip() if r["sheet_tb"] else r["sheet"]
                out.append(Character(name=name, role=roles.get(r["role"].get(), self.default_role) if self.show_role else self.default_role,
                                     origin=r["origin"].get().strip(), age=r["age"].get().strip(),
                                     universe=r["universe"], notes=r["notes"], sheet=sheet,
                                     sheet_label=r["sheet_label"]))
        return out


# ── Assistente de criação ─────────────────────────────────────────────────────────

class AkashicWizard(ctk.CTkToplevel):
    """Percorre a árvore de escolhas e entrega as respostas por `on_done(answers)`."""

    def __init__(self, parent, project_name: str, on_done, on_skip=None):
        super().__init__(parent)
        self.title(f"Registro Akáshico: {project_name}")
        self.geometry("1100x760")
        self.minsize(900, 620)
        self.configure(fg_color=BG_PANEL)
        self.transient(parent)
        self.on_done, self.on_skip = on_done, on_skip
        self.answers: dict = {"title": project_name}
        self.pos = 0
        self._widget = None
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self.side = ctk.CTkScrollableFrame(self, width=270, fg_color=BG_SIDE, corner_radius=0, scrollbar_button_color=BORDER)
        self.side.grid(row=0, column=0, sticky="nsew")
        self.main = ctk.CTkFrame(self, fg_color=BG_PANEL, corner_radius=0)
        self.main.grid(row=0, column=1, sticky="nsew", padx=20, pady=16)
        self.main.grid_columnconfigure(0, weight=1)
        self.main.grid_rowconfigure(2, weight=1)
        self.title_lbl = ctk.CTkLabel(self.main, text="", font=_font(19, True), text_color=TEXT, wraplength=760, justify="left", anchor="w")
        self.title_lbl.grid(row=0, column=0, sticky="ew")
        self.help_lbl = ctk.CTkLabel(self.main, text="", font=_font(12), text_color=TEXT_DIM, wraplength=760, justify="left", anchor="w")
        self.help_lbl.grid(row=1, column=0, sticky="ew", pady=(2, 10))
        self.body = ctk.CTkFrame(self.main, fg_color="transparent")
        self.body.grid(row=2, column=0, sticky="nsew")
        self.body.grid_columnconfigure(0, weight=1)
        self.body.grid_rowconfigure(0, weight=1)
        self.error_lbl = ctk.CTkLabel(self.main, text="", text_color=RED, font=_font(12), anchor="w")
        self.error_lbl.grid(row=3, column=0, sticky="ew", pady=(6, 0))
        foot = ctk.CTkFrame(self.main, fg_color="transparent")
        foot.grid(row=4, column=0, sticky="ew", pady=(8, 0))
        foot.grid_columnconfigure(1, weight=1)
        self.btn_back = ctk.CTkButton(foot, text="← Voltar", width=110, command=self._back, fg_color=BG_CARD, hover_color=BG_HOVER)
        self.btn_back.grid(row=0, column=0)
        if on_skip:
            ctk.CTkButton(foot, text="Pular (projeto vazio)", width=160, command=self._skip, fg_color="transparent", border_width=1, border_color=BORDER, hover_color=BG_HOVER, text_color=TEXT_DIM).grid(row=0, column=1, padx=10)
        self.btn_next = ctk.CTkButton(foot, text="Próximo →", width=140, command=self._next, fg_color=ACCENT)
        self.btn_next.grid(row=0, column=2)
        self.protocol("WM_DELETE_WINDOW", self._skip)
        self._render()
        self.after(150, self._grab)

    def _grab(self):
        try:
            self.grab_set()
        except Exception:  # janela ainda não visível: o modal é só conforto
            pass

    # navegação
    def _visible(self):
        return tree.visible_questions(self.answers)

    def _skip(self):
        if self.on_skip:
            self.on_skip()
        self.destroy()

    def _back(self):
        self._collect()
        if self.pos > 0:
            self.pos -= 1
            self._render()

    def _next(self):
        q = self._current_question()
        if q is not None:
            self._collect()
            if q.required and not tree.is_answered(q, self.answers):
                need = tree.min_items(q, self.answers)
                self.error_lbl.configure(text=f"Responda esta pergunta para continuar." if q.kind not in ("universes", "characters") else f"Adicione pelo menos {need} item(ns).")
                return
            self.answers = tree.prune(self.answers)
        if self.pos < len(self._visible()):
            self.pos += 1
            self._render()
        else:
            self._finish()

    def _current_question(self):
        vis = self._visible()
        return vis[self.pos] if self.pos < len(vis) else None

    def _finish(self):
        self.on_done(self.answers)
        self.destroy()

    # renderização
    def _render(self):
        vis = self._visible()
        self.pos = min(self.pos, len(vis))
        self.error_lbl.configure(text="")
        for w in self.body.winfo_children():
            w.destroy()
        self._widget = None
        self._render_side(vis)
        self.btn_back.configure(state="normal" if self.pos > 0 else "disabled")
        if self.pos >= len(vis):
            self._render_review()
            return
        q = vis[self.pos]
        self.title_lbl.configure(text=f"{self.pos + 1}/{len(vis)}  ·  {q.title}")
        self.help_lbl.configure(text=q.help + ("" if q.required else ("  " if q.help else "") + "(opcional)"))
        self.btn_next.configure(text="Revisar →" if self.pos == len(vis) - 1 else "Próximo →", fg_color=ACCENT)
        getattr(self, f"_w_{q.kind}")(q)

    def _render_side(self, vis):
        for w in self.side.winfo_children():
            w.destroy()
        ctk.CTkLabel(self.side, text="ÁRVORE DE ESCOLHAS", font=_font(10, True), text_color=TEXT_DIM).pack(anchor="w", padx=12, pady=(12, 6))
        for i, q in enumerate(vis):
            done = tree.is_answered(q, self.answers)
            mark = "●" if done else "○"
            color = ACCENT if i == self.pos else (GREEN if done else TEXT_DIM)
            ctk.CTkButton(self.side, text=f"{mark}  {q.title[:34]}", anchor="w", height=26, fg_color=BG_HOVER if i == self.pos else "transparent", hover_color=BG_HOVER, text_color=color, font=_font(11), command=lambda idx=i: self._jump(idx)).pack(fill="x", padx=6, pady=1)
        ctk.CTkButton(self.side, text="▸  Revisar e criar", anchor="w", height=26, fg_color=BG_HOVER if self.pos >= len(vis) else "transparent", hover_color=BG_HOVER, text_color=AMBER, font=_font(11), command=lambda: self._jump(len(vis))).pack(fill="x", padx=6, pady=(8, 1))

    def _jump(self, idx: int):
        self._collect()
        self.answers = tree.prune(self.answers)
        self.pos = idx
        self._render()

    # widgets por tipo de pergunta
    def _w_text(self, q):
        e = ctk.CTkEntry(self.body, font=_font(14), height=38, fg_color=BG_DARK, border_color=BORDER)
        e.insert(0, str(self.answers.get(q.id, "")))
        e.grid(row=0, column=0, sticky="new")
        e.focus_set()
        self._widget = ("text", e)

    def _w_longtext(self, q):
        t = ctk.CTkTextbox(self.body, font=_font(13), fg_color=BG_DARK, border_color=BORDER, border_width=1)
        t.insert("1.0", str(self.answers.get(q.id, "")))
        t.grid(row=0, column=0, sticky="nsew")
        self._widget = ("longtext", t)

    def _w_single(self, q):
        var = ctk.StringVar(value=self.answers.get(q.id, ""))
        for i, (value, label) in enumerate(q.options):
            ctk.CTkRadioButton(self.body, text=label, variable=var, value=value, font=_font(14)).grid(row=i, column=0, sticky="w", pady=6, padx=6)
        self._widget = ("single", var)

    def _w_multi(self, q):
        chosen = set(self.answers.get(q.id, []))
        vars_ = {}
        for i, (value, label) in enumerate(q.options):
            vars_[value] = ctk.BooleanVar(value=value in chosen)
            ctk.CTkCheckBox(self.body, text=label, variable=vars_[value], font=_font(14)).grid(row=i, column=0, sticky="w", pady=6, padx=6)
        self._widget = ("multi", vars_)

    def _w_universes(self, q):
        ed = UniversesEditor(self.body)
        ed.grid(row=0, column=0, sticky="nsew")
        ed.set([Universe(**{k: v for k, v in u.items() if k in Universe.__dataclass_fields__}) for u in self.answers.get("universes", [])])
        self._widget = ("universes", ed)

    def _w_characters(self, q):
        role = "protagonist" if q.id == "protagonists" else "supporting"
        ed = CharactersEditor(self.body, default_role=role, show_role=False)
        ed.grid(row=0, column=0, sticky="nsew")
        ed.set([Character(**{k: v for k, v in c.items() if k in Character.__dataclass_fields__}) for c in self.answers.get(q.id, [])])
        self._widget = ("characters", ed)

    def _collect(self):
        q = self._current_question()
        if q is None or self._widget is None:
            return
        kind, w = self._widget
        if kind == "text":
            self.answers[q.id] = w.get().strip()
        elif kind == "longtext":
            self.answers[q.id] = w.get("1.0", "end").strip()
        elif kind == "single":
            self.answers[q.id] = w.get()
        elif kind == "multi":
            self.answers[q.id] = [v for v, var in w.items() if var.get()]
        elif kind == "universes":
            self.answers[q.id] = [asdict(u) for u in w.get()]
        elif kind == "characters":
            self.answers[q.id] = [asdict(c) for c in w.get()]

    def _render_review(self):
        pending = tree.missing(self.answers)
        text = build_akashic(self.answers)
        n_todo = text.count("[A DEFINIR")
        self.title_lbl.configure(text="Revisão do Registro Akáshico")
        self.help_lbl.configure(text=f"{n_todo} marcação(ões) [A DEFINIR] para preencher depois na tela de edição." if not pending
                                else f"Faltam respostas obrigatórias: {', '.join(p.title for p in pending[:3])}…")
        box = ctk.CTkTextbox(self.body, font=_font(12, mono=True), fg_color=BG_DARK, border_color=BORDER, border_width=1)
        box.insert("1.0", text)
        box.configure(state="disabled")
        box.grid(row=0, column=0, sticky="nsew")
        self.btn_next.configure(text="✓ Criar projeto", fg_color=GREEN if not pending else BG_HOVER, state="normal" if not pending else "disabled")


# ── Tela de edição dentro do projeto ─────────────────────────────────────────────────

class AkashicEditor(ctk.CTkToplevel):
    """Edita o Registro Akáshico do projeto: universos, personagens e texto. Salvar recompila o modelo curto."""

    def __init__(self, parent, project, on_saved=None):
        super().__init__(parent)
        self.project, self.on_saved = project, on_saved
        self.title(f"Registro Akáshico: {project.name}")
        self.geometry("1100x760")
        self.minsize(900, 600)
        self.configure(fg_color=BG_PANEL)
        self.transient(parent)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        self.banner = ctk.CTkFrame(self, fg_color=BG_CARD, corner_radius=0)
        self.banner.grid(row=0, column=0, sticky="ew")
        self.banner_lbl = ctk.CTkLabel(self.banner, text="", text_color=AMBER, font=_font(12), anchor="w")
        self.banner_lbl.pack(side="left", padx=14, pady=8)
        self.btn_migrate = ctk.CTkButton(self.banner, text="Converter para o formato v2", width=200, command=self._migrate, fg_color=AMBER, text_color="#111")
        self.tabs = ctk.CTkTabview(self, fg_color=BG_PANEL, segmented_button_selected_color=ACCENT)
        self.tabs.grid(row=1, column=0, sticky="nsew", padx=14, pady=(8, 4))
        for name in ("Universos", "Personagens", "Texto"):
            self.tabs.add(name)
        u_tab = self.tabs.tab("Universos")
        u_tab.grid_columnconfigure(0, weight=1)
        u_tab.grid_rowconfigure(1, weight=1)
        top = ctk.CTkFrame(u_tab, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        ctk.CTkLabel(top, text="Estrutura do mundo", text_color=TEXT_DIM, font=_font(12)).pack(side="left", padx=(0, 8))
        self.structure_var = ctk.StringVar()
        ctk.CTkComboBox(top, values=list(STRUCTURES.values()), variable=self.structure_var, width=330, fg_color=BG_CARD, border_color=BORDER).pack(side="left")
        self.universes = UniversesEditor(u_tab, show_sheet=True)
        self.universes.grid(row=1, column=0, sticky="nsew")
        c_tab = self.tabs.tab("Personagens")
        c_tab.grid_columnconfigure(0, weight=1)
        c_tab.grid_rowconfigure(0, weight=1)
        self.characters = CharactersEditor(c_tab, show_sheet=True)
        self.characters.grid(row=0, column=0, sticky="nsew")
        t_tab = self.tabs.tab("Texto")
        t_tab.grid_columnconfigure(0, weight=1)
        t_tab.grid_rowconfigure(1, weight=1)
        nav = ctk.CTkFrame(t_tab, fg_color="transparent")
        nav.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        ctk.CTkLabel(nav, text="Ir para a seção", text_color=TEXT_DIM, font=_font(12)).pack(side="left", padx=(0, 8))
        self.section_cb = ctk.CTkComboBox(nav, values=[], width=420, command=self._goto_section, fg_color=BG_CARD, border_color=BORDER)
        self.section_cb.pack(side="left")
        self.text_box = ctk.CTkTextbox(t_tab, font=_font(13, mono=True), fg_color=BG_DARK, border_color=BORDER, border_width=1, undo=True)
        self.text_box.grid(row=1, column=0, sticky="nsew")
        foot = ctk.CTkFrame(self, fg_color="transparent")
        foot.grid(row=2, column=0, sticky="ew", padx=14, pady=(4, 12))
        foot.grid_columnconfigure(1, weight=1)
        ctk.CTkButton(foot, text="💾 Salvar e recompilar modelo", width=230, command=self._save, fg_color=GREEN).grid(row=0, column=0)
        self.status_lbl = ctk.CTkLabel(foot, text="", text_color=TEXT_DIM, font=_font(12), anchor="w")
        self.status_lbl.grid(row=0, column=1, padx=12, sticky="ew")
        ctk.CTkButton(foot, text="📂 Importar arquivo…", width=170, command=self._import, fg_color=BG_CARD, hover_color=BG_HOVER).grid(row=0, column=2, padx=6)
        ctk.CTkButton(foot, text="Fechar", width=90, command=self.destroy, fg_color=BG_CARD, hover_color=BG_HOVER).grid(row=0, column=3)
        self._load()
        self.after(150, lambda: self._safe_grab())

    def _safe_grab(self):
        try:
            self.grab_set()
        except Exception:
            pass

    # carregar / salvar
    def _load(self, text: str | None = None):
        path = self.project.akashic_path
        if text is None:
            text = read_file(path) if path.exists() else ""
        meta, body = read_meta(text)
        self._meta_missing = meta is None
        self.meta = meta or AkashicMeta(title=self.project.name)
        if meta is not None and not self.meta.lists_synced:
            # Primeira abertura: as fichas das seções 9.5 e 5.8 do texto passam para as abas.
            from pipeline.akashic_sync import import_lists_from_body
            import_lists_from_body(self.meta, body)
        if self._meta_missing and body.strip():
            self.banner_lbl.configure(text="Este arquivo está no formato antigo (v1): universos e personagens ainda não foram lidos.")
            self.banner.grid()
            self.btn_migrate.pack(side="right", padx=14, pady=6)
        else:
            self.banner.grid_remove()
        self.structure_var.set(STRUCTURES.get(self.meta.structure, self.meta.structure))
        self.universes.set(self.meta.universes)
        self.characters.set(self.meta.characters)
        self.text_box.delete("1.0", "end")
        self.text_box.insert("1.0", body)
        self._refresh_sections()

    def _refresh_sections(self):
        body = self.text_box.get("1.0", "end")
        heads = [(i + 1, ln.strip()) for i, ln in enumerate(body.splitlines()) if re.match(r"^#{1,3} ", ln)]
        self._section_lines = {h: n for n, h in heads}
        values = list(self._section_lines)
        self.section_cb.configure(values=values)
        self.section_cb.set(values[0] if values else "")

    def _goto_section(self, label: str):
        line = self._section_lines.get(label)
        if line:
            self.text_box.see(f"{line}.0")
            self.text_box.mark_set("insert", f"{line}.0")

    def _current_meta(self) -> AkashicMeta:
        structure = _invert(STRUCTURES).get(self.structure_var.get(), self.meta.structure)
        body = self.text_box.get("1.0", "end")
        m = re.search(r"^# (?:Bíblia do Mundo:\s*)?(.+)$", body, re.M)
        return AkashicMeta(title=self.meta.title or (m.group(1).strip() if m else self.project.name),
                           structure=structure, language=self.meta.language, universes=self.universes.get(),
                           characters=self.characters.get(), answers=self.meta.answers,
                           lists_synced=self.meta.lists_synced)

    def _save(self):
        body = self.text_box.get("1.0", "end").rstrip() + "\n"
        path = self.project.akashic_path
        if path.exists():
            shutil.copy2(path, path.with_suffix(".md.bak"))  # uma cópia rolante: projetos/ não tem histórico no Git
        meta = self._current_meta()
        if not self._meta_missing:
            from pipeline.akashic_sync import render_lists_into_body
            meta.lists_synced = True
            body = render_lists_into_body(body, meta)
        write_file(path, write_meta(body, meta))
        ok, msg = build_registro_modelo(self.project.project_dir)
        self.status_lbl.configure(text=("✓ Salvo. " if ok else "Salvo, mas o modelo falhou: ") + msg, text_color=GREEN if ok else AMBER)
        self._load()
        if self.on_saved:
            self.on_saved(ok)

    def _migrate(self):
        path = self.project.akashic_path
        stamp = path.with_suffix(".v1.bak.md")
        shutil.copy2(path, stamp)
        new_text, report = migrate_text(self._raw_text())
        write_file(path, new_text)
        self.btn_migrate.pack_forget()
        self._load(new_text)
        self.status_lbl.configure(text=f"{report} Cópia do original: {stamp.name}", text_color=GREEN)

    def _raw_text(self) -> str:
        return read_file(self.project.akashic_path)

    def _import(self):
        picked = filedialog.askopenfilename(filetypes=[("Markdown", "*.md *.txt"), ("Todos", "*.*")])
        if not picked:
            return
        if self.project.akashic_path.exists() and not messagebox.askyesno("Importar", "Substituir o Registro Akáshico atual pelo arquivo escolhido?\nUma cópia .bak será mantida."):
            return
        content = read_file(Path(picked))
        if self.project.akashic_path.exists():
            shutil.copy2(self.project.akashic_path, self.project.akashic_path.with_suffix(".md.bak"))
        write_file(self.project.akashic_path, content)
        ok, msg = build_registro_modelo(self.project.project_dir)
        self._load()
        self.status_lbl.configure(text=("✓ Importado. " if ok else "Importado, mas o modelo falhou: ") + msg, text_color=GREEN if ok else AMBER)
        if self.on_saved:
            self.on_saved(ok)
