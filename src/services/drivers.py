"""Drivers de navegador usados pelo TIR.

O TIR **distribui** `chromedriver.exe` e `geckodriver.exe` dentro do pacote
(`tir/technologies/core/drivers/windows`), mas eles envelhecem: na primeira
corrida real aqui vieram ChromeDriver 126 com Chrome 148 instalado, e
geckodriver 0.30.0, de 2021. O sintoma é sempre o mesmo — `session not
created` no `setUpClass`, e nenhum caso roda.

Solução: uma pasta `drivers/` ao lado do executável, colocada **na frente do
PATH** do processo do TIR. O Selenium procura o driver no PATH antes de
qualquer outra coisa, então basta largar um arquivo novo ali para trocar a
versão — sem mexer no venv, que é recriado a cada atualização do framework.

A pasta é semeada com o que o TIR já traz, para nunca ficar vazia. Trocar o
arquivo é o caminho documentado de atualização.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path

from services.recursos import pasta_do_programa

log = logging.getLogger(__name__)

NOME_PASTA = "drivers"
EXECUTAVEIS = ("geckodriver.exe", "chromedriver.exe")

# Onde o TIR guarda os dele, dentro do venv.
_SUBPASTA_TIR = Path("tir") / "technologies" / "core" / "drivers" / "windows"

_SEM_JANELA = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def pasta() -> Path:
    return pasta_do_programa() / NOME_PASTA


def _pasta_do_tir(python_do_venv: Path) -> Path | None:
    """`site-packages/tir/.../drivers/windows` do venv informado."""
    lib = Path(python_do_venv).parent.parent / "Lib" / "site-packages"
    candidato = lib / _SUBPASTA_TIR
    return candidato if candidato.is_dir() else None


def semear(python_do_venv: Path) -> dict:
    """Copia para `drivers/` os drivers do TIR que ainda não estiverem lá.

    Não sobrescreve: se o usuário largou um driver novo, ele manda.
    """
    destino = pasta()
    destino.mkdir(parents=True, exist_ok=True)

    origem = _pasta_do_tir(python_do_venv)
    if origem is None:
        return {"ok": True, "copiados": [], "motivo": "TIR sem pasta de drivers."}

    copiados = []
    for nome in EXECUTAVEIS:
        alvo, fonte = destino / nome, origem / nome
        if alvo.exists() or not fonte.is_file():
            continue
        shutil.copy2(fonte, alvo)
        copiados.append(nome)
    if copiados:
        log.info("[DRIVERS] Semeados a partir do TIR: %s", ", ".join(copiados))
    return {"ok": True, "copiados": copiados}


def ambiente_com_drivers(base: dict | None = None) -> dict:
    """Cópia do ambiente com `drivers/` na frente do PATH."""
    env = dict(base or os.environ)
    caminho = str(pasta())
    atual = env.get("PATH", "")
    if caminho.casefold() not in atual.casefold():
        env["PATH"] = caminho + os.pathsep + atual
    return env


def _consultar(filtro: str) -> list:
    """`[(pid, ppid, criado)]` dos processos que casam com o filtro CIM."""
    script = (f"Get-CimInstance Win32_Process -Filter \"{filtro}\" | "
              "ForEach-Object { \"$($_.ProcessId)|$($_.ParentProcessId)\" }")
    try:
        saida = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=30,
            creationflags=_SEM_JANELA).stdout
    except (OSError, subprocess.SubprocessError):
        return []

    achados = []
    for linha in (saida or "").splitlines():
        if "|" not in linha:
            continue
        pid, _, ppid = linha.partition("|")
        try:
            achados.append((int(pid.strip()), int(ppid.strip())))
        except ValueError:
            continue
    return achados


def _filtro_dos_drivers() -> str:
    return " or ".join(f"Name='{nome}'" for nome in EXECUTAVEIS)


def _matar_arvore(pid: int) -> bool:
    try:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                       capture_output=True, timeout=30,
                       creationflags=_SEM_JANELA)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def encerrar_do_lancador(pid_lancador: int) -> int:
    """Derruba os drivers que **este** lançador abriu, e o navegador de cada um.

    Quando o teste falha antes do fim — `setUpClass` estourando, por exemplo —
    o TIR nunca chega no `TearDown`, e o driver com o navegador headless ficam
    vivos depois que o lançador morre. Numa noite de tentativas isso acumulou
    158 Firefox e 68 geckodriver ocupando gigabytes.

    O corte é por parentesco: só morre o que nasceu deste lançador. O
    navegador que o usuário abriu à mão não é filho de driver nenhum.
    """
    if not pid_lancador:
        return 0
    mortos = 0
    for pid, ppid in _consultar(_filtro_dos_drivers()):
        if ppid == pid_lancador and _matar_arvore(pid):
            mortos += 1
    if mortos:
        log.info("[DRIVERS] %d driver(s) do lançador %d encerrados.",
                 mortos, pid_lancador)
    return mortos


def encerrar_orfaos() -> int:
    """Rede de segurança: driver sem processo pai vivo.

    Driver de navegador só existe por automação — o usuário nunca abre um à
    mão. Órfão aqui é sempre resto de corrida anterior.
    """
    import os as _os
    mortos = 0
    for pid, ppid in _consultar(_filtro_dos_drivers()):
        vivo = False
        if ppid:
            try:
                _os.kill(ppid, 0)
                vivo = True
            except (OSError, PermissionError):
                vivo = _pai_existe(ppid)
        if not vivo and _matar_arvore(pid):
            mortos += 1
    if mortos:
        log.warning("[DRIVERS] %d driver(s) órfão(s) de corridas anteriores "
                    "encerrados.", mortos)
    return mortos


def _pai_existe(pid: int) -> bool:
    return bool(_consultar(f"ProcessId={int(pid)}"))


def _versao(executavel: Path) -> str:
    try:
        saida = subprocess.run([str(executavel), "--version"],
                               capture_output=True, text=True, timeout=20,
                               creationflags=_SEM_JANELA).stdout
    except (OSError, subprocess.SubprocessError):
        return ""
    return (saida or "").strip().splitlines()[0] if saida else ""


def listar() -> list:
    """O que está em `drivers/`, com a versão — para a tela e o diagnóstico."""
    destino = pasta()
    if not destino.is_dir():
        return []
    return [{"nome": nome, "versao": _versao(destino / nome),
             "caminho": str(destino / nome)}
            for nome in EXECUTAVEIS if (destino / nome).is_file()]
