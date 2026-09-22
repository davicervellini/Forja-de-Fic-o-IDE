"""Abre a Forja de Ficção sem a janela de console (duplo clique no Windows usa o pythonw)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import app_desktop  # noqa: E402

app_desktop.run()
