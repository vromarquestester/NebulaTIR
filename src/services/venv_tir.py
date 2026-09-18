"""Ambiente virtual que roda o TIR.

O TIR **não funciona em Python novo** — exige 3.12. O NebulaTIR em si pode
rodar em versão mais nova; por isso o TIR fica num `.venv` separado, ao lado
do executável, invocado como subprocesso.

Quem provisiona o interpretador depende da máquina:

- **com `uv` no PATH** (máquina de desenvolvimento), o `uv` cria o venv e
  baixa o 3.12 se a máquina não tiver;
- **sem `uv`** (máquina do usuário, que tem só o Python instalado), o venv é
  criado com `python -m venv` a partir do Python 3.12 global — procurado pelo
  `py -3.12`, depois `python3.12`, `python3` e `python`, aceito só se for 3.12.

Sem nenhum dos dois não há como criar o venv, e o erro diz o que instalar.

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


def _python_global() -> tuple[str | None, list[str]]:
    """Python 3.12 instalado na máquina, para quando não há `uv`.

    Devolve `(executável, versões_vistas)`. O executável é o que o próprio
    interpretador diz ser (`sys.executable`), não o atalho do PATH — no
    Windows o `py -3.12` e o alias da Store apontam para outro lugar. Um
    Python de outra versão não serve: o TIR exige `==3.12.*`.
    """
    candidatos: list[list[str]] = []
    if os.name == "nt":
        py = shutil.which("py")
        if py:
            candidatos.append([py, f"-{VERSAO_PYTHON}"])
    for nome in (f"python{VERSAO_PYTHON}", "python3", "python"):
        exe = shutil.which(nome)
        if exe:
            candidatos.append([exe])

    vistas: list[str] = []
    for cmd in candidatos:
        ok, saida = _rodar([*cmd, "-c",
                            "import sys; print(sys.executable); "
                            "print('%d.%d' % sys.version_info[:2])"], 30)
        linhas = [l.strip() for l in saida.splitlines() if l.strip()]
        if not ok or len(linhas) < 2:
            continue
        executavel, versao = linhas[-2], linhas[-1]
        if versao == VERSAO_PYTHON:
            return executavel, vistas
        if versao not in vistas:
            vistas.append(versao)
    return None, vistas


def provisionador() -> str | None:
    """Quem cria o venv nesta máquina: `"uv"`, `"python"` ou `None`."""
    if _uv():
        return "uv"
    if _python_global()[0]:
        return "python"
    return None


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
    if uv:
        via = "uv"
        cmd = [uv, "venv", "--python", VERSAO_PYTHON, str(caminho_venv())]
    else:
        python, vistas = _python_global()
        if not python:
            achou = ""
            if vistas:
                achou = (" Python encontrado no PATH: "
                         + ", ".join(vistas) + " — o TIR só roda no "
                         f"{VERSAO_PYTHON}.")
            return {"ok": False,
                    "erro": "Nem o `uv` nem um Python "
                            f"{VERSAO_PYTHON} foram encontrados no PATH."
                            f"{achou} Instale o Python {VERSAO_PYTHON} "
                            "(python.org) ou o uv (docs.astral.sh/uv)."}
        via = "python"
        cmd = [python, "-m", "venv", str(caminho_venv())]

    log.info("[VENV] Criando o ambiente do TIR (Python %s) via %s…",
             VERSAO_PYTHON, via)
    ok, saida = _rodar(cmd, TEMPO_LIMITE_CRIACAO)
    if not ok:
        return {"ok": False, "erro": f"Falha ao criar o ambiente: {saida}"}
    return {"ok": True, "criado": True, "python": str(python_do_venv()),
            "via": via, "saida": saida}


def atualizar_tir() -> dict:
    """`pip install tir_framework --upgrade` dentro do venv do TIR."""
    if not existe():
        return {"ok": False, "erro": "Ambiente do TIR ainda não foi criado."}

    uv = _uv()
    # `uv pip` é a via rápida; o pip do próprio venv é a reserva. Um venv
    # criado pelo uv nem sempre traz pip — se o uv sumiu depois, o
    # `ensurepip` repõe; num venv de `python -m venv` ele já existe.
    if uv:
        cmd = [uv, "pip", "install", "--upgrade", *PACOTES,
               "--python", str(python_do_venv())]
    else:
        _rodar([str(python_do_venv()), "-m", "ensurepip", "--upgrade"],
               TEMPO_LIMITE_INSTALL)
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
