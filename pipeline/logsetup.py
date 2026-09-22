"""
logsetup.py — Log em arquivo, para o usuário conseguir mandar o erro quando algo quebrar.

O executável é gerado com `--windowed`, que esconde o console: sem arquivo, nenhum erro
fica registrado. O log vai para `<pasta de dados>/logs/forja.log`, com rotação em 3
arquivos de 1 MB. Exceções não tratadas, inclusive de threads, também vão para lá.
"""

import logging
import sys
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path

from pipeline import config

LOG_FILENAME = "forja.log"
_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"


def log_file() -> Path:
    return config.LOG_DIR / LOG_FILENAME


def setup_logging(level: int = logging.INFO) -> Path | None:
    """Configura console e arquivo. Retorna o caminho do log, ou None se não deu para criar o arquivo."""
    root = logging.getLogger()
    root.setLevel(level)
    # Aberto pelo pythonw (sem console) o sys.stderr é None: aí só o arquivo de log vale.
    if sys.stderr is not None and not any(
            isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler) for h in root.handlers):
        console = logging.StreamHandler()
        console.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%H:%M:%S"))
        root.addHandler(console)

    path: Path | None = log_file()
    try:
        config.LOG_DIR.mkdir(parents=True, exist_ok=True)
        if not any(isinstance(h, RotatingFileHandler) for h in root.handlers):
            fh = RotatingFileHandler(path, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
            fh.setFormatter(logging.Formatter(_FORMAT))
            root.addHandler(fh)
    except OSError:
        root.exception("Não foi possível criar o arquivo de log em %s", path)
        path = None

    def excepthook(exc_type, exc, tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc, tb)
            return
        logging.getLogger("forja").critical("Erro não tratado", exc_info=(exc_type, exc, tb))

    def thread_excepthook(args):
        logging.getLogger("forja").critical(
            "Erro não tratado na thread %s", getattr(args.thread, "name", "?"),
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )

    sys.excepthook = excepthook
    threading.excepthook = thread_excepthook
    logging.getLogger("forja").info(
        "Forja de Ficção iniciada. Pasta de dados: %s | log: %s", config.DATA_DIR, path
    )
    return path
