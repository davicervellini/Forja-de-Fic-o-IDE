"""
app_desktop.py — Abre a Forja de Ficção numa janela própria (interface web + pywebview).

A API (webapp/server.py) roda num servidor local em 127.0.0.1, numa porta livre, e a
janela usa o WebView2 do Windows. Nada fica exposto na rede.

Uso:
    python app_desktop.py            # janela do programa
    python app_desktop.py --browser  # abre no navegador padrão (desenvolvimento)
"""

import logging
import os
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path
from typing import Any

import uvicorn

from pipeline import config
from pipeline.logsetup import setup_logging
from webapp.jobs import JobManager
from webapp.server import create_app

logger = logging.getLogger("forja")

HOST = "127.0.0.1"


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((HOST, 0))
        return s.getsockname()[1]


def start_server(jobs: JobManager) -> tuple[uvicorn.Server, str]:
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(
        create_app(jobs), host=HOST, port=port, log_level="warning", access_log=False,
        # Sem isso o uvicorn tenta configurar o log e briga com o logsetup.
        log_config=None,
    ))
    thread = threading.Thread(target=server.run, daemon=True, name="api")
    thread.start()
    deadline = time.time() + 15
    while not server.started:
        if time.time() > deadline or not thread.is_alive():
            raise RuntimeError("O servidor local não iniciou.")
        time.sleep(0.05)
    return server, f"http://{HOST}:{port}/"


class DesktopApi:
    """Funções que a página chama via window.pywebview.api (diálogos nativos)."""

    FILTERS = {
        "epub": "E-book (*.epub)",
        "md": "Markdown (*.md)",
        "txt": "Texto (*.txt)",
        "html": "Página HTML (*.html)",
    }

    def __init__(self):
        # Com sublinhado: o pywebview não expõe à página atributos que começam com _.
        self._window: Any = None

    def save_dialog(self, filename: str, fmt: str):
        import webview
        result = self._window.create_file_dialog(
            webview.FileDialog.SAVE,
            directory=str(Path.home() / "Documents"),
            save_filename=filename,
            file_types=(self.FILTERS.get(fmt, "Todos (*.*)"),),
        )
        if not result:
            return None
        path = result if isinstance(result, str) else result[0]
        if not path.lower().endswith(f".{fmt}"):
            path += f".{fmt}"
        return path

    def reveal(self, path: str):
        p = Path(path)
        if sys.platform == "win32":
            subprocess.Popen(["explorer", "/select,", str(p)])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", str(p)])
        else:
            subprocess.Popen(["xdg-open", str(p.parent)])


def main():
    setup_logging()
    config.ensure_data_dirs()
    jobs = JobManager()
    server, url = start_server(jobs)
    logger.info("Interface em %s", url)

    if "--browser" in sys.argv:
        webbrowser.open(url)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
    else:
        import webview
        api = DesktopApi()
        window = webview.create_window(
            "Forja de Ficção", url, js_api=api, width=1360, height=880, min_size=(980, 640),
            background_color="#15171c", text_select=True,
        )
        api._window = window

        def on_closing():
            # Geração em andamento: cancela e espera o pipeline devolver o estado anterior.
            if jobs.busy:
                logger.info("Janela fechando com geração em andamento; cancelando.")
                jobs.cancel()
                jobs.wait(30)
            return True

        if window is not None:
            window.events.closing += on_closing
        webview.start(private_mode=False, storage_path=str(config.DATA_DIR / "webview"))

    if jobs.busy:
        jobs.cancel()
        jobs.wait(30)
    server.should_exit = True
    logger.info("Forja de Ficção encerrada.")
    os._exit(0)


if __name__ == "__main__":
    main()
