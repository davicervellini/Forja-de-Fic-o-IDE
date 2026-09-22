"""
server.py — API local da Forja de Ficção (FastAPI) e arquivos da interface web.

Roda só em 127.0.0.1, dentro do próprio programa (app_desktop.py abre a janela).
Projetos são identificados pelo nome da pasta dentro de `projetos/`.
"""

import json
import logging
import queue
import threading
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from pipeline import chapters as ch
from pipeline import config
from pipeline.akashic import build_registro_modelo
from pipeline.api import check_ollama_health, list_installed_models
from pipeline.export import FORMATS, default_filename, export_project
from pipeline.io_utils import read_file, write_file
from pipeline.orchestrator import PipelineOrchestrator
from pipeline.profiles import PROFILES, missing_models
from pipeline.project import StoryProject
from pipeline.scenes import parse_premise
from webapp.jobs import JobManager

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"

# Arquivos extras que a tela do capítulo mostra, quando existem.
EXTRA_FILES = {
    "consistency": "consistencia.md",
    "rejected_polish": "polimento_descartado.md",
    "planned_scenes": "cenas_planejadas.md",
}


# ── Corpos das requisições ───────────────────────────────────

class NewProject(BaseModel):
    name: str
    akashic_text: str = ""


class TextBody(BaseModel):
    text: str


class NewChapter(BaseModel):
    premise: str


class GenerateBody(BaseModel):
    chapters: list[int] | None = None


class RedoBody(BaseModel):
    premise: str | None = None


class StateBody(BaseModel):
    character_roster: str | None = None
    open_threads: str | None = None
    dynamic_memory: str | None = None
    story_so_far: str | None = None


class ProjectMeta(BaseModel):
    book_title: str | None = None
    author: str | None = None
    language: str | None = None


class ExportBody(BaseModel):
    format: str = "epub"
    first: int | None = None
    last: int | None = None
    path: str | None = None


class SettingsBody(BaseModel):
    values: dict[str, Any]


# ── Aplicação ────────────────────────────────────────────────

def create_app(manager: JobManager | None = None) -> FastAPI:
    app = FastAPI(title="Forja de Ficção", docs_url=None, redoc_url=None)
    jobs: JobManager = manager or JobManager()
    app.state.jobs = jobs

    def projects_root() -> Path:
        return Path(config.PROJECTS_DIR)

    def project_dir(slug: str) -> Path:
        root = projects_root().resolve()
        d = (root / slug).resolve()
        # O nome vem da URL: nunca pode apontar para fora da pasta de projetos.
        if d.parent != root or not (d / "projeto.json").exists():
            raise HTTPException(404, f"Projeto não encontrado: {slug}")
        return d

    def load(slug: str) -> StoryProject:
        return StoryProject.load(project_dir(slug))

    def ensure_idle(slug: str | None = None):
        if jobs.busy and (slug is None or (jobs.job and jobs.job.project == slug)):
            raise HTTPException(409, "Há uma geração em andamento neste projeto. Espere terminar ou cancele.")

    def chapter_exists(project: StoryProject, num: int):
        if not project.chapter_dir(num).is_dir():
            raise HTTPException(404, f"Capítulo {num:02d} não existe.")

    # ── Status e configurações ───────────────────────────────

    @app.get("/api/status")
    def status():
        return {
            "ollama": check_ollama_health(timeout=3),
            "job": jobs.state(),
            "models": {
                "drafting": config.MODEL_DRAFTING,
                "refining": config.MODEL_REFINING,
                "summarizing": config.MODEL_SUMMARIZING,
            },
            "data_dir": str(config.DATA_DIR),
        }

    @app.get("/api/settings")
    def get_settings():
        from pipeline.logsetup import log_file
        return {
            "values": config.current_settings(),
            "keys": config.UI_KEYS,
            "profiles": PROFILES,
            "data_dir": str(config.DATA_DIR),
            "log_file": str(log_file()),
        }

    @app.put("/api/settings")
    def put_settings(body: SettingsBody):
        ensure_idle()
        try:
            config.save_user_settings(body.values)
        except ValueError as e:
            raise HTTPException(400, str(e))
        return {"values": config.current_settings()}

    @app.get("/api/ollama")
    def ollama(base_url: str | None = None):
        online = check_ollama_health(timeout=3, base_url=base_url)
        models = list_installed_models(timeout=5, base_url=base_url) if online else []
        wanted = {k: v for k, v in config.current_settings().items() if k.startswith("MODEL_")}
        return {"online": online, "models": models, "missing": missing_models(wanted, models) if online else []}

    # ── Projetos ─────────────────────────────────────────────

    @app.get("/api/projects")
    def list_projects():
        out = []
        for p in StoryProject.list_projects(projects_root()):
            out.append({
                "slug": Path(p["path"]).name,
                "name": p["name"],
                "last_chapter": p["last_chapter"],
                "last_modified": p["last_modified"],
                "created": p["created"],
            })
        out.sort(key=lambda p: p["last_modified"] or "", reverse=True)
        return out

    @app.post("/api/projects")
    def create_project(body: NewProject):
        name = body.name.strip()
        if not name:
            raise HTTPException(400, "Dê um nome ao projeto.")
        try:
            project = StoryProject.create(projects_root(), name)
        except FileExistsError:
            raise HTTPException(409, "Já existe um projeto com esse nome.")
        msg = ""
        if body.akashic_text.strip():
            write_file(project.akashic_path, body.akashic_text)
            _, msg = build_registro_modelo(project.project_dir)
        return {"slug": project.project_dir.name, "message": msg}

    @app.delete("/api/projects/{slug}")
    def delete_project(slug: str):
        ensure_idle(slug)
        dest = StoryProject.trash_project(project_dir(slug))
        return {"message": f"Projeto movido para {dest.parent.name}/{dest.name}."}

    @app.get("/api/projects/{slug}")
    def get_project(slug: str):
        project = load(slug)
        chapters = []
        for e in project.scan_chapters():
            info = ch.read_info(project, e.num)
            text = e.final or e.draft
            chapters.append({
                "num": e.num,
                "title": ch.chapter_title(text, e.num) if text
                else parse_premise(e.premise).title or f"Capítulo {e.num:02d}",
                "status": e.status,
                "words": len(text.split()),
                "has_final": bool(e.final.strip()),
                "memory_stale": bool(info.get("memory_stale")),
                "edited": bool(info.get("edited")),
            })
        return {
            "slug": slug,
            "name": project.name,
            "meta": {k: project.metadata.get(k, "") for k in ("book_title", "author", "language")},
            "chapters": chapters,
            "next_chapter": project.next_chapter_num(),
            "akashic": project.akashic_path.exists(),
            "state": {
                "character_roster": project.character_roster,
                "open_threads": project.open_threads,
                "dynamic_memory": project.dynamic_memory,
                "story_so_far": project.story_so_far,
                "summaries": [{"num": n, "text": t} for n, t in project.accumulated_summaries],
            },
        }

    @app.put("/api/projects/{slug}/meta")
    def put_meta(slug: str, body: ProjectMeta):
        project = load(slug)
        for key, value in body.model_dump(exclude_none=True).items():
            project.metadata[key] = value.strip()
        project._save_metadata()
        return {"ok": True}

    @app.put("/api/projects/{slug}/state")
    def put_state(slug: str, body: StateBody):
        ensure_idle(slug)
        project = load(slug)
        for key, value in body.model_dump(exclude_none=True).items():
            setattr(project, key, value.strip())
        project.save_state()
        return {"ok": True}

    @app.get("/api/projects/{slug}/akashic")
    def get_akashic(slug: str):
        project = load(slug)
        text = read_file(project.akashic_path) if project.akashic_path.exists() else ""
        return {"text": text}

    @app.put("/api/projects/{slug}/akashic")
    def put_akashic(slug: str, body: TextBody):
        ensure_idle(slug)
        project = load(slug)
        if project.akashic_path.exists():
            # Cópia de segurança antes de gravar por cima do registro inteiro.
            write_file(project.project_dir / "registro_akashico.anterior.md", read_file(project.akashic_path))
        write_file(project.akashic_path, body.text)
        ok, msg = build_registro_modelo(project.project_dir)
        if not ok:
            raise HTTPException(400, msg)
        return {"message": msg}

    # ── Capítulos ────────────────────────────────────────────

    @app.get("/api/projects/{slug}/chapters/{num}")
    def get_chapter(slug: str, num: int):
        project = load(slug)
        chapter_exists(project, num)
        d = project.chapter_dir(num)

        def txt(name: str) -> str:
            return read_file(d / name) if (d / name).exists() else ""

        info = ch.read_info(project, num)
        later = ch.later_done(project, num)
        return {
            "num": num,
            "premise": txt("premissa.md"),
            "draft": txt("rascunho.md"),
            "final": txt("capitulo_final.md"),
            "summary": txt("resumo.md"),
            "extras": {k: txt(v) for k, v in EXTRA_FILES.items()},
            "info": info,
            "later_done": later,
            "has_snapshot": ch.has_snapshot(project, num),
            "versions": ch.list_versions(project, num),
        }

    @app.post("/api/projects/{slug}/chapters")
    def add_chapter(slug: str, body: NewChapter):
        ensure_idle(slug)
        project = load(slug)
        if not body.premise.strip():
            raise HTTPException(400, "Escreva a premissa do capítulo.")
        num = project.next_chapter_num()
        ch.save_premise(project, num, body.premise)
        return {"num": num}

    @app.put("/api/projects/{slug}/chapters/{num}/premise")
    def put_premise(slug: str, num: int, body: TextBody):
        ensure_idle(slug)
        project = load(slug)
        chapter_exists(project, num)
        ch.save_premise(project, num, body.text)
        return {"ok": True}

    @app.put("/api/projects/{slug}/chapters/{num}/final")
    def put_final(slug: str, num: int, body: TextBody):
        ensure_idle(slug)
        project = load(slug)
        chapter_exists(project, num)
        if not body.text.strip():
            raise HTTPException(400, "O texto do capítulo está vazio.")
        return {"message": ch.save_final_text(project, num, body.text)}

    @app.delete("/api/projects/{slug}/chapters/{num}")
    def delete_chapter(slug: str, num: int):
        ensure_idle(slug)
        project = load(slug)
        return {"message": project.delete_chapter(num)}

    @app.get("/api/projects/{slug}/chapters/{num}/versions/{version_id}")
    def get_version(slug: str, num: int, version_id: str):
        project = load(slug)
        try:
            return {"files": ch.read_version(project, num, version_id)}
        except FileNotFoundError as e:
            raise HTTPException(404, str(e))

    @app.post("/api/projects/{slug}/chapters/{num}/versions/{version_id}/restore")
    def restore_version(slug: str, num: int, version_id: str):
        ensure_idle(slug)
        project = load(slug)
        try:
            return {"message": ch.restore_version(project, num, version_id)}
        except FileNotFoundError as e:
            raise HTTPException(404, str(e))

    # ── Geração ──────────────────────────────────────────────

    def require_ollama():
        if not check_ollama_health(timeout=3):
            raise HTTPException(503, f"O Ollama não respondeu em {config.OLLAMA_BASE_URL}. Abra o Ollama e tente de novo.")

    def start_job(slug: str, kind: str, label: str, work):
        ensure_idle()
        require_ollama()
        project = load(slug)

        def target(callbacks, cancel_event: threading.Event):
            orch = PipelineOrchestrator(project=project, cancel_event=cancel_event)
            work(orch, project, callbacks)

        try:
            job = jobs.start(kind, slug, label, target)
        except RuntimeError as e:
            raise HTTPException(409, str(e))
        return {"job": job.id}

    @app.post("/api/projects/{slug}/generate")
    def generate(slug: str, body: GenerateBody):
        project = load(slug)
        wanted = set(body.chapters) if body.chapters else None
        pending = [
            e for e in project.scan_chapters()
            if e.status != "done" and e.premise.strip() and (wanted is None or e.num in wanted)
        ]
        if not pending:
            raise HTTPException(400, "Nenhum capítulo pendente com premissa.")
        nums = [e.num for e in pending]
        premises = [e.premise for e in pending]
        label = f"Gerando capítulo(s) {', '.join(f'{n:02d}' for n in nums)}"
        return start_job(slug, "generate", label,
                         lambda orch, _p, cb: orch.run_batch(premises, cb, chapter_nums=nums))

    @app.post("/api/projects/{slug}/chapters/{num}/redo")
    def redo(slug: str, num: int, body: RedoBody):
        project = load(slug)
        chapter_exists(project, num)
        premise = body.premise
        if premise is not None and not premise.strip():
            raise HTTPException(400, "A premissa está vazia.")
        return start_job(slug, "redo", f"Refazendo capítulo {num:02d}",
                         lambda orch, _p, cb: orch.redo_chapter(num, premise, cb))

    @app.post("/api/projects/{slug}/chapters/{num}/refresh-memory")
    def refresh_memory(slug: str, num: int):
        project = load(slug)
        chapter_exists(project, num)
        return start_job(slug, "memory", f"Atualizando memória com o capítulo {num:02d}",
                         lambda orch, _p, cb: orch.refresh_memory(num, cb))

    @app.post("/api/jobs/cancel")
    def cancel():
        return {"cancelled": jobs.cancel()}

    @app.get("/api/events")
    def events():
        q, backlog = jobs.subscribe()

        def stream():
            try:
                # Eventos já passados (página reaberta no meio de uma geração): a interface
                # recupera o estado sem repetir avisos.
                for event in backlog:
                    yield f"data: {json.dumps({**event, 'replay': True}, ensure_ascii=False)}\n\n"
                while True:
                    try:
                        event = q.get(timeout=15)
                    except queue.Empty:
                        # Comentário SSE: mantém a conexão viva e detecta quando a página fechou.
                        yield ": ping\n\n"
                        continue
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            finally:
                jobs.unsubscribe(q)

        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    # ── Exportação ───────────────────────────────────────────

    @app.post("/api/projects/{slug}/export")
    def export(slug: str, body: ExportBody):
        project = load(slug)
        if body.format not in FORMATS:
            raise HTTPException(400, f"Formato desconhecido: {body.format}")
        dest = Path(body.path) if body.path else config.DATA_DIR / "exportacoes" / default_filename(project, body.format)
        try:
            path, count = export_project(project, body.format, dest, body.first, body.last, config.SCENE_BREAK)
        except ValueError as e:
            raise HTTPException(400, str(e))
        return {"path": str(path), "chapters": count}

    @app.get("/api/projects/{slug}/export/download")
    def export_download(slug: str, format: str = "epub", first: int | None = None, last: int | None = None):
        project = load(slug)
        if format not in FORMATS:
            raise HTTPException(400, f"Formato desconhecido: {format}")
        dest = config.DATA_DIR / "exportacoes" / default_filename(project, format)
        try:
            path, _ = export_project(project, format, dest, first, last, config.SCENE_BREAK)
        except ValueError as e:
            raise HTTPException(400, str(e))
        return FileResponse(path, filename=path.name)

    # ── Interface ────────────────────────────────────────────

    if STATIC_DIR.is_dir():
        app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")

    return app
