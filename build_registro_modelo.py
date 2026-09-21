"""
build_registro_modelo.py — Gera registro_modelo.md a partir do Registro Akáshico.

Uso:
    python build_registro_modelo.py <caminho_do_projeto>

A importação pela GUI já faz isso automaticamente.
Este script existe para uso manual / automação.
"""

import sys
from pathlib import Path

from pipeline.akashic import build_registro_modelo


def main() -> None:
    if len(sys.argv) < 2:
        print("Uso: python build_registro_modelo.py <caminho_do_projeto>")
        sys.exit(1)

    project_dir = Path(sys.argv[1]).resolve()
    ok, msg = build_registro_modelo(project_dir)
    print(msg)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
