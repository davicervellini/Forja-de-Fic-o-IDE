"""
gui.py — Interface visual do Pipeline de Ficção com CustomTkinter.

Inclui uma tela inicial (Launcher) para gerenciar projetos,
e o editor principal para o projeto selecionado.
"""

import logging
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter

from pipeline.api import check_ollama_health
from pipeline.config import (
    PROJECTS_DIR,
    MODEL_DRAFTING,
    MODEL_REFINING,
    MODEL_SUMMARIZING,
    DRAFTING_TEMPERATURE,
    REFINING_TEMPERATURE,
    REQUEST_TIMEOUT,
)
from pipeline.io_utils import read_file
from pipeline.project import StoryProject
from pipeline.orchestrator import PipelineOrchestrator, PipelineCallbacks, ChapterResult

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

customtkinter.set_appearance_mode("dark")
customtkinter.set_default_color_theme("blue")

STATUS_COLORS = {
    "pending": "#6b7280",
    "drafting": "#f59e0b",
    "polishing": "#3b82f6",
    "summarizing": "#8b5cf6",
    "done": "#10b981",
    "error": "#ef4444",
}
STATUS_ICONS = {
    "pending": "○", "drafting": "◐", "polishing": "◑",
    "summarizing": "◈", "done": "●", "error": "✗",
}


# ═══════════════════════════════════════════════════════════════
# DIÁLOGOS
# ═══════════════════════════════════════════════════════════════

class AddChapterDialog(customtkinter.CTkToplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.title("Adicionar Capítulo")
        self.geometry("600x450")
        self.resizable(True, True)
        self.transient(parent)
        self.grab_set()

        self.result: str | None = None
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        customtkinter.CTkLabel(
            self, text="Escreva ou cole a premissa do capítulo:",
            font=customtkinter.CTkFont(size=14, weight="bold")
        ).grid(row=0, column=0, padx=16, pady=(16, 8), sticky="w")

        self.textbox = customtkinter.CTkTextbox(self, font=customtkinter.CTkFont(size=13))
        self.textbox.grid(row=1, column=0, padx=16, pady=4, sticky="nsew")

        btn_frame = customtkinter.CTkFrame(self, fg_color="transparent")
        btn_frame.grid(row=2, column=0, padx=16, pady=(8, 16), sticky="ew")
        btn_frame.grid_columnconfigure((0, 1, 2), weight=1)

        customtkinter.CTkButton(
            btn_frame, text="📂 Carregar Arquivo", command=self._load_file, width=160
        ).grid(row=0, column=0, padx=4)

        customtkinter.CTkButton(
            btn_frame, text="✓ Adicionar", command=self._confirm,
            fg_color="#10b981", hover_color="#059669", width=160
        ).grid(row=0, column=1, padx=4)

        customtkinter.CTkButton(
            btn_frame, text="✗ Cancelar", command=self._cancel,
            fg_color="#6b7280", hover_color="#4b5563", width=160
        ).grid(row=0, column=2, padx=4)

        self.after(100, self.textbox.focus_set)

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
        if not text: return
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
        self.geometry("450x300")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1)

        customtkinter.CTkLabel(
            self, text="Buscar personagem para a Memória Dinâmica",
            font=customtkinter.CTkFont(size=14, weight="bold")
        ).grid(row=0, column=0, padx=16, pady=(16, 8), sticky="w")

        frame = customtkinter.CTkFrame(self, fg_color="transparent")
        frame.grid(row=1, column=0, padx=16, pady=4, sticky="ew")
        frame.grid_columnconfigure(1, weight=1)

        customtkinter.CTkLabel(frame, text="Nome:").grid(row=0, column=0, padx=(0,8), pady=8, sticky="e")
        self.name_entry = customtkinter.CTkEntry(frame)
        self.name_entry.grid(row=0, column=1, sticky="ew", pady=8)

        customtkinter.CTkLabel(frame, text="Franquia:").grid(row=1, column=0, padx=(0,8), pady=8, sticky="e")
        
        from pipeline.wiki_fetcher import WIKI_DOMAINS
        self.franchise_cb = customtkinter.CTkComboBox(frame, values=list(WIKI_DOMAINS.keys()))
        self.franchise_cb.grid(row=1, column=1, sticky="ew", pady=8)

        self.status_lbl = customtkinter.CTkLabel(self, text="")
        self.status_lbl.grid(row=2, column=0, padx=16, pady=4, sticky="w")

        btn_frame = customtkinter.CTkFrame(self, fg_color="transparent")
        btn_frame.grid(row=3, column=0, padx=16, pady=16, sticky="sew")
        btn_frame.grid_columnconfigure((0, 1), weight=1)

        self.btn_import = customtkinter.CTkButton(
            btn_frame, text="🔍 Buscar", command=self._start, fg_color="#3b82f6"
        )
        self.btn_import.grid(row=0, column=0, padx=4)
        self.btn_cancel = customtkinter.CTkButton(
            btn_frame, text="Cancelar", command=self.destroy, fg_color="#6b7280"
        )
        self.btn_cancel.grid(row=0, column=1, padx=4)

    def _start(self):
        char = self.name_entry.get().strip()
        if not char: return
        self.status_lbl.configure(text="Baixando (aguarde)...", text_color="#f59e0b")
        self.btn_import.configure(state="disabled")
        threading.Thread(target=self._worker, args=(char, self.franchise_cb.get()), daemon=True).start()

    def _worker(self, char, franchise):
        from pipeline.wiki_fetcher import add_character_to_memory
        try:
            msg = add_character_to_memory(char, franchise, self.project.project_dir)
            self.after(0, lambda: self._success(msg))
        except Exception as e:
            self.after(0, lambda e=str(e): self._error(e))

    def _success(self, msg):
        messagebox.showinfo("Sucesso", msg)
        self.project.dynamic_memory = read_file(self.project.dynamic_memory_path)
        self.destroy()

    def _error(self, err):
        self.status_lbl.configure(text="Erro.", text_color="#ef4444")
        messagebox.showerror("Erro", err)
        self.btn_import.configure(state="normal")


# ═══════════════════════════════════════════════════════════════
# TELA PRINCIPAL DO EDITOR
# ═══════════════════════════════════════════════════════════════

class EditorScreen(customtkinter.CTkFrame):
    def __init__(self, parent, project: StoryProject, on_close: callable):
        super().__init__(parent)
        self.project = project
        self.on_close = on_close

        self.queue_items: list[dict] = []
        self.selected_index: int | None = None
        self.pipeline_thread: threading.Thread | None = None
        self.cancel_event = threading.Event()
        self.is_running = False

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self._create_sidebar()
        self._create_main_content()
        self._load_existing_chapters()

    def _load_existing_chapters(self):
        """Carrega os capítulos existentes do disco (persistência)."""
        entries = self.project.scan_chapters()
        for entry in entries:
            self.queue_items.append({
                "num": entry.num,
                "title": f"Capítulo {entry.num:02d}",
                "premise": entry.premise,
                "status": entry.status,
                "draft": entry.draft,
                "final": entry.final,
                "summary": entry.summary,
            })
        if self.queue_items:
            self._refresh_queue_list()
            self._select_queue_item(len(self.queue_items) - 1)

    def _create_sidebar(self):
        sidebar = customtkinter.CTkFrame(self, width=320, corner_radius=0)
        sidebar.grid(row=0, column=0, sticky="nsew")
        sidebar.grid_rowconfigure(4, weight=1)
        sidebar.grid_propagate(False)

        # Header
        customtkinter.CTkLabel(
            sidebar, text=f"⚒ {self.project.name}", font=customtkinter.CTkFont(size=20, weight="bold")
        ).grid(row=0, column=0, padx=16, pady=(16, 4), sticky="w")
        
        customtkinter.CTkButton(
            sidebar, text="← Voltar aos Projetos", command=self.on_close, 
            fg_color="transparent", border_width=1, height=24
        ).grid(row=1, column=0, padx=16, pady=(0, 12), sticky="w")

        # Config Frame
        cfg = customtkinter.CTkFrame(sidebar)
        cfg.grid(row=2, column=0, padx=12, pady=(0, 8), sticky="ew")
        cfg.grid_columnconfigure(1, weight=1)

        fields = [
            ("Mod. Fase 1:", "model_draft_entry", MODEL_DRAFTING),
            ("Mod. Fase 2:", "model_refine_entry", MODEL_REFINING),
            ("Mod. Fase 3:", "model_summ_entry", MODEL_SUMMARIZING),
        ]
        for i, (lbl, attr, val) in enumerate(fields):
            customtkinter.CTkLabel(cfg, text=lbl, font=customtkinter.CTkFont(size=11)).grid(row=i, column=0, padx=4, sticky="w")
            ent = customtkinter.CTkEntry(cfg, height=24, font=customtkinter.CTkFont(size=11))
            ent.insert(0, val)
            ent.grid(row=i, column=1, padx=4, pady=2, sticky="ew")
            setattr(self, attr, ent)

        customtkinter.CTkButton(
            cfg, text="📜 Importar Registro Akáshico", command=self._import_akashic, height=28,
        ).grid(row=3, column=0, columnspan=2, padx=4, pady=4, sticky="ew")

        customtkinter.CTkButton(
            cfg, text="🌐 Importar da Wiki", command=lambda: WikiImportDialog(self, self.project),
            height=28, fg_color="#3b82f6", hover_color="#2563eb",
        ).grid(row=4, column=0, columnspan=2, padx=4, pady=(0,4), sticky="ew")

        has_akashic = self.project.akashic_model_path.exists() or self.project.akashic_path.exists()
        self.akashic_lbl = customtkinter.CTkLabel(
            cfg, text="● Registro Ok" if has_akashic else "○ Sem Registro",
            text_color="#10b981" if has_akashic else "#ef4444", font=customtkinter.CTkFont(size=10)
        )
        self.akashic_lbl.grid(row=5, column=0, columnspan=2, padx=4, pady=(0,4), sticky="w")

        # Fila
        customtkinter.CTkLabel(
            sidebar, text="📋 Fila de Capítulos", font=customtkinter.CTkFont(size=13, weight="bold")
        ).grid(row=3, column=0, padx=16, pady=(8, 4), sticky="w")

        self.queue_scroll = customtkinter.CTkScrollableFrame(sidebar, label_text="")
        self.queue_scroll.grid(row=4, column=0, padx=12, pady=4, sticky="nsew")
        self.queue_scroll.grid_columnconfigure(0, weight=1)

        q_btns = customtkinter.CTkFrame(sidebar, fg_color="transparent")
        q_btns.grid(row=5, column=0, padx=12, pady=4, sticky="ew")
        q_btns.grid_columnconfigure((0, 1), weight=1)
        
        customtkinter.CTkButton(
            q_btns, text="＋ Add", command=self._add, fg_color="#10b981", height=28
        ).grid(row=0, column=0, padx=(0,2), sticky="ew")
        customtkinter.CTkButton(
            q_btns, text="— Rem", command=self._rem, fg_color="#ef4444", height=28
        ).grid(row=0, column=1, padx=(2,0), sticky="ew")

        # Controls
        ctl = customtkinter.CTkFrame(sidebar, fg_color="transparent")
        ctl.grid(row=6, column=0, padx=12, pady=16, sticky="ew")
        ctl.grid_columnconfigure(0, weight=1)

        self.btn_start = customtkinter.CTkButton(
            ctl, text="▶ Iniciar", command=self.start_pipeline, fg_color="#10b981", height=40, font=customtkinter.CTkFont(weight="bold")
        )
        self.btn_start.grid(row=0, column=0, pady=(0,4), sticky="ew")
        
        self.btn_cancel = customtkinter.CTkButton(
            ctl, text="⏹ Cancelar", command=self.cancel_pipeline, fg_color="#ef4444", state="disabled", height=32
        )
        self.btn_cancel.grid(row=1, column=0, sticky="ew")

    def _create_main_content(self):
        main = customtkinter.CTkFrame(self, corner_radius=0, fg_color="transparent")
        main.grid(row=0, column=1, sticky="nsew", padx=(4, 0))
        main.grid_columnconfigure(0, weight=1)
        main.grid_rowconfigure(0, weight=1)

        self.tabview = customtkinter.CTkTabview(main)
        self.tabview.grid(row=0, column=0, padx=12, pady=(12, 4), sticky="nsew")

        for t in ["Premissa", "Rascunho", "Capítulo Final", "Resumo", "Log"]:
            self.tabview.add(t)

        self.premise_tb = customtkinter.CTkTextbox(self.tabview.tab("Premissa"), font=customtkinter.CTkFont("Consolas", 13))
        self.premise_tb.pack(fill="both", expand=True)

        self.draft_tb = customtkinter.CTkTextbox(self.tabview.tab("Rascunho"), state="disabled", font=customtkinter.CTkFont("Consolas", 13))
        self.draft_tb.pack(fill="both", expand=True)

        self.final_tb = customtkinter.CTkTextbox(self.tabview.tab("Capítulo Final"), state="disabled", font=customtkinter.CTkFont("Consolas", 13))
        self.final_tb.pack(fill="both", expand=True)

        self.summary_tb = customtkinter.CTkTextbox(self.tabview.tab("Resumo"), state="disabled", font=customtkinter.CTkFont("Consolas", 13))
        self.summary_tb.pack(fill="both", expand=True)

        self.log_tb = customtkinter.CTkTextbox(self.tabview.tab("Log"), state="disabled", text_color="#d1d5db")
        self.log_tb.pack(fill="both", expand=True)

        status_frame = customtkinter.CTkFrame(main, height=60)
        status_frame.grid(row=1, column=0, padx=12, pady=(0, 12), sticky="sew")
        status_frame.grid_columnconfigure(0, weight=1)

        self.status_label = customtkinter.CTkLabel(status_frame, text="Pronto.", anchor="w")
        self.status_label.grid(row=0, column=0, padx=12, pady=(8, 2), sticky="ew")

        self.progress_bar = customtkinter.CTkProgressBar(status_frame)
        self.progress_bar.grid(row=1, column=0, padx=12, pady=(2, 4), sticky="ew")
        self.progress_bar.set(0)

    # ── Métodos ──
    def _import_akashic(self):
        path = filedialog.askopenfilename(filetypes=[("Markdown", "*.md *.txt"), ("Todos", "*.*")])
        if path:
            from pipeline.io_utils import write_file
            content = read_file(Path(path))
            write_file(self.project.akashic_path, content)
            self.akashic_lbl.configure(text="● Registro Ok", text_color="#10b981")
            messagebox.showinfo("Sucesso", "Registro Akáshico importado. Rode o build script para gerar a versão curta.")

    def _add(self):
        if self.is_running: return
        d = AddChapterDialog(self)
        self.wait_window(d)
        if d.result:
            num = self.project.next_chapter_num()
            # Persiste a premissa imediatamente no disco
            from pipeline.io_utils import write_file
            ch_dir = self.project.chapter_dir(num)
            write_file(ch_dir / "premissa.md", d.result)
            self.queue_items.append({
                "num": num,
                "title": f"Capítulo {num:02d}",
                "premise": d.result,
                "status": "pending",
                "draft": "",
                "final": "",
                "summary": "",
            })
            self._refresh_queue_list()
            self._select_queue_item(len(self.queue_items)-1)

    def _rem(self):
        if self.is_running or self.selected_index is None: return
        item = self.queue_items[self.selected_index]
        chapter_num = item.get("num")
        if chapter_num is not None:
            # Confirma e remove do disco
            from tkinter import messagebox
            if messagebox.askyesno("Confirmar", f"Remover Capítulo {chapter_num:02d} do disco também?"):
                self.project.delete_chapter(chapter_num)
        self.queue_items.pop(self.selected_index)
        self.selected_index = None
        self._refresh_queue_list()

    def _refresh_queue_list(self):
        for w in self.queue_scroll.winfo_children(): w.destroy()
        for i, item in enumerate(self.queue_items):
            color = STATUS_COLORS.get(item["status"], "#6b7280")
            btn = customtkinter.CTkButton(
                self.queue_scroll, text=f"  {STATUS_ICONS.get(item['status'])}  {item['title']}",
                anchor="w", fg_color="#374151" if i == self.selected_index else "transparent",
                text_color=color, hover_color="#374151", command=lambda idx=i: self._select_queue_item(idx)
            )
            btn.grid(row=i, column=0, padx=2, pady=1, sticky="ew")

    def _select_queue_item(self, idx):
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
        if txt: tb.insert("0.0", txt)
        tb.configure(state="disabled")

    def _append_tb(self, tb, txt):
        tb.configure(state="normal")
        tb.insert("end", txt)
        tb.see("end")
        tb.configure(state="disabled")

    def _log(self, m):
        logger.info(m)
        self._append_tb(self.log_tb, f"{m}\n")

    def start_pipeline(self):
        if self.is_running or not self.queue_items: return
        
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
            # Usa o campo de sumarização se existir, senão fallback
            model_summ = getattr(self, "model_summ_entry", None)
            model_summ_val = model_summ.get() if model_summ else self.model_draft_entry.get()

            orch = PipelineOrchestrator(
                project=self.project,
                cancel_event=self.cancel_event,
                model_drafting=self.model_draft_entry.get(),
                model_refining=self.model_refine_entry.get(),
                model_summarizing=model_summ_val,
            )

            # Só processa do primeiro não-done em diante
            to_process = [i for i in self.queue_items if i["status"] != "done"]
            if not to_process:
                return

            # Mapa chapter_num -> índice na fila para callbacks seguros
            self._num_to_idx = {item["num"]: idx for idx, item in enumerate(self.queue_items)}

            premises = [i["premise"] for i in to_process]
            start_from = to_process[0]["num"]

            cb = PipelineCallbacks(
                on_status=lambda m: self.after(0, lambda: self.status_label.configure(text=m)),
                on_token=lambda p, t: self.after(0, lambda: self._on_token(p, t)),
                on_phase_complete=lambda p, t, c: self.after(0, lambda: self._on_phase(p, t, c)),
                on_chapter_start=lambda c: self.after(0, lambda: self._on_start(c)),
                on_chapter_complete=lambda c, r: self.after(0, lambda: self._on_end(c)),
                on_error=lambda m, c: self.after(0, lambda: self._on_err(m, c)),
            )
            orch.run_batch(premises, cb, start_from=start_from)
        except Exception as e:
            logger.exception("Worker error")
        finally:
            self.after(0, self._finish)

    def _on_token(self, p, t):
        if p == "drafting": self._append_tb(self.draft_tb, t); self.tabview.set("Rascunho")
        elif p == "refining": self._append_tb(self.final_tb, t); self.tabview.set("Capítulo Final")
        elif p == "summarizing": self._append_tb(self.summary_tb, t); self.tabview.set("Resumo")

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


# ═══════════════════════════════════════════════════════════════
# LAUNCHER (TELA INICIAL)
# ═══════════════════════════════════════════════════════════════

class LauncherScreen(customtkinter.CTkFrame):
    def __init__(self, parent, on_open_project):
        super().__init__(parent)
        self.on_open_project = on_open_project
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        # Header
        header = customtkinter.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, padx=24, pady=(24, 12), sticky="ew")
        header.grid_columnconfigure(0, weight=1)

        customtkinter.CTkLabel(
            header, text="⚒ Forja de Ficção IDE", font=customtkinter.CTkFont(size=28, weight="bold")
        ).grid(row=0, column=0, sticky="w")

        customtkinter.CTkButton(
            header, text="＋ Novo Projeto", command=self._new_project,
            fg_color="#10b981", hover_color="#059669", width=140, height=36
        ).grid(row=0, column=1, sticky="e")

        # Lista de projetos
        self.list_frame = customtkinter.CTkScrollableFrame(self, label_text="Seus Projetos")
        self.list_frame.grid(row=1, column=0, padx=24, pady=12, sticky="nsew")
        self.list_frame.grid_columnconfigure(0, weight=1)

        self._refresh_list()

    def _refresh_list(self):
        for w in self.list_frame.winfo_children():
            w.destroy()

        projects = StoryProject.list_projects(PROJECTS_DIR)
        if not projects:
            customtkinter.CTkLabel(
                self.list_frame, text="Nenhum projeto ainda.\nClique em \"+ Novo Projeto\" para começar.",
                text_color="#9ca3af", font=customtkinter.CTkFont(size=14)
            ).grid(row=0, column=0, pady=40)
            return

        for i, p in enumerate(projects):
            card = customtkinter.CTkFrame(self.list_frame)
            card.grid(row=i, column=0, padx=8, pady=6, sticky="ew")
            card.grid_columnconfigure(0, weight=1)

            customtkinter.CTkLabel(
                card, text=p["name"], font=customtkinter.CTkFont(size=16, weight="bold")
            ).grid(row=0, column=0, padx=16, pady=(12, 2), sticky="w")
            
            customtkinter.CTkLabel(
                card, text=f"Último cap: {p['last_chapter']} | Modificado: {p['last_modified'][:10] if p['last_modified'] else 'N/A'}",
                text_color="#9ca3af"
            ).grid(row=1, column=0, padx=16, pady=(0,16), sticky="w")

            actions = customtkinter.CTkFrame(card, fg_color="transparent")
            actions.grid(row=0, column=2, rowspan=2, padx=16, sticky="e")

            customtkinter.CTkButton(
                actions, text="Abrir Projeto", command=lambda path=p["path"]: self._open(path),
                fg_color="#3b82f6", width=120
            ).pack(side="left", padx=4)

            customtkinter.CTkButton(
                actions, text="Excluir", command=lambda path=p["path"], name=p["name"]: self._delete(path, name),
                fg_color="#ef4444", width=80
            ).pack(side="left", padx=4)

    def _new_project(self):
        dialog = customtkinter.CTkInputDialog(text="Nome do novo projeto:", title="Novo Projeto")
        name = dialog.get_input()
        if not name: return
        
        try:
            proj = StoryProject.create(PROJECTS_DIR, name)
            self._open(proj.project_dir)
        except Exception as e:
            messagebox.showerror("Erro", str(e))

    def _delete(self, path, name):
        if messagebox.askyesno("Confirmar", f"Excluir '{name}' para sempre?"):
            StoryProject.delete_project(path)
            self._refresh_list()

    def _open(self, path):
        proj = StoryProject.load(Path(path))
        self.on_open_project(proj)


class AppRoot(customtkinter.CTk):
    def __init__(self):
        super().__init__()
        self.title("Forja de Ficção IDE")
        self.geometry("1320x780")
        self.minsize(1000, 650)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)
        
        self.current_screen = None
        self.show_launcher()

    def show_launcher(self):
        if self.current_screen: self.current_screen.destroy()
        self.current_screen = LauncherScreen(self, self.show_editor)
        self.current_screen.grid(row=0, column=0, sticky="nsew")

    def show_editor(self, project: StoryProject):
        if self.current_screen: self.current_screen.destroy()
        self.current_screen = EditorScreen(self, project, self.show_launcher)
        self.current_screen.grid(row=0, column=0, sticky="nsew")

if __name__ == "__main__":
    AppRoot().mainloop()
