"""
app_desktop.py — Abre a Forja de Ficção numa janela própria (interface web + pywebview).

A API (webapp/server.py) roda num servidor local em 127.0.0.1, numa porta livre, e a
janela usa o WebView2 do Windows. Nada fica exposto na rede.

Uso:
    python app_desktop.py            # janela do programa, com console para ver o log
    pythonw app_desktop.py           # janela do programa, sem console (o atalho usa este)
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


ICON = Path(__file__).resolve().parent / "webapp" / "static" / "forja.ico"


def windows_app_id():
    """Sem isto o Windows agrupa a janela com o Python e mostra o ícone dele na barra de tarefas."""
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("ForjaDeFiccao.App")


def set_window_icon(title: str):
    """O pywebview no Windows não aceita ícone próprio: troca pelo da Forja depois que a janela abre."""
    if sys.platform != "win32" or not ICON.exists():
        return
    import ctypes
    user32 = ctypes.windll.user32
    user32.FindWindowW.restype = ctypes.c_void_p
    user32.LoadImageW.restype = ctypes.c_void_p
    user32.SendMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p]
    hwnd = user32.FindWindowW(None, title)
    if not hwnd:
        return
    for which, size in ((1, 48), (0, 16)):  # ICON_BIG, ICON_SMALL
        hicon = user32.LoadImageW(None, str(ICON), 1, size, size, 0x10)  # IMAGE_ICON, LR_LOADFROMFILE
        if hicon:
            user32.SendMessageW(hwnd, 0x80, which, hicon)  # WM_SETICON


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
        windows_app_id()
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
            window.events.shown += lambda: set_window_icon("Forja de Ficção")
        webview.start(private_mode=False, storage_path=str(config.DATA_DIR / "webview"))

    if jobs.busy:
        jobs.cancel()
        jobs.wait(30)
    server.should_exit = True
    logger.info("Forja de Ficção encerrada.")
    os._exit(0)


def show_fatal(message: str):
    """Sem console (pythonw), um erro na abertura sumiria calado: mostra numa caixa do Windows."""
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, message, "Forja de Ficção", 0x10)
    else:
        print(message, file=sys.stderr)


def run():
    try:
        main()
    except Exception as e:
        logger.exception("A Forja de Ficção não conseguiu abrir.")
        from pipeline.logsetup import log_file
        show_fatal(f"A Forja de Ficção não conseguiu abrir:\n\n{e}\n\nDetalhes no log:\n{log_file()}")
        os._exit(1)


if __name__ == "__main__":
    run()
