"""
jobs.py — Uma geração por vez, rodando numa thread, com eventos para a interface.

O Ollama usa a GPU inteira: duas gerações ao mesmo tempo só deixam as duas lentas.
Por isso existe um único trabalho ativo no programa. A interface recebe os eventos
(status, trechos de texto, fim de fase, erro) por Server-Sent Events.
"""

import itertools
import logging
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from pipeline.orchestrator import PipelineCallbacks

logger = logging.getLogger(__name__)

# Trechos de texto são juntados antes de ir para a interface: um evento por token
# deixaria a página lenta sem ganho visível.
TOKEN_FLUSH_SECONDS = 0.15
HISTORY_LIMIT = 400


@dataclass
class Job:
    id: int
    kind: str
    project: str
    label: str
    chapter: int | None = None
    started: float = field(default_factory=time.time)
    status: str = "running"  # running, done, error, cancelled
    message: str = ""


class JobManager:
    def __init__(self):
        self._lock = threading.Lock()
        self._ids = itertools.count(1)
        self._subscribers: list[queue.Queue] = []
        self._history: list[dict] = []
        self._seq = itertools.count(1)
        self.job: Job | None = None
        self.cancel_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._token_buf: dict[str, list[str]] = {}
        self._last_flush = 0.0
        # A troca da nuvem para o modelo local aparece na tela como aviso.
        from pipeline import api
        api.fallback_listeners.append(self._on_fallback)

    def _on_fallback(self, message: str):
        self._flush_tokens(force=True)
        self.emit("fallback", message=message, chapter=self.job.chapter if self.job else None)

    # ── Assinantes (SSE) ─────────────────────────────────────

    def subscribe(self) -> tuple[queue.Queue, list[dict]]:
        q: queue.Queue = queue.Queue(maxsize=2000)
        with self._lock:
            self._subscribers.append(q)
            backlog = list(self._history)
        return q, backlog

    def unsubscribe(self, q: queue.Queue):
        with self._lock:
            if q in self._subscribers:
                self._subscribers.remove(q)

    def emit(self, type_: str, **data: Any):
        event = {"seq": next(self._seq), "type": type_, "time": time.time(), **data}
        with self._lock:
            if type_ == "job_start":
                self._history = []
            self._history.append(event)
            if len(self._history) > HISTORY_LIMIT:
                # Trechos antigos saem primeiro; eventos de estado ficam.
                self._history = [e for e in self._history if e["type"] != "token"][-HISTORY_LIMIT:]
            subs = list(self._subscribers)
        for q in subs:
            try:
                q.put_nowait(event)
            except queue.Full:
                pass

    # ── Execução ─────────────────────────────────────────────

    @property
    def busy(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def state(self) -> dict | None:
        if not self.job:
            return None
        j = self.job
        return {"id": j.id, "kind": j.kind, "project": j.project, "label": j.label,
                "chapter": j.chapter, "status": j.status, "message": j.message,
                "started": j.started, "running": self.busy}

    def start(self, kind: str, project: str, label: str, target: Callable[[PipelineCallbacks, threading.Event], Any]) -> Job:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                raise RuntimeError("Já existe uma geração em andamento. Espere terminar ou cancele.")
            self.cancel_event = threading.Event()
            job = Job(id=next(self._ids), kind=kind, project=project, label=label)
            self.job = job
        # A thread ainda não começou: o estado sai marcado como rodando de propósito.
        self.emit("job_start", job={**(self.state() or {}), "running": True})

        def run():
            try:
                target(self._callbacks(job), self.cancel_event)
                if self.cancel_event.is_set():
                    job.status = "cancelled"
                elif job.status == "running":
                    job.status = "done"
            except Exception as e:
                logger.exception("Erro no trabalho de geração")
                job.status = "error"
                job.message = str(e)
                self.emit("error", message=str(e), chapter=job.chapter)
            finally:
                self._flush_tokens(force=True)
                self.emit("job_end", job=self.state())

        self._thread = threading.Thread(target=run, daemon=True, name=f"job-{job.id}")
        self._thread.start()
        return job

    def cancel(self) -> bool:
        if not self.busy:
            return False
        self.cancel_event.set()
        self.emit("status", message="Cancelando...")
        return True

    def wait(self, timeout: float | None = None) -> bool:
        t = self._thread
        if t is not None:
            t.join(timeout)
        return not self.busy

    # ── Callbacks do pipeline ────────────────────────────────

    def _flush_tokens(self, force: bool = False):
        now = time.time()
        if not force and now - self._last_flush < TOKEN_FLUSH_SECONDS:
            return
        self._last_flush = now
        buf, self._token_buf = self._token_buf, {}
        for phase, parts in buf.items():
            if parts:
                self.emit("token", phase=phase, text="".join(parts), chapter=self.job.chapter if self.job else None)

    def _callbacks(self, job: Job) -> PipelineCallbacks:
        def on_token(phase: str, token: str):
            self._token_buf.setdefault(phase, []).append(token)
            self._flush_tokens()

        def on_status(msg: str):
            self._flush_tokens(force=True)
            job.message = msg
            self.emit("status", message=msg, chapter=job.chapter)

        def on_phase(phase: str, text: str, num: int):
            self._flush_tokens(force=True)
            self.emit("phase_complete", phase=phase, chapter=num, words=len(text.split()))

        def on_start(num: int):
            job.chapter = num
            self.emit("chapter_start", chapter=num)

        def on_complete(num: int, result):
            self.emit("chapter_complete", chapter=num, status=result.status)

        def on_error(msg: str, num: int | None):
            self._flush_tokens(force=True)
            cancelled = self.cancel_event.is_set()
            job.status = "cancelled" if cancelled else "error"
            job.message = msg
            self.emit("error", message=msg, chapter=num, cancelled=cancelled)

        return PipelineCallbacks(
            on_status=on_status,
            on_token=on_token,
            on_phase_complete=on_phase,
            on_chapter_start=on_start,
            on_chapter_complete=on_complete,
            on_error=on_error,
        )
