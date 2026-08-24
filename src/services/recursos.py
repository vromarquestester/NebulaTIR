"""Arquivos que acompanham o programa (empacotados ou do repositório).

O PyInstaller extrai os dados para `sys._MEIPASS`; em desenvolvimento eles
estão na árvore do projeto. Este módulo esconde a diferença de quem consome.
"""

from __future__ import annotations

import sys
from pathlib import Path

RAIZ_REPO = Path(__file__).resolve().parent.parent.parent


def base_empacotada() -> Path | None:
    caminho = getattr(sys, "_MEIPASS", None)
    return Path(caminho) if caminho else None


def recurso(*partes: str) -> Path | None:
    """Primeiro no pacote, depois no repositório. None se não existir."""
    empacotado = base_empacotada()
    if empacotado:
        candidato = empacotado.joinpath(*partes)
        if candidato.exists():
            return candidato
    candidato = RAIZ_REPO.joinpath(*partes)
    return candidato if candidato.exists() else None


def pasta_do_programa() -> Path:
    """Onde o executável está — é aí que nascem `tests/`, `.venv/` e `config/`.

    Congelado, é a pasta do `.exe`. Em desenvolvimento, a raiz do repositório;
    sem isso, `tests/` cairia dentro de `src/` durante o desenvolvimento.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return RAIZ_REPO


