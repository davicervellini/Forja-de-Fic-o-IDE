"""
gui.py — Interface visual do Pipeline de Ficção (estilo IDE).

Tela inicial (Launcher) para gerenciar projetos + Editor principal
com sidebar, toolbar, abas e status bar.
"""

import logging
import threading
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter

from pipeline.api import check_ollama_health
from pipeline.config import (
    PROJECTS_DIR,
    MODEL_DRAFTING,
    MODEL_REFINING,
    MODEL_SUMMARIZING,
)
from pipeline.io_utils import read_file, write_file
from pipeline.project import StoryProject
from pipeline.orchestrator import PipelineOrchestrator, PipelineCallbacks

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

customtkinter.set_appearance_mode("dark")
customtkinter.set_default_color_theme("blue")

BG_DARK = "#0f1115"
BG_PANEL = "#161a22"
BG_SIDE = "#12151c"
BG_CARD = "#1c212b"
BG_HOVER = "#252b38"
ACCENT = "#3b82f6"
ACCENT_GREEN = "#10b981"
ACCENT_RED = "#ef4444"
ACCENT_AMBER = "#f59e0b"
TEXT = "#e5e7eb"
TEXT_DIM = "#9ca3af"
BORDER = "#2a3140"

STATUS_COLORS = {
    "pending": "#6b7280",
    "drafting": "#f59e0b",
    "polishing": "#3b82f6",
    "summarizing": "#8b5cf6",
    "done": "#10b981",
    "error": "#ef4444",
}
STATUS_ICONS = {
    "pending": "○",
    "drafting": "◐",
    "polishing": "◑",
    "summarizing": "◈",
    "done": "●",
    "error": "✗",
}


class AddChapterDialog(customtkinter.CTkToplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.title("Novo Capítulo")
        self.geometry("640x420")
        self.resizable(True, True)
        self.transient(parent)
        self.grab_set()
        self.configure(fg_color=BG_PANEL)
        self.result = None
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        customtkinter.CTkLabel(self, text="Premissa do capítulo", font=customtkinter.CTkFont(size=15, weight="bold"), text_color=TEXT).grid(row=0, column=0, padx=20, pady=(18, 6), sticky="w")
        self.textbox = customtkinter.CTkTextbox(self, font=customtkinter.CTkFont(family="Consolas", size=13), fg_color=BG_DARK, border_color=BORDER, border_width=1)
        self.textbox.grid(row=1, column=0, padx=20, pady=6, sticky="nsew")
        btn_frame = customtkinter.CTkFrame(self, fg_color="transparent")
        btn_frame.grid(row=2, column=0, padx=20, pady=(8, 18), sticky="ew")
        btn_frame.grid_columnconfigure((0, 1, 2), weight=1)
        customtkinter.CTkButton(btn_frame, text="📂 Arquivo", command=self._load_file, fg_color=BG_CARD, hover_color=BG_HOVER, width=140).grid(row=0, column=0, padx=4)
        customtkinter.CTkButton(btn_frame, text="Adicionar", command=self._confirm, fg_color=ACCENT_GREEN, hover_color="#059669", width=140).grid(row=0, column=1, padx=4)
        customtkinter.CTkButton(btn_frame, text="Cancelar", command=self._cancel, fg_color=BG_CARD, hover_color=BG_HOVER, width=140).grid(row=0, column=2, padx=4)
        self.after(80, self.textbox.focus_set)

    def _load_file(self):
        path = filedialog.askopenfilename(filetypes=[("Texto", "*.txt *.md"), ("Todos", "*.*")])
        if path:
            try:
                self.textbox.delete("0.0", "end")
                self.textbox.insert("0.0", read_file(Path(path)))
            except Exception as e:
                messagebox.showerror("Erro", str(e))

    def _confirm(self):
        text = self.textbox.get("0.0", "end").strip()
        if not text:
            return
        self.result = text
        self.destroy()

    def _cancel(self):
        self.result = None
        self.destroy()


class WikiImportDialog(customtkinter.CTkToplevel):
    def __init__(self, parent, project: StoryProject):
        super().__init__(parent)
        self.project = project
        self.title("Importar da Wiki")
        self.geometry("540x500")
        self.resizable(True, True)
        self.transient(parent)
        self.grab_set()
        self.configure(fg_color=BG_PANEL)
        self._running = False
        self._cancel = threading.Event()
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)
        customtkinter.CTkLabel(self, text="Personagens → Memória Dinâmica", font=customtkinter.CTkFont(size=15, weight="bold"), text_color=TEXT).grid(row=0, column=0, padx=18, pady=(16, 2), sticky="w")
        customtkinter.CTkLabel(self, text="Um nome por linha. Processados em ordem.", text_color=TEXT_DIM, font=customtkinter.CTkFont(size=11)).grid(row=1, column=0, padx=18, pady=(0, 6), sticky="w")
        self.names_tb = customtkinter.CTkTextbox(self, font=customtkinter.CTkFont(size=13), fg_color=BG_DARK, border_color=BORDER, border_width=1, height=140)
        self.names_tb.grid(row=2, column=0, padx=18, pady=4, sticky="nsew")
        frame = customtkinter.CTkFrame(self, fg_color="transparent")
        frame.grid(row=3, column=0, padx=18, pady=8, sticky="ew")
        frame.grid_columnconfigure(1, weight=1)
        customtkinter.CTkLabel(frame, text="Franquia", text_color=TEXT_DIM).grid(row=0, column=0, padx=(0, 8), sticky="e")
        from pipeline.wiki_fetcher import WIKI_DOMAINS
        self.franchise_cb = customtkinter.CTkComboBox(frame, values=list(WIKI_DOMAINS.keys()), fg_color=BG_CARD, border_color=BORDER)
        self.franchise_cb.grid(row=0, column=1, sticky="ew")
        self.status_lbl = customtkinter.CTkLabel(self, text="Pronto.", anchor="w", text_color=TEXT_DIM)
        self.status_lbl.grid(row=4, column=0, padx=18, pady=(4, 2), sticky="ew")
        self.progress = customtkinter.CTkProgressBar(self, progress_color=ACCENT)
        self.progress.grid(row=5, column=0, padx=18, pady=(0, 6), sticky="ew")
        self.progress.set(0)
        self.log_tb = customtkinter.CTkTextbox(self, height=80, font=customtkinter.CTkFont(size=11), fg_color=BG_DARK, border_color=BORDER, border_width=1, state="disabled")
        self.log_tb.grid(row=6, column=0, padx=18, pady=4, sticky="ew")
        btn_frame = customtkinter.CTkFrame(self, fg_color="transparent")
        btn_frame.grid(row=7, column=0, padx=18, pady=(8, 16), sticky="ew")
        btn_frame.grid_columnconfigure((0, 1, 2), weight=1)
        self.btn_start = customtkinter.CTkButton(btn_frame, text="▶ Iniciar fila", command=self._start_queue, fg_color=ACCENT_GREEN, hover_color="#059669")
        self.btn_start.grid(row=0, column=0, padx=4)
        self.btn_cancel = customtkinter.CTkButton(btn_frame, text="⏹ Cancelar", command=self._cancel_queue, fg_color=ACCENT_RED, state="disabled")
        self.btn_cancel.grid(row=0, column=1, padx=4)
        customtkinter.CTkButton(btn_frame, text="Fechar", command=self.destroy, fg_color=BG_CARD, hover_color=BG_HOVER).grid(row=0, column=2, padx=4)

    def _append_log(self, text: str):
        self.log_tb.configure(state="normal")
        self.log_tb.insert("end", text + "\n")
        self.log_tb.see("end")
        self.log_tb.configure(state="disabled")

    def _start_queue(self):
        if self._running:
            return
        raw = self.names_tb.get("0.0", "end").strip()
        if not raw:
            return
        names = []
        for line in raw.replace(",", "\n").splitlines():
            name = line.strip()
            if name and name not in names:
                names.append(name)
        if not names:
            return
        self._cancel.clear()
        self._running = True
        self.btn_start.configure(state="disabled")
        self.btn_cancel.configure(state="normal")
        self.names_tb.configure(state="disabled")
        self.franchise_cb.configure(state="disabled")
        self.progress.set(0)
        self.status_lbl.configure(text=f"Fila: {len(names)} personagem(ns)", text_color=ACCENT_AMBER)
        self._append_log(f"Iniciando fila com {len(names)} personagem(ns)...")
        franchise = self.franchise_cb.get()
        threading.Thread(target=self._worker_queue, args=(names, franchise), daemon=True).start()

    def _cancel_queue(self):
        if self._running:
            self._cancel.set()
            self.status_lbl.configure(text="Cancelando após o atual...", text_color=ACCENT_RED)
            self.btn_cancel.configure(state="disabled")

    def _worker_queue(self, names, franchise):
        from pipeline.wiki_fetcher import add_character_to_memory
        total = len(names)
        success = 0
        for i, char in enumerate(names):
            if self._cancel.is_set():
                self.after(0, lambda: self._finish_queue(f"Cancelado. Processados: {i}/{total}"))
                return
            self.after(0, lambda c=char, idx=i: self.status_lbl.configure(text=f"Processando {idx+1}/{total}: {c}", text_color=ACCENT))
            try:
                msg = add_character_to_memory(char, franchise, self.project.project_dir)
                success += 1
                short = msg.split("\n")[0] if msg else "OK"
                self.after(0, lambda m=f"✓ {char}: {short}": self._append_log(m))
            except Exception as e:
                self.after(0, lambda m=f"✗ {char}: {e}": self._append_log(m))
            self.after(0, lambda v=(i + 1) / total: self.progress.set(v))
        self.after(0, lambda: self._finish_queue(f"Concluído: {success}/{total} adicionados."))

    def _finish_queue(self, final_msg):
        self._running = False
        self.btn_start.configure(state="normal")
        self.btn_cancel.configure(state="disabled")
        self.names_tb.configure(state="normal")
        self.franchise_cb.configure(state="normal")
        self.status_lbl.configure(text=final_msg, text_color=ACCENT_GREEN)
        self._append_log(final_msg)
        try:
            if self.project.dynamic_memory_path.exists():
                self.project.dynamic_memory = read_file(self.project.dynamic_memory_path)
        except Exception:
            pass


class EditorScreen(customtkinter.CTkFrame):
    def __init__(self, parent, project: StoryProject, on_close):
        super().__init__(parent, fg_color=BG_DARK)
        self.project = project
        self.on_close = on_close
        self.queue_items = []
        self.selected_index = None
        self.pipeline_thread = None
        self.cancel_event = threading.Event()
        self.is_running = False
        self._num_to_idx = {}
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(1, weight=1)
        self._build_toolbar()
        self._build_sidebar()
        self._build_main()
        self._build_statusbar()
        self._load_existing_chapters()

    def _build_toolbar(self):
        bar = customtkinter.CTkFrame(self, height=48, fg_color=BG_PANEL, corner_radius=0)
        bar.grid(row=0, column=0, columnspan=2, sticky="ew")
        bar.grid_columnconfigure(1, weight=1)
        bar.grid_propagate(False)
        customtkinter.CTkButton(bar, text="← Projetos", command=self.on_close, width=100, height=30, fg_color="transparent", border_width=1, border_color=BORDER, hover_color=BG_HOVER, font=customtkinter.CTkFont(size=12)).grid(row=0, column=0, padx=(12, 8), pady=9)
        customtkinter.CTkLabel(bar, text=f"⚒  {self.project.name}", font=customtkinter.CTkFont(size=15, weight="bold"), text_color=TEXT).grid(row=0, column=1, sticky="w", padx=4)
        actions = customtkinter.CTkFrame(bar, fg_color="transparent")
        actions.grid(row=0, column=2, padx=12, pady=6)
        customtkinter.CTkButton(actions, text="📜 Akáshico", command=self._import_akashic, width=100, height=28, fg_color=BG_CARD, hover_color=BG_HOVER, font=customtkinter.CTkFont(size=12)).pack(side="left", padx=3)
        customtkinter.CTkButton(actions, text="🌐 Wiki", command=lambda: WikiImportDialog(self, self.project), width=80, height=28, fg_color=BG_CARD, hover_color=BG_HOVER, font=customtkinter.CTkFont(size=12)).pack(side="left", padx=3)
        self.btn_start = customtkinter.CTkButton(actions, text="▶  Run", command=self.start_pipeline, width=90, height=28, fg_color=ACCENT_GREEN, hover_color="#059669", font=customtkinter.CTkFont(size=12, weight="bold"))
        self.btn_start.pack(side="left", padx=3)
        self.btn_cancel = customtkinter.CTkButton(actions, text="⏹", command=self.cancel_pipeline, width=36, height=28, fg_color=ACCENT_RED, state="disabled")
        self.btn_cancel.pack(side="left", padx=3)

    def _build_sidebar(self):
        side = customtkinter.CTkFrame(self, width=300, fg_color=BG_SIDE, corner_radius=0)
        side.grid(row=1, column=0, sticky="nsew")
        side.grid_rowconfigure(3, weight=1)
        side.grid_propagate(False)
        sec = customtkinter.CTkFrame(side, fg_color=BG_CARD, corner_radius=8)
        sec.grid(row=0, column=0, padx=10, pady=(12, 6), sticky="ew")
        sec.grid_columnconfigure(1, weight=1)
        customtkinter.CTkLabel(sec, text="MODELOS", font=customtkinter.CTkFont(size=10, weight="bold"), text_color=TEXT_DIM).grid(row=0, column=0, columnspan=2, padx=10, pady=(8, 4), sticky="w")
        for i, (lbl, attr, val) in enumerate([("Fase 1", "model_draft_entry", MODEL_DRAFTING), ("Fase 2", "model_refine_entry", MODEL_REFINING), ("Fase 3", "model_summ_entry", MODEL_SUMMARIZING)], start=1):
            customtkinter.CTkLabel(sec, text=lbl, font=customtkinter.CTkFont(size=11), text_color=TEXT_DIM).grid(row=i, column=0, padx=(10, 4), pady=2, sticky="w")
            ent = customtkinter.CTkEntry(sec, height=26, font=customtkinter.CTkFont(size=11), fg_color=BG_DARK, border_color=BORDER)
            ent.insert(0, val)
            ent.grid(row=i, column=1, padx=(0, 10), pady=2, sticky="ew")
            setattr(self, attr, ent)
        has_ak = self.project.akashic_model_path.exists() or self.project.akashic_path.exists()
        self.akashic_lbl = customtkinter.CTkLabel(side, text="● Registro Akáshico OK" if has_ak else "○ Sem Registro Akáshico", text_color=ACCENT_GREEN if has_ak else ACCENT_RED, font=customtkinter.CTkFont(size=11))
        self.akashic_lbl.grid(row=1, column=0, padx=14, pady=(4, 8), sticky="w")
        hdr = customtkinter.CTkFrame(side, fg_color="transparent")
        hdr.grid(row=2, column=0, padx=10, pady=(4, 2), sticky="ew")
        hdr.grid_columnconfigure(0, weight=1)
        customtkinter.CTkLabel(hdr, text="CAPÍTULOS", font=customtkinter.CTkFont(size=10, weight="bold"), text_color=TEXT_DIM).grid(row=0, column=0, sticky="w")
        customtkinter.CTkButton(hdr, text="+", width=28, height=24, command=self._add, fg_color=ACCENT_GREEN, hover_color="#059669", font=customtkinter.CTkFont(size=14, weight="bold")).grid(row=0, column=1, padx=(4, 0))
        customtkinter.CTkButton(hdr, text="−", width=28, height=24, command=self._rem, fg_color=BG_CARD, hover_color=ACCENT_RED, font=customtkinter.CTkFont(size=14, weight="bold")).grid(row=0, column=2, padx=(2, 0))
        self.queue_scroll = customtkinter.CTkScrollableFrame(side, fg_color=BG_SIDE, scrollbar_button_color=BORDER)
        self.queue_scroll.grid(row=3, column=0, padx=6, pady=4, sticky="nsew")
        self.queue_scroll.grid_columnconfigure(0, weight=1)

    def _build_main(self):
        main = customtkinter.CTkFrame(self, fg_color=BG_DARK, corner_radius=0)
        main.grid(row=1, column=1, sticky="nsew")
        main.grid_columnconfigure(0, weight=1)
        main.grid_rowconfigure(0, weight=1)
        self.tabview = customtkinter.CTkTabview(main, fg_color=BG_PANEL, segmented_button_fg_color=BG_SIDE, segmented_button_selected_color=ACCENT, segmented_button_selected_hover_color="#2563eb", segmented_button_unselected_color=BG_CARD, segmented_button_unselected_hover_color=BG_HOVER, text_color=TEXT, corner_radius=8)
        self.tabview.grid(row=0, column=0, padx=10, pady=(8, 4), sticky="nsew")
        for t in ["Premissa", "Rascunho", "Capítulo Final", "Resumo", "Roster", "Threads", "Log"]:
            self.tabview.add(t)
        font = customtkinter.CTkFont(family="Consolas", size=13)
        self.premise_tb = customtkinter.CTkTextbox(self.tabview.tab("Premissa"), font=font, fg_color=BG_DARK)
        self.premise_tb.pack(fill="both", expand=True, padx=2, pady=2)
        self.draft_tb = customtkinter.CTkTextbox(self.tabview.tab("Rascunho"), state="disabled", font=font, fg_color=BG_DARK)
        self.draft_tb.pack(fill="both", expand=True, padx=2, pady=2)
        self.final_tb = customtkinter.CTkTextbox(self.tabview.tab("Capítulo Final"), state="disabled", font=font, fg_color=BG_DARK)
        self.final_tb.pack(fill="both", expand=True, padx=2, pady=2)
        self.summary_tb = customtkinter.CTkTextbox(self.tabview.tab("Resumo"), state="disabled", font=font, fg_color=BG_DARK)
        self.summary_tb.pack(fill="both", expand=True, padx=2, pady=2)
        rf = self.tabview.tab("Roster")
        self.roster_tb = customtkinter.CTkTextbox(rf, font=font, fg_color=BG_DARK)
        self.roster_tb.pack(fill="both", expand=True, padx=2, pady=(2, 0))
        customtkinter.CTkButton(rf, text="💾 Salvar Roster", command=self._save_roster, fg_color=ACCENT_GREEN, height=28, width=140).pack(pady=6)
        tf = self.tabview.tab("Threads")
        self.threads_tb = customtkinter.CTkTextbox(tf, font=font, fg_color=BG_DARK)
        self.threads_tb.pack(fill="both", expand=True, padx=2, pady=(2, 0))
        customtkinter.CTkButton(tf, text="💾 Salvar Open Threads", command=self._save_threads, fg_color=ACCENT_GREEN, height=28, width=160).pack(pady=6)
        self.log_tb = customtkinter.CTkTextbox(self.tabview.tab("Log"), state="disabled", font=customtkinter.CTkFont(size=12), fg_color=BG_DARK, text_color=TEXT_DIM)
        self.log_tb.pack(fill="both", expand=True, padx=2, pady=2)

    def _build_statusbar(self):
        bar = customtkinter.CTkFrame(self, height=32, fg_color=BG_PANEL, corner_radius=0)
        bar.grid(row=2, column=0, columnspan=2, sticky="ew")
        bar.grid_columnconfigure(0, weight=1)
        bar.grid_propagate(False)
        self.status_label = customtkinter.CTkLabel(bar, text="Pronto.", anchor="w", font=customtkinter.CTkFont(size=11), text_color=TEXT_DIM)
        self.status_label.grid(row=0, column=0, padx=12, sticky="w")
        self.progress_bar = customtkinter.CTkProgressBar(bar, width=160, height=8, progress_color=ACCENT)
        self.progress_bar.grid(row=0, column=1, padx=12, pady=10)
        self.progress_bar.set(0)

    def _load_existing_chapters(self):
        entries = self.project.scan_chapters()
        for entry in entries:
            self.queue_items.append({"num": entry.num, "title": f"Capítulo {entry.num:02d}", "premise": entry.premise, "status": entry.status, "draft": entry.draft, "final": entry.final, "summary": entry.summary})
        self.roster_tb.delete("0.0", "end")
        if self.project.character_roster:
            self.roster_tb.insert("0.0", self.project.character_roster)
        self.threads_tb.delete("0.0", "end")
        if self.project.open_threads:
            self.threads_tb.insert("0.0", self.project.open_threads)
        if self.queue_items:
            self._refresh_queue_list()
            self._select_queue_item(len(self.queue_items) - 1)

    def _import_akashic(self):
        path = filedialog.askopenfilename(filetypes=[("Markdown", "*.md *.txt"), ("Todos", "*.*")])
        if not path:
            return
        content = read_file(Path(path))
        write_file(self.project.akashic_path, content)
        from pipeline.akashic import build_registro_modelo
        ok, msg = build_registro_modelo(self.project.project_dir)
        if ok:
            self.akashic_lbl.configure(text="● Registro Akáshico OK", text_color=ACCENT_GREEN)
            messagebox.showinfo("Sucesso", f"Registro Akáshico importado.\n\n{msg}")
        else:
            self.akashic_lbl.configure(text="○ Sem Registro Akáshico", text_color=ACCENT_RED)
            messagebox.showerror("Erro", f"Importado, mas falhou ao gerar o modelo:\n{msg}")

    def _add(self):
        if self.is_running:
            return
        d = AddChapterDialog(self)
        self.wait_window(d)
        if d.result:
            num = self.project.next_chapter_num()
            ch_dir = self.project.chapter_dir(num)
            write_file(ch_dir / "premissa.md", d.result)
            self.queue_items.append({"num": num, "title": f"Capítulo {num:02d}", "premise": d.result, "status": "pending", "draft": "", "final": "", "summary": ""})
            self._refresh_queue_list()
            self._select_queue_item(len(self.queue_items) - 1)

    def _rem(self):
        if self.is_running or self.selected_index is None:
            return
        item = self.queue_items[self.selected_index]
        chapter_num = item.get("num")
        if chapter_num is not None:
            if messagebox.askyesno("Confirmar", f"Remover Capítulo {chapter_num:02d} do disco também?"):
                self.project.delete_chapter(chapter_num)
        self.queue_items.pop(self.selected_index)
        self.selected_index = None
        self._refresh_queue_list()

    def _refresh_queue_list(self):
        for w in self.queue_scroll.winfo_children():
            w.destroy()
        for i, item in enumerate(self.queue_items):
            color = STATUS_COLORS.get(item["status"], "#6b7280")
            selected = i == self.selected_index
            btn = customtkinter.CTkButton(self.queue_scroll, text=f"  {STATUS_ICONS.get(item['status'], '○')}  {item['title']}", anchor="w", fg_color=BG_HOVER if selected else "transparent", text_color=color, hover_color=BG_HOVER, height=30, font=customtkinter.CTkFont(size=12), command=lambda idx=i: self._select_queue_item(idx))
            btn.grid(row=i, column=0, padx=2, pady=1, sticky="ew")

    def _select_queue_item(self, idx: int):
        self.selected_index = idx
        item = self.queue_items[idx]
        self.premise_tb.delete("0.0", "end")
        self.premise_tb.insert("0.0", item["premise"])
        self._set_tb(self.draft_tb, item.get("draft", ""))
        self._set_tb(self.final_tb, item.get("final", ""))
        self._set_tb(self.summary_tb, item.get("summary", ""))
        self._refresh_queue_list()

    def _set_tb(self, tb, txt):
        tb.configure(state="normal")
        tb.delete("0.0", "end")
        if txt:
            tb.insert("0.0", txt)
        tb.configure(state="disabled")

    def _append_tb(self, tb, txt):
        tb.configure(state="normal")
        tb.insert("end", txt)
        tb.see("end")
        tb.configure(state="disabled")

    def _log(self, m):
        logger.info(m)
        self._append_tb(self.log_tb, f"{m}\n")

    def _save_roster(self):
        text = self.roster_tb.get("0.0", "end").strip()
        self.project.character_roster = text
        self.project.save_state()
        self._log("Roster salvo.")
        messagebox.showinfo("Salvo", "Character Roster atualizado.")

    def _save_threads(self):
        text = self.threads_tb.get("0.0", "end").strip()
        self.project.open_threads = text
        self.project.save_state()
        self._log("Open Threads salvos.")
        messagebox.showinfo("Salvo", "Open Threads atualizados.")

    def start_pipeline(self):
        if self.is_running or not self.queue_items:
            return
        if self.selected_index is not None:
            self.queue_items[self.selected_index]["premise"] = self.premise_tb.get("0.0", "end").strip()
        if not check_ollama_health():
            messagebox.showerror("Erro", "Ollama não está rodando.")
            return
        self.is_running = True
        self.cancel_event.clear()
        self.btn_start.configure(state="disabled")
        self.btn_cancel.configure(state="normal")
        self.progress_bar.configure(mode="indeterminate")
        self.progress_bar.start()
        self.pipeline_thread = threading.Thread(target=self._worker, daemon=True)
        self.pipeline_thread.start()

    def cancel_pipeline(self):
        if self.is_running:
            self.cancel_event.set()
            self.btn_cancel.configure(state="disabled")

    def _worker(self):
        try:
            model_summ = getattr(self, "model_summ_entry", None)
            model_summ_val = model_summ.get() if model_summ else self.model_draft_entry.get()
            orch = PipelineOrchestrator(project=self.project, cancel_event=self.cancel_event, model_drafting=self.model_draft_entry.get(), model_refining=self.model_refine_entry.get(), model_summarizing=model_summ_val)
            to_process = [i for i in self.queue_items if i["status"] != "done"]
            if not to_process:
                return
            self._num_to_idx = {item["num"]: idx for idx, item in enumerate(self.queue_items)}
            premises = [i["premise"] for i in to_process]
            start_from = to_process[0]["num"]
            cb = PipelineCallbacks(on_status=lambda m: self.after(0, lambda: self.status_label.configure(text=m)), on_token=lambda p, t: self.after(0, lambda: self._on_token(p, t)), on_phase_complete=lambda p, t, c: self.after(0, lambda: self._on_phase(p, t, c)), on_chapter_start=lambda c: self.after(0, lambda: self._on_start(c)), on_chapter_complete=lambda c, r: self.after(0, lambda: self._on_end(c)), on_error=lambda m, c: self.after(0, lambda: self._on_err(m, c)))
            orch.run_batch(premises, cb, start_from=start_from)
        except Exception:
            logger.exception("Worker error")
        finally:
            self.after(0, self._finish)

    def _on_token(self, p, t):
        if p == "drafting":
            self._append_tb(self.draft_tb, t)
            self.tabview.set("Rascunho")
        elif p == "refining":
            self._append_tb(self.final_tb, t)
            self.tabview.set("Capítulo Final")
        elif p == "summarizing":
            self._append_tb(self.summary_tb, t)
            self.tabview.set("Resumo")

    def _on_phase(self, p, t, c):
        idx = self._num_to_idx.get(c)
        if idx is None:
            return
        if p == "drafting":
            self.queue_items[idx]["draft"] = t
            self.queue_items[idx]["status"] = "polishing"
        elif p == "refining":
            self.queue_items[idx]["final"] = t
            self.queue_items[idx]["status"] = "summarizing"
        elif p == "summarizing":
            self.queue_items[idx]["summary"] = t
            self.queue_items[idx]["status"] = "done"
        self._refresh_queue_list()

    def _on_start(self, c):
        idx = self._num_to_idx.get(c)
        if idx is None:
            return
        self.queue_items[idx]["status"] = "drafting"
        self._select_queue_item(idx)
        self._set_tb(self.draft_tb, "")
        self._set_tb(self.final_tb, "")
        self._set_tb(self.summary_tb, "")

    def _on_end(self, c):
        idx = self._num_to_idx.get(c)
        if idx is not None:
            self.queue_items[idx]["status"] = "done"
            self._refresh_queue_list()

    def _on_err(self, m, c):
        if c is not None:
            idx = self._num_to_idx.get(c)
            if idx is not None:
                self.queue_items[idx]["status"] = "error"
        self._refresh_queue_list()
        self._log(f"ERRO: {m}")

    def _finish(self):
        self.is_running = False
        self.btn_start.configure(state="normal")
        self.btn_cancel.configure(state="disabled")
        self.progress_bar.stop()
        self.progress_bar.set(1)
        self.status_label.configure(text="Pipeline parado.")
        self.project.save_state()
        self.roster_tb.delete("0.0", "end")
        if self.project.character_roster:
            self.roster_tb.insert("0.0", self.project.character_roster)
        self.threads_tb.delete("0.0", "end")
        if self.project.open_threads:
            self.threads_tb.insert("0.0", self.project.open_threads)


class LauncherScreen(customtkinter.CTkFrame):
    def __init__(self, parent, on_open_project):
        super().__init__(parent, fg_color=BG_DARK)
        self.on_open_project = on_open_project
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        header = customtkinter.CTkFrame(self, fg_color=BG_PANEL, height=64, corner_radius=0)
        header.grid(row=0, column=0, sticky="ew")
        header.grid_columnconfigure(0, weight=1)
        header.grid_propagate(False)
        customtkinter.CTkLabel(header, text="⚒  Forja de Ficção IDE", font=customtkinter.CTkFont(size=22, weight="bold"), text_color=TEXT).grid(row=0, column=0, padx=24, pady=16, sticky="w")
        customtkinter.CTkButton(header, text="＋  Novo Projeto", command=self._new_project, fg_color=ACCENT_GREEN, hover_color="#059669", width=140, height=34, font=customtkinter.CTkFont(size=13, weight="bold")).grid(row=0, column=1, padx=24, pady=14)
        self.list_frame = customtkinter.CTkScrollableFrame(self, fg_color=BG_DARK, label_text="", scrollbar_button_color=BORDER)
        self.list_frame.grid(row=1, column=0, padx=24, pady=16, sticky="nsew")
        self.list_frame.grid_columnconfigure(0, weight=1)
        self._refresh_list()

    def _refresh_list(self):
        for w in self.list_frame.winfo_children():
            w.destroy()
        projects = StoryProject.list_projects(PROJECTS_DIR)
        if not projects:
            customtkinter.CTkLabel(self.list_frame, text="Nenhum projeto ainda.\nClique em “Novo Projeto” para começar.", text_color=TEXT_DIM, font=customtkinter.CTkFont(size=14)).grid(row=0, column=0, pady=60)
            return
        for i, p in enumerate(projects):
            card = customtkinter.CTkFrame(self.list_frame, fg_color=BG_CARD, corner_radius=10)
            card.grid(row=i, column=0, padx=4, pady=6, sticky="ew")
            card.grid_columnconfigure(0, weight=1)
            customtkinter.CTkLabel(card, text=p["name"], font=customtkinter.CTkFont(size=16, weight="bold"), text_color=TEXT).grid(row=0, column=0, padx=18, pady=(14, 2), sticky="w")
            meta = f"Último cap: {p['last_chapter']}   ·   {p['last_modified'][:10] if p['last_modified'] else 'N/A'}"
            customtkinter.CTkLabel(card, text=meta, text_color=TEXT_DIM, font=customtkinter.CTkFont(size=11)).grid(row=1, column=0, padx=18, pady=(0, 14), sticky="w")
            actions = customtkinter.CTkFrame(card, fg_color="transparent")
            actions.grid(row=0, column=1, rowspan=2, padx=16, sticky="e")
            customtkinter.CTkButton(actions, text="Abrir", command=lambda path=p["path"]: self._open(path), fg_color=ACCENT, hover_color="#2563eb", width=100, height=32).pack(side="left", padx=4)
            customtkinter.CTkButton(actions, text="Excluir", command=lambda path=p["path"], name=p["name"]: self._delete(path, name), fg_color=BG_HOVER, hover_color=ACCENT_RED, width=80, height=32).pack(side="left", padx=4)

    def _new_project(self):
        dialog = customtkinter.CTkInputDialog(text="Nome do novo projeto:", title="Novo Projeto")
        name = dialog.get_input()
        if not name:
            return
        try:
            proj = StoryProject.create(PROJECTS_DIR, name)
            self._open(proj.project_dir)
        except Exception as e:
            messagebox.showerror("Erro", str(e))

    def _delete(self, path, name):
        if messagebox.askyesno("Confirmar", f"Excluir “{name}” para sempre?"):
            StoryProject.delete_project(path)
            self._refresh_list()

    def _open(self, path):
        proj = StoryProject.load(Path(path))
        self.on_open_project(proj)


class AppRoot(customtkinter.CTk):
    def __init__(self):
        super().__init__()
        self.title("Forja de Ficção IDE")
        self.geometry("1380x820")
        self.minsize(1100, 680)
        self.configure(fg_color=BG_DARK)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self.current_screen = None
        self.show_launcher()

    def show_launcher(self):
        if self.current_screen:
            self.current_screen.destroy()
        self.current_screen = LauncherScreen(self, self.show_editor)
        self.current_screen.grid(row=0, column=0, sticky="nsew")

    def show_editor(self, project: StoryProject):
        if self.current_screen:
            self.current_screen.destroy()
        self.current_screen = EditorScreen(self, project, self.show_launcher)
        self.current_screen.grid(row=0, column=0, sticky="nsew")


if __name__ == "__main__":
    AppRoot().mainloop()
