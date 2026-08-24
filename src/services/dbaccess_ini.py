"""Consolida os aliases de banco no `dbaccess.ini` que vai rodar.

O `clonar_ambiente` grava `[MSSQL/<novo_banco>]` no `dbaccess.ini` **do clone**
— dentro da pasta do ambiente novo. Só que um DbAccess atende todos os bancos
(a porta 7890 é fixa e um processo por ambiente brigaria por ela), e o
processo que sobe é o de **um** ambiente. Resultado: ele conhece apenas o
próprio alias, e o AppServer dos clones pede um banco que o DbAccess nunca
ouviu falar — o login trava até o timeout, sem mensagem de erro clara.

Este módulo copia as seções que faltam para o arquivo do DbAccess que vai
rodar. Nada é removido: alias sobrando não atrapalha, alias faltando derruba
a instância inteira.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

log = logging.getLogger(__name__)

NOME_ARQUIVO = "dbaccess.ini"
PREFIXO = "MSSQL/"

_RE_SECAO = re.compile(r"^\s*\[([^\]]+)\]\s*$")


def caminho_do_ini(dbaccess_exe: str) -> Path:
    return Path(dbaccess_exe or "").parent / NOME_ARQUIVO


def _blocos(texto: str) -> dict[str, list[str]]:
    """Seções do arquivo, na ordem, como listas de linhas."""
    blocos: dict[str, list[str]] = {}
    atual = None
    for linha in texto.splitlines(keepends=True):
        achou = _RE_SECAO.match(linha)
        if achou:
            atual = achou.group(1).strip()
            blocos[atual] = [linha]
        elif atual is not None:
            blocos[atual].append(linha)
    return blocos


def aliases(ini_path: Path) -> list[str]:
    """Bancos que este `dbaccess.ini` conhece."""
    ini_path = Path(ini_path)
    if not ini_path.is_file():
        return []
    texto = ini_path.read_text(encoding="latin-1", errors="replace")
    return [s[len(PREFIXO):] for s in _blocos(texto)
            if s.upper().startswith(PREFIXO.upper())]


def consolidar(destino: Path, origens: list[Path]) -> dict:
    """Leva para `destino` as seções `[MSSQL/...]` que faltam nas `origens`.

    Devolve `mudou=True` quando o arquivo foi alterado — o DbAccess lê o `.ini`
    na partida, então nesse caso ele precisa ser reiniciado para enxergar os
    aliases novos.
    """
    destino = Path(destino)
    if not destino.is_file():
        return {"ok": False, "erro": f"dbaccess.ini não encontrado: {destino}"}

    texto = destino.read_text(encoding="latin-1", errors="replace")
    existentes = {s.upper() for s in _blocos(texto)}
    novas, vindos_de = [], {}

    for origem in origens:
        origem = Path(origem)
        if not origem.is_file() or origem.resolve() == destino.resolve():
            continue
        for secao, linhas in _blocos(
                origem.read_text(encoding="latin-1", errors="replace")).items():
            if not secao.upper().startswith(PREFIXO.upper()):
                continue
            if secao.upper() in existentes:
                continue
            existentes.add(secao.upper())
            novas.append("".join(linhas))
            vindos_de[secao] = str(origem)

    if not novas:
        return {"ok": True, "mudou": False, "aliases": aliases(destino)}

    if not texto.endswith("\n"):
        texto += "\n"
    texto += "\n" + "\n".join(bloco.rstrip("\n") + "\n" for bloco in novas)
    destino.write_text(texto, encoding="latin-1")

    log.info("[DBACCESS] %d alias adicionado(s) em %s: %s", len(novas),
             destino, ", ".join(sorted(vindos_de)))
    return {"ok": True, "mudou": True, "adicionados": sorted(vindos_de),
            "aliases": aliases(destino)}


def faltando(ini_path: Path, bancos: list[str]) -> list[str]:
    """Bancos que o DbAccess ainda não conhece — o diagnóstico do travamento."""
    conhecidos = {a.upper() for a in aliases(ini_path)}
    return [b for b in bancos if b and b.upper() not in conhecidos]


# ── Porta de escuta ─────────────────────────────────────────
# Só faz sentido quando cada instância tem o próprio DbAccess. Enquanto o
# processo era um só, a 7890 padrão bastava.

SECAO_GERAL = "GENERAL"
CHAVE_PORTA = "Port"
PORTA_PADRAO = 7890


def aplicar_porta(ini_path: Path, porta: int) -> dict:
    """Escreve `[GENERAL] Port=<porta>` — onde este DbAccess vai escutar.

    Sem a chave o DbAccess assume 7890, e um segundo processo na mesma
    máquina não sobe. A TOTVS documenta várias instâncias lado a lado, cada
    uma em outra porta, e diz que o valor do `.ini` tem **precedência sobre o
    `-pNNNN`** da linha de comando — por isso a escrita aqui, e não só o
    parâmetro.

    Mexe no arquivo do **clone**, que é descartável. O `dbaccess.ini` que o
    usuário mantém à mão não passa por aqui.
    """
    ini_path = Path(ini_path)
    if not ini_path.is_file():
        return {"ok": False, "erro": f"dbaccess.ini não encontrado: {ini_path}"}
    if not porta:
        return {"ok": False, "erro": "Porta do DbAccess não informada."}

    try:
        linhas = ini_path.read_text(encoding="latin-1").splitlines(keepends=True)
    except OSError as e:
        return {"ok": False, "erro": f"Não consegui ler o {ini_path.name}: {e}"}

    alvo = SECAO_GERAL.casefold()
    atual, escrito, fim_da_secao = None, False, None
    for i, linha in enumerate(linhas):
        achou = _RE_SECAO.match(linha)
        if achou:
            if atual == alvo and fim_da_secao is None:
                fim_da_secao = i
            atual = achou.group(1).strip().casefold()
            continue
        if atual != alvo or "=" not in linha:
            continue
        if linha.split("=", 1)[0].strip().casefold() == CHAVE_PORTA.casefold():
            fim = "\r\n" if linha.endswith("\r\n") else "\n"
            linhas[i] = f"{CHAVE_PORTA}={porta}{fim}"
            escrito = True

    if not escrito:
        if atual == alvo and fim_da_secao is None:
            fim_da_secao = len(linhas)          # a seção vai até o fim
        if fim_da_secao is None:
            linhas.insert(0, f"[{SECAO_GERAL}]\n{CHAVE_PORTA}={porta}\n")
        else:
            linhas.insert(fim_da_secao, f"{CHAVE_PORTA}={porta}\n")

    ini_path.write_text("".join(linhas), encoding="latin-1")
    log.info("[DBACCESS.INI] %s: porta %s.", ini_path.parent.name, porta)
    return {"ok": True, "porta": int(porta), "arquivo": str(ini_path)}


def ler_porta(ini_path: Path) -> int:
    """Porta configurada; 7890 quando a chave falta, que é o padrão do produto."""
    ini_path = Path(ini_path)
    if not ini_path.is_file():
        return 0
    atual = None
    for linha in ini_path.read_text(encoding="latin-1").splitlines():
        achou = _RE_SECAO.match(linha)
        if achou:
            atual = achou.group(1).strip().casefold()
            continue
        if atual != SECAO_GERAL.casefold() or "=" not in linha:
            continue
        chave, _, valor = linha.partition("=")
        if chave.strip().casefold() == CHAVE_PORTA.casefold():
            try:
                return int(valor.strip())
            except ValueError:
                return 0
    return PORTA_PADRAO
