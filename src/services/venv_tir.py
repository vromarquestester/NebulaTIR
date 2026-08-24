"""Ambiente virtual que roda o TIR.

O TIR **não funciona em Python novo** — exige 3.12. O NebulaTIR em si pode
rodar em versão mais nova; por isso o TIR fica num `.venv` separado, ao lado
do executável, invocado como subprocesso.

O interpretador é provisionado pelo `uv`, que baixa o 3.12 se a máquina não
tiver. Sem isso, instalar o NebulaTIR exigiria instalar Python antes.

Antes de cada execução roda `pip install tir_framework --upgrade`, como pedido
— o framework muda com frequência e a esteira precisa da versão do dia.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path

from services.recursos import pasta_do_programa

log = logging.getLogger(__name__)

VERSAO_PYTHON = "3.12"
NOME_VENV = ".venv"
PACOTE = "tir_framework"
# O relatório PNG roda dentro deste venv (o lançador importa o exportador que
# veio do LogNebula), então o Pillow é dependência daqui — não do NebulaTIR.
PACOTES = (PACOTE, "pillow")

_SEM_JANELA = getattr(subprocess, "CREATE_NO_WINDOW", 0)
TEMPO_LIMITE_CRIACAO = 600      # download do interpretador na primeira vez
TEMPO_LIMITE_INSTALL = 600


def caminho_venv() -> Path:
    return pasta_do_programa() / NOME_VENV


def python_do_venv() -> Path:
    venv = caminho_venv()
    return venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def existe() -> bool:
    return python_do_venv().is_file()


def _uv() -> str | None:
    return shutil.which("uv")


def _rodar(cmd: list[str], tempo: int) -> tuple[bool, str]:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=tempo, creationflags=_SEM_JANELA)
    except (OSError, subprocess.SubprocessError) as e:
        return False, str(e)
    saida = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode == 0, saida.strip()


def criar(forcar: bool = False) -> dict:
    """Cria o `.venv` do TIR na pasta do programa, se ainda não existir."""
    if existe() and not forcar:
        return {"ok": True, "criado": False, "python": str(python_do_venv())}

    uv = _uv()
    if not uv:
        return {"ok": False,
                "erro": "O `uv` não foi encontrado no PATH. Ele é quem "
                        f"provisiona o Python {VERSAO_PYTHON} do TIR."}

    log.info("[VENV] Criando o ambiente do TIR (Python %s)…", VERSAO_PYTHON)
    ok, saida = _rodar([uv, "venv", "--python", VERSAO_PYTHON,
                        str(caminho_venv())], TEMPO_LIMITE_CRIACAO)
    if not ok:
        return {"ok": False, "erro": f"Falha ao criar o ambiente: {saida}"}
    return {"ok": True, "criado": True, "python": str(python_do_venv()),
            "saida": saida}


def atualizar_tir() -> dict:
    """`pip install tir_framework --upgrade` dentro do venv do TIR."""
    if not existe():
        return {"ok": False, "erro": "Ambiente do TIR ainda não foi criado."}

    uv = _uv()
    # `uv pip` é a via rápida; o pip do próprio venv é a reserva, porque um
    # venv criado pelo uv nem sempre traz pip instalado.
    if uv:
        cmd = [uv, "pip", "install", "--upgrade", *PACOTES,
               "--python", str(python_do_venv())]
    else:
        cmd = [str(python_do_venv()), "-m", "pip", "install", "--upgrade",
               *PACOTES]

    log.info("[VENV] Atualizando %s…", ", ".join(PACOTES))
    ok, saida = _rodar(cmd, TEMPO_LIMITE_INSTALL)
    if not ok:
        return {"ok": False, "erro": f"Falha ao atualizar o {PACOTE}: {saida}"}
    return {"ok": True, "saida": saida}


def preparar() -> dict:
    """Garante ambiente criado e TIR atualizado. Chamado antes de executar."""
    criacao = criar()
    if not criacao.get("ok"):
        return criacao
    atualizacao = atualizar_tir()
    if not atualizacao.get("ok"):
        return atualizacao
    return {"ok": True, "python": str(python_do_venv()),
            "criado_agora": criacao.get("criado", False)}


def versao_instalada() -> str:
    """Versão do tir_framework no venv, ou vazio se não der para saber."""
    if not existe():
        return ""
    ok, saida = _rodar([str(python_do_venv()), "-c",
                        "import importlib.metadata as m; "
                        f"print(m.version('{PACOTE}'))"], 30)
    return saida.strip() if ok else ""
