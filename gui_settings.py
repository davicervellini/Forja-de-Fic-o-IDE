"""
gui_settings.py — Tela ⚙ Configurações.

Perfis de hardware, modelos instalados no Ollama, contexto, temperatura, camadas na GPU,
meta de palavras e pastas de dados e de log. Salvar grava o config.json na pasta de dados
e aplica os valores na hora: a próxima geração já usa as configurações novas.
"""

import os
import subprocess
import sys
import threading
from pathlib import Path
from tkinter import messagebox

import customtkinter as ctk

from pipeline import config
from pipeline.api import check_ollama_health, list_installed_models
from pipeline.logsetup import log_file
from pipeline.profiles import PROFILES, missing_models, profile_by_label

BG_DARK, BG_PANEL, BG_CARD, BG_HOVER = "#0f1115", "#161a22", "#1c212b", "#252b38"
ACCENT, GREEN, RED, AMBER = "#3b82f6", "#10b981", "#ef4444", "#f59e0b"
TEXT, TEXT_DIM, BORDER = "#e5e7eb", "#9ca3af", "#2a3140"

PHASES = [("Rascunho", "DRAFTING"), ("Polimento", "REFINING"), ("Resumo e memória", "SUMMARIZING")]


def open_folder(path: Path):
    path.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        os.startfile(str(path))  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


def _font(size=12, bold=False):
    return ctk.CTkFont(size=size, weight="bold" if bold else "normal")


class SettingsDialog(ctk.CTkToplevel):
    def __init__(self, parent, on_saved=None):
        super().__init__(parent)
        self.on_saved = on_saved
        self.title("Configurações")
        self.geometry("820x720")
        self.minsize(720, 560)
        self.configure(fg_color=BG_PANEL)
        self.transient(parent)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self.installed: list[str] = []
        self.vars: dict[str, ctk.Variable] = {}
        self.model_boxes: dict[str, ctk.CTkComboBox] = {}

        body = ctk.CTkScrollableFrame(self, fg_color=BG_PANEL)
        body.grid(row=0, column=0, sticky="nsew", padx=12, pady=(12, 4))
        body.grid_columnconfigure(0, weight=1)
        self.body = body
        cur = config.current_settings()

        # ── Perfil ──
        card = self._card("Perfil de hardware")
        self.profile_var = ctk.StringVar(value=PROFILES.get(cur["HARDWARE_PROFILE"], PROFILES["custom"])["label"])
        ctk.CTkComboBox(card, values=[p["label"] for p in PROFILES.values()], variable=self.profile_var,
                        command=self._apply_profile, width=460, fg_color=BG_DARK, border_color=BORDER
                        ).grid(row=1, column=0, columnspan=3, padx=12, pady=(0, 4), sticky="w")
        self.profile_note = ctk.CTkLabel(card, text="", text_color=TEXT_DIM, font=_font(11), wraplength=720, justify="left")
        self.profile_note.grid(row=2, column=0, columnspan=3, padx=12, pady=(0, 10), sticky="w")

        # ── Ollama ──
        card = self._card("Ollama")
        self.vars["OLLAMA_BASE_URL"] = ctk.StringVar(value=cur["OLLAMA_BASE_URL"])
        ctk.CTkEntry(card, textvariable=self.vars["OLLAMA_BASE_URL"], width=320, fg_color=BG_DARK, border_color=BORDER
                     ).grid(row=1, column=0, padx=12, pady=(0, 6), sticky="w")
        ctk.CTkButton(card, text="Testar conexão", width=140, command=self._test, fg_color=BG_HOVER
                      ).grid(row=1, column=1, padx=6, pady=(0, 6), sticky="w")
        self.conn_lbl = ctk.CTkLabel(card, text="", text_color=TEXT_DIM, font=_font(11), wraplength=720, justify="left")
        self.conn_lbl.grid(row=2, column=0, columnspan=3, padx=12, pady=(0, 4), sticky="w")
        ctk.CTkLabel(card, text="Dica: no Windows, OLLAMA_NUM_PARALLEL=1 faz o modelo caber melhor na GPU "
                                "(o padrão 4 reserva memória para quatro conversas ao mesmo tempo). "
                                "Comando: setx OLLAMA_NUM_PARALLEL 1, depois reinicie o Ollama.",
                     text_color=TEXT_DIM, font=_font(11), wraplength=720, justify="left"
                     ).grid(row=3, column=0, columnspan=3, padx=12, pady=(0, 10), sticky="w")

        # ── Modelos e parâmetros por fase ──
        card = self._card("Modelos por fase")
        heads = ["Fase", "Modelo", "Temperatura", "Contexto (tokens)", "Camadas na GPU"]
        for c, h in enumerate(heads):
            ctk.CTkLabel(card, text=h, text_color=TEXT_DIM, font=_font(11)).grid(row=1, column=c, padx=8, sticky="w")
        for r, (label, key) in enumerate(PHASES, start=2):
            ctk.CTkLabel(card, text=label, text_color=TEXT).grid(row=r, column=0, padx=(12, 6), pady=3, sticky="w")
            mkey = f"MODEL_{key}"
            self.vars[mkey] = ctk.StringVar(value=cur[mkey])
            box = ctk.CTkComboBox(card, values=[], variable=self.vars[mkey], width=210, fg_color=BG_DARK,
                                  border_color=BORDER, command=lambda _v: self._refresh_model_status())
            box.grid(row=r, column=1, padx=6, pady=3, sticky="w")
            self.model_boxes[mkey] = box
            for c, suffix in ((2, "TEMPERATURE"), (3, "NUM_CTX"), (4, "NUM_GPU")):
                k = f"{key}_{suffix}"
                v = cur[k]
                self.vars[k] = ctk.StringVar(value="" if v is None else str(v))
                ctk.CTkEntry(card, textvariable=self.vars[k], width=110, fg_color=BG_DARK, border_color=BORDER,
                             placeholder_text="automático" if suffix == "NUM_GPU" else "").grid(row=r, column=c, padx=6, pady=3, sticky="w")
        self.model_status = ctk.CTkLabel(card, text="", text_color=TEXT_DIM, font=_font(11), wraplength=720, justify="left")
        self.model_status.grid(row=5, column=0, columnspan=5, padx=12, pady=(4, 10), sticky="w")

        # ── Geração ──
        card = self._card("Geração")
        ctk.CTkLabel(card, text="Meta de palavras por capítulo", text_color=TEXT).grid(row=1, column=0, padx=12, pady=3, sticky="w")
        self.vars["CHAPTER_TARGET_WORDS"] = ctk.StringVar(value=str(cur["CHAPTER_TARGET_WORDS"]))
        ctk.CTkEntry(card, textvariable=self.vars["CHAPTER_TARGET_WORDS"], width=110, fg_color=BG_DARK, border_color=BORDER
                     ).grid(row=1, column=1, padx=6, pady=3, sticky="w")
        ctk.CTkLabel(card, text="Timeout por leitura (segundos)", text_color=TEXT).grid(row=2, column=0, padx=12, pady=3, sticky="w")
        self.vars["REQUEST_TIMEOUT"] = ctk.StringVar(value=str(cur["REQUEST_TIMEOUT"]))
        ctk.CTkEntry(card, textvariable=self.vars["REQUEST_TIMEOUT"], width=110, fg_color=BG_DARK, border_color=BORDER
                     ).grid(row=2, column=1, padx=6, pady=3, sticky="w")
        self.vars["CONSISTENCY_CHECK_ENABLED"] = ctk.BooleanVar(value=bool(cur["CONSISTENCY_CHECK_ENABLED"]))
        ctk.CTkCheckBox(card, text="Checagem de consistência depois de cada capítulo",
                        variable=self.vars["CONSISTENCY_CHECK_ENABLED"]).grid(row=3, column=0, columnspan=2, padx=12, pady=(3, 10), sticky="w")

        # ── Pastas ──
        card = self._card("Pastas")
        for r, (label, path) in enumerate([("Dados e projetos", config.DATA_DIR), ("Logs", config.LOG_DIR)], start=1):
            ctk.CTkLabel(card, text=label, text_color=TEXT).grid(row=r, column=0, padx=12, pady=3, sticky="w")
            ctk.CTkLabel(card, text=str(path), text_color=TEXT_DIM, font=_font(11)).grid(row=r, column=1, padx=6, pady=3, sticky="w")
            ctk.CTkButton(card, text="Abrir", width=70, fg_color=BG_HOVER, command=lambda p=path: open_folder(p)
                          ).grid(row=r, column=2, padx=6, pady=3)
        ctk.CTkLabel(card, text=f"Arquivo de log atual: {log_file().name}. Mande este arquivo quando relatar um erro.",
                     text_color=TEXT_DIM, font=_font(11)).grid(row=3, column=0, columnspan=3, padx=12, pady=(0, 10), sticky="w")

        # ── Rodapé ──
        foot = ctk.CTkFrame(self, fg_color="transparent")
        foot.grid(row=1, column=0, sticky="ew", padx=12, pady=(4, 12))
        foot.grid_columnconfigure(0, weight=1)
        self.status_lbl = ctk.CTkLabel(foot, text="", text_color=TEXT_DIM, anchor="w")
        self.status_lbl.grid(row=0, column=0, sticky="ew")
        ctk.CTkButton(foot, text="💾 Salvar", width=120, command=self._save, fg_color=GREEN).grid(row=0, column=1, padx=6)
        ctk.CTkButton(foot, text="Cancelar", width=100, command=self.destroy, fg_color=BG_CARD, hover_color=BG_HOVER).grid(row=0, column=2)

        self._update_note()
        self._test()
        self.after(150, self._grab)

    # ── utilidades ──
    def _grab(self):
        try:
            self.grab_set()
        except Exception:
            pass

    def _card(self, title: str) -> ctk.CTkFrame:
        card = ctk.CTkFrame(self.body, fg_color=BG_CARD, corner_radius=8)
        card.grid(row=len(self.body.winfo_children()), column=0, sticky="ew", pady=6)
        card.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(card, text=title, font=_font(13, True), text_color=TEXT).grid(row=0, column=0, columnspan=5, padx=12, pady=(10, 6), sticky="w")
        return card

    def _profile_key(self) -> str:
        return profile_by_label(self.profile_var.get())

    def _update_note(self):
        self.profile_note.configure(text=PROFILES[self._profile_key()]["note"])

    def _apply_profile(self, _label=None):
        for key, value in PROFILES[self._profile_key()]["values"].items():
            if key in self.vars:
                self.vars[key].set(str(value))
        self._update_note()
        self._refresh_model_status()

    # ── Ollama ──
    def _test(self):
        url = self.vars["OLLAMA_BASE_URL"].get().strip()
        self.conn_lbl.configure(text="Testando…", text_color=TEXT_DIM)

        def work():
            ok = check_ollama_health(timeout=5, base_url=url)
            models = list_installed_models(timeout=5, base_url=url) if ok else []
            try:
                self.after(0, lambda: self._show_test(ok, models))
            except Exception:
                pass

        threading.Thread(target=work, daemon=True).start()

    def _show_test(self, ok: bool, models: list[str]):
        self.installed = models
        if ok:
            self.conn_lbl.configure(text=f"Conectado. {len(models)} modelo(s) instalado(s).", text_color=GREEN)
        else:
            self.conn_lbl.configure(text="Ollama não respondeu neste endereço. Ele está aberto?", text_color=RED)
        for box in self.model_boxes.values():
            box.configure(values=models)
        self._refresh_model_status()

    def _refresh_model_status(self):
        if not self.installed:
            self.model_status.configure(text="")
            return
        chosen = {k: self.vars[k].get().strip() for k in self.model_boxes}
        missing = missing_models(chosen, self.installed)
        if missing:
            cmds = "   ".join(f"ollama pull {m}" for m in missing)
            self.model_status.configure(text=f"Não instalado(s): {', '.join(missing)}. Baixe no terminal: {cmds}", text_color=AMBER)
        else:
            self.model_status.configure(text="Todos os modelos escolhidos estão instalados.", text_color=GREEN)

    # ── salvar ──
    def _save(self):
        values = {k: v.get() for k, v in self.vars.items()}
        values["HARDWARE_PROFILE"] = self._profile_key()
        try:
            config.save_user_settings(values)
        except ValueError as e:
            messagebox.showerror("Configurações", f"Valor inválido.\n\n{e}", parent=self)
            return
        except OSError as e:
            messagebox.showerror("Configurações", f"Não foi possível gravar {config.SETTINGS_PATH}.\n\n{e}", parent=self)
            return
        if self.on_saved:
            self.on_saved()
        self.destroy()
