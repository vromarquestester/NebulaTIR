"""Escrita das portas no `appserver.ini` de cada instância paralela.

O `clonar_ambiente` do Gerenciador ajusta **apenas** a porta do `[WEBAPP]`.
Todas as outras — `[TCP]`, `[HTTPREST]`, `[WEBAGENT]` e o `SQLitePort` da
seção do ambiente — saem iguais do template em todos os clones. Com isso, só
um AppServer consegue escutar: o segundo morre ao tentar ocupar 8881 e 8080.

Este módulo aplica o plano de portas do NebulaTIR sobre o arquivo já gerado.

Ficam de fora, por decisão do usuário: `[LICENSECLIENT] port=8009` e o
`DBPort=7890` do DbAccess — um DbAccess só atende todos os bancos.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

log = logging.getLogger(__name__)

NOME_ARQUIVO = "appserver.ini"

# chave do plano → (seção, chave no arquivo)
DESTINOS = {
    "webapp": ("WEBAPP", "port"),
    "tcp": ("TCP", "Port"),
    "httprest": ("HTTPREST", "Port"),
    "webagent": ("WEBAGENT", "Port"),
}
# O SQLitePort não vive numa seção própria: fica na seção do ambiente.
CHAVE_SQLITE = "SQLitePort"

# `DBPort` também mora na seção do ambiente. Ele aponta o DbAccess daquela
# instância — era fixo em 7890 enquanto um processo só atendia todos.
CHAVE_DBPORT = "DBPort"


def caminho_do_ini(appserver_exe: str) -> Path:
    return Path(appserver_exe or "").parent / NOME_ARQUIVO


def _secao(linha: str) -> str | None:
    limpa = linha.strip()
    if limpa.startswith("[") and limpa.endswith("]"):
        return limpa[1:-1].strip()
    return None


def aplicar_portas(ini_path: Path, portas: dict) -> dict:
    """Reescreve as portas no arquivo, preservando o resto byte a byte.

    Edição linha a linha em vez de `configparser`: o `appserver.ini` tem
    comentários, seções repetidas em caixa diferente e chaves que o
    configparser normalizaria — reescrever o arquivo inteiro mudaria coisas
    que ninguém pediu para mudar.
    """
    ini_path = Path(ini_path)
    if not ini_path.is_file():
        return {"ok": False, "erro": f"appserver.ini não encontrado: {ini_path}"}

    alvos = {}
    for chave, valor in (portas or {}).items():
        if chave in DESTINOS and valor:
            secao, chave_ini = DESTINOS[chave]
            alvos[secao.casefold()] = (chave_ini, str(valor))

    # Chaves que moram na seção do ambiente, cujo nome muda por instalação:
    # a troca é pela chave, em qualquer seção.
    soltas = {
        CHAVE_SQLITE.casefold(): ("sqlite", str((portas or {}).get("sqlite") or "")),
        CHAVE_DBPORT.casefold(): ("dbaccess",
                                  str((portas or {}).get("dbaccess") or "")),
    }
    soltas = {k: v for k, v in soltas.items() if v[1]}

    try:
        linhas = ini_path.read_text(encoding="latin-1").splitlines(keepends=True)
    except OSError as e:
        return {"ok": False, "erro": f"Não consegui ler o {ini_path.name}: {e}"}

    atual, aplicadas = None, {}
    for i, linha in enumerate(linhas):
        nova_secao = _secao(linha)
        if nova_secao is not None:
            atual = nova_secao.casefold()
            continue
        if atual is None or "=" not in linha:
            continue

        chave_da_linha = linha.split("=", 1)[0].strip()
        fim = "\r\n" if linha.endswith("\r\n") else "\n"

        if atual in alvos:
            chave_ini, valor = alvos[atual]
            if chave_da_linha.casefold() == chave_ini.casefold():
                linhas[i] = f"{chave_da_linha}={valor}{fim}"
                aplicadas[atual] = valor
        solta = soltas.get(chave_da_linha.casefold())
        if solta is not None:
            logica, valor = solta
            linhas[i] = f"{chave_da_linha}={valor}{fim}"
            aplicadas[logica] = valor

    faltando = [s for s in alvos if s not in aplicadas]
    ini_path.write_text("".join(linhas), encoding="latin-1")
    log.info("[INI] %s: %s", ini_path.parent.name,
             ", ".join(f"{k}={v}" for k, v in sorted(aplicadas.items())))
    return {"ok": True, "aplicadas": aplicadas, "faltando": faltando,
            "arquivo": str(ini_path)}


CHAVE_SPECIALKEY = "SpecialKey"
# Limite conservador: a chave é concatenada nas funções de semáforo, e não há
# documentação sobre o tamanho máximo. O sufixo é curto de propósito.
_MAX_SPECIALKEY = 20


def aplicar_specialkey(ini_path: Path, sufixo: str) -> dict:
    """Torna o `SpecialKey` do clone diferente do ambiente de origem.

    **É o que impede o paralelismo quando não é feito.** A `SpecialKey` é
    concatenada nas funções de controle de acesso simultâneo — ela é a
    identidade do ambiente para o semáforo e para o bloqueio de RPO. O clone
    nasce com a chave do original, então o Protheus vê os três como o MESMO
    ambiente. Cada instância tem o RPO no próprio caminho
    (`...\\PAR2510_V1_TIR2\\Protheus\\apo`), e RPOs em caminhos diferentes sob
    a mesma chave disparam:

        Identificados acessos utilizando RPO divergentes
        ACCESO DIVERGENTE: Entorno: ENVIRONMENT, Server: ..., Puerto: 8882

    A primeira instância a entrar vira o "acesso inicial"; as seguintes são
    barradas. Daí o sintoma de só uma instância trabalhar.

    A regra documentada é: chave única por ambiente com dicionário próprio;
    igual só entre ambientes que dividem o mesmo `Protheus_data` — que não é o
    caso, cada clone tem o seu `RootPath`. Não tem efeito sobre licença; o que
    o compartilhamento indevido causa é justamente erro de conexão e semáforo.
    """
    ini_path = Path(ini_path)
    if not ini_path.is_file():
        return {"ok": False, "erro": f"appserver.ini não encontrado: {ini_path}"}
    sufixo = "".join(c for c in (sufixo or "") if c.isalnum())
    if not sufixo:
        return {"ok": False, "erro": "Sufixo de SpecialKey vazio."}

    try:
        linhas = ini_path.read_text(encoding="latin-1").splitlines(keepends=True)
    except OSError as e:
        return {"ok": False, "erro": f"Não consegui ler o {ini_path.name}: {e}"}

    marca = f"_{sufixo}"
    aplicada, reescrever = "", False
    for i, linha in enumerate(linhas):
        if _secao(linha) is not None or "=" not in linha:
            continue
        chave, _, valor = linha.partition("=")
        if chave.strip().casefold() != CHAVE_SPECIALKEY.casefold():
            continue
        atual = valor.strip()
        if atual.endswith(marca):
            # Já é a do clone. Subir a instância de novo não pode virar
            # `PAR25xx_T2_T2`, nem reescrever o arquivo à toa.
            aplicada = atual
            break
        base = atual[:_MAX_SPECIALKEY - len(marca)]
        aplicada = f"{base}{marca}"
        fim = "\r\n" if linha.endswith("\r\n") else "\n"
        linhas[i] = f"{chave.strip()}={aplicada}{fim}"
        reescrever = True
        break

    if not aplicada:
        return {"ok": True, "mudou": False,
                "motivo": "o appserver.ini não define SpecialKey"}
    if not reescrever:
        return {"ok": True, "mudou": False, "specialkey": aplicada}

    ini_path.write_text("".join(linhas), encoding="latin-1")
    log.info("[INI] %s: SpecialKey=%s (identidade própria para o semáforo e "
             "para o controle de RPO).", ini_path.parent.name, aplicada)
    return {"ok": True, "mudou": True, "specialkey": aplicada}


def ler_specialkey(ini_path: Path) -> str:
    ini_path = Path(ini_path)
    if not ini_path.is_file():
        return ""
    for linha in ini_path.read_text(encoding="latin-1").splitlines():
        if _secao(linha) is not None or "=" not in linha:
            continue
        chave, _, valor = linha.partition("=")
        if chave.strip().casefold() == CHAVE_SPECIALKEY.casefold():
            return valor.strip()
    return ""


SECAO_WEBMONITOR = "WEBMONITOR"
CHAVE_ENABLE = "ENABLE"


def desativar_webmonitor(ini_path: Path) -> dict:
    """Escreve `ENABLE=0` em `[WEBMONITOR]` do clone.

    O WebMonitor abre uma porta **fixa** (3434 aqui) em todo AppServer que
    sobe, e o número não sai de nenhuma chave que o plano de portas controle.
    Com três clones na mesma máquina, dois falham ao abri-la — ruído de
    `error 10048` no console, misturado com o que importa.

    O TIR não usa o monitor: ele fala com o WebApp. Desligar é a única saída
    limpa, e é o caminho documentado (`ENABLE=0` na seção).

    Vale só para os clones do NebulaTIR. O ambiente que o usuário abre à mão
    pelo Gerenciador continua com o monitor.
    """
    ini_path = Path(ini_path)
    if not ini_path.is_file():
        return {"ok": False, "erro": f"appserver.ini não encontrado: {ini_path}"}

    try:
        linhas = ini_path.read_text(encoding="latin-1").splitlines(keepends=True)
    except OSError as e:
        return {"ok": False, "erro": f"Não consegui ler o {ini_path.name}: {e}"}

    alvo = SECAO_WEBMONITOR.casefold()
    atual, escrito, fim_da_secao = None, False, None
    for i, linha in enumerate(linhas):
        nova = _secao(linha)
        if nova is not None:
            if atual == alvo and fim_da_secao is None:
                fim_da_secao = i
            atual = nova.casefold()
            continue
        if atual != alvo or "=" not in linha:
            continue
        if linha.split("=", 1)[0].strip().casefold() == CHAVE_ENABLE.casefold():
            fim = "\r\n" if linha.endswith("\r\n") else "\n"
            linhas[i] = f"{CHAVE_ENABLE}=0{fim}"
            escrito = True

    if not escrito:
        if atual == alvo and fim_da_secao is None:
            fim_da_secao = len(linhas)          # a seção vai até o fim
        if fim_da_secao is None:
            # Sem a seção no arquivo: cria uma. O AppServer aceita, e o clone
            # é descartável — não é o `.ini` que o usuário mantém à mão.
            if linhas and not linhas[-1].endswith(("\n", "\r\n")):
                linhas.append("\n")
            linhas.append(f"\n[{SECAO_WEBMONITOR}]\n{CHAVE_ENABLE}=0\n")
        else:
            linhas.insert(fim_da_secao, f"{CHAVE_ENABLE}=0\n")

    ini_path.write_text("".join(linhas), encoding="latin-1")
    log.info("[INI] %s: WebMonitor desligado (porta fixa colide entre clones).",
             ini_path.parent.name)
    return {"ok": True, "arquivo": str(ini_path)}


def ler_portas(ini_path: Path) -> dict:
    """Portas atualmente no arquivo — usado para conferir e para os testes."""
    ini_path = Path(ini_path)
    if not ini_path.is_file():
        return {}
    por_secao = {secao.casefold(): chave for secao, chave in DESTINOS.values()}
    del por_secao  # a leitura abaixo é por seção, não por chave

    achadas, atual = {}, None
    for linha in ini_path.read_text(encoding="latin-1").splitlines():
        nova = _secao(linha)
        if nova is not None:
            atual = nova.casefold()
            continue
        if atual is None or "=" not in linha:
            continue
        chave = linha.split("=", 1)[0].strip()
        valor = linha.split("=", 1)[1].strip()
        for logica, (secao, chave_ini) in DESTINOS.items():
            if atual == secao.casefold() and chave.casefold() == chave_ini.casefold():
                achadas[logica] = valor
        if chave.casefold() == CHAVE_SQLITE.casefold():
            achadas["sqlite"] = valor
        if chave.casefold() == CHAVE_DBPORT.casefold():
            achadas["dbaccess"] = valor
    return achadas


_RE_NUMERO = re.compile(r"^\d+$")


def conferir(ini_path: Path, portas: dict) -> list[str]:
    """Diferenças entre o que o plano pede e o que está no arquivo."""
    atuais = ler_portas(ini_path)
    problemas = []
    for chave, esperado in (portas or {}).items():
        if chave not in DESTINOS and chave not in ("sqlite", "dbaccess"):
            continue
        atual = atuais.get(chave, "")
        if not _RE_NUMERO.match(atual or "") or int(atual) != int(esperado):
            problemas.append(f"{chave}: esperado {esperado}, no arquivo {atual or '—'}")
    return problemas
