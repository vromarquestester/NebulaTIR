"""O que as instâncias deixaram no disco, e o que disso ainda faz sentido.

Cruza três fontes que discordam entre si:

- **o registro** (`instances.json`) — o que o NebulaTIR criou;
- **o Gerenciador** — o que ainda está cadastrado como ambiente;
- **o disco** — o que de fato ocupa espaço.

A situação de cada instância sai desse cruzamento. Um ambiente Protheus
completo por instância é dezenas de GB, e o caso que abriu esta frente foi
justamente uma pasta de pé sem ninguém sabendo mais de onde veio.

**Órfã só se declara contra um Gerenciador no ar.** A máquina pode ter mais de
uma instalação dele, cada uma com o próprio `bancos_config.ini` — foi o que
aconteceu aqui: as instâncias pareciam órfãs porque o config lido era o da
outra instalação. Com o canal offline, nada é classificado; a situação vira
`indefinida` e a limpeza fica travada. Apagar dezenas de GB com base em
"não achei o cadastro" é o erro que não dá para desfazer.

Tamanho não é medido junto: somar uma árvore de milhares de arquivos leva
segundos e a listagem abre a tela. `medir` existe para isso, sob demanda.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

from services.instancias import pasta_raiz

log = logging.getLogger(__name__)

# `PAR2510_V1_TIR1` → origem `PAR2510_V1`, slot 1. É o padrão de nome que a
# geração usa (`paralelos.nome_paralelo`), e o que permite reconhecer no disco
# uma instância cujo registro se perdeu.
RE_INSTANCIA = re.compile(r"^(?P<origem>.+)_TIR(?P<slot>\d+)$", re.IGNORECASE)

OK = "ok"                        # cadastrada e no disco
ORFA = "orfa"                    # o ambiente-pai não existe mais
SEM_CADASTRO = "sem_cadastro"    # o pai existe, a instância saiu do Gerenciador
FANTASMA = "fantasma"            # registrada, mas não há pasta no disco
NAO_REGISTRADA = "nao_registrada"  # pasta no disco que o registro não conhece
INDEFINIDA = "indefinida"        # Gerenciador offline: não dá para afirmar nada

MOTIVOS = {
    OK: "No Gerenciador e no disco.",
    ORFA: "O ambiente-pai não está mais no Gerenciador.",
    SEM_CADASTRO: "Saiu do Gerenciador, mas o ambiente-pai continua lá.",
    FANTASMA: "Está no registro, mas não há pasta no disco.",
    NAO_REGISTRADA: "Pasta de instância que o registro não conhece.",
    INDEFINIDA: "Gerenciador fechado — sem ele não dá para dizer o que sobra.",
}

# Situações que autorizam limpeza. `sem_cadastro` fica de fora de propósito: o
# pai vivo significa que aquela instância pode estar em uso numa corrida.
REMOVIVEIS = (ORFA, FANTASMA, NAO_REGISTRADA)


def _existe(caminho: str | None) -> bool:
    return bool(caminho) and Path(caminho).exists()


def decompor(nome: str) -> tuple[str, int] | None:
    """`PAR2510_V1_TIR2` → `("PAR2510_V1", 2)`; qualquer outra coisa → None."""
    achado = RE_INSTANCIA.match(nome or "")
    if not achado:
        return None
    return achado.group("origem"), int(achado.group("slot"))


def pastas_de_instancia(base_path: str | Path) -> list[Path]:
    """Pastas com cara de instância no disco.

    Dois lugares: `NebulaInstancia`, onde as novas nascem, e a raiz dos
    ambientes, onde estão as criadas antes desta mudança.
    """
    base = Path(base_path or "")
    achadas: list[Path] = []
    for raiz in (pasta_raiz(base), base):
        try:
            for item in sorted(raiz.iterdir(), key=lambda p: p.name.casefold()):
                if item.is_dir() and decompor(item.name):
                    achadas.append(item)
        except OSError:
            continue
    return achadas


def caminhos_ocupados(item: dict) -> list[str]:
    """Tudo que aquela instância ocupa e que existe agora.

    `workspace_banco` entra porque é onde ficam o MDF/LDF anexados — apagar só
    a pasta do ambiente deixaria a maior parte dos GB para trás. `temp_pai`
    **não** entra: é do ambiente-pai, não da instância.
    """
    return [c for c in (item.get("pasta"), item.get("workspace_banco"))
            if _existe(c)]


def _situacao(item: dict, ambientes: set[str], online: bool) -> str:
    if not online:
        return INDEFINIDA
    origem = item.get("origem", "")
    if origem and origem not in ambientes:
        return ORFA
    # Fantasma só quando se sabe onde a pasta deveria estar e ela não está.
    # Registro sem caminho é registro antigo, não instância sumida.
    if item.get("pasta") and not _existe(item["pasta"]):
        return FANTASMA
    if item["ambiente"] not in ambientes:
        return SEM_CADASTRO
    return OK


def levantar(*, registradas: list[dict], ambientes: set[str], online: bool,
             base_path: str = "") -> dict:
    """Situação de cada instância, registrada ou não.

    `registradas` sai de `Instancias.listar()` (já com o estado dos processos),
    `ambientes` é o que o Gerenciador tem cadastrado agora.
    """
    itens = []
    conhecidas = set()
    # Instância registrada antes desta versão não guardou caminho nenhum. A
    # pasta de mesmo nome no disco é o vínculo que sobrou — não é gravada no
    # registro aqui, só usada para não mostrar a linha sem tamanho e sem rumo.
    no_disco = {p.name.casefold(): p for p in pastas_de_instancia(base_path)}

    for registro in registradas:
        nome = registro["ambiente"]
        conhecidas.add(nome.casefold())
        achada = no_disco.get(nome.casefold())
        if not registro.get("pasta") and achada is not None:
            registro = {**registro, "pasta": str(achada)}
        situacao = _situacao(registro, ambientes, online)
        itens.append({
            "ambiente": nome,
            "origem": registro.get("origem", ""),
            "banco": registro.get("banco", ""),
            "registrada": True,
            "situacao": situacao,
            "motivo": MOTIVOS[situacao],
            "removivel": situacao in REMOVIVEIS,
            "estado": registro.get("estado", ""),
            "caminhos": caminhos_ocupados(registro),
            "pasta": registro.get("pasta", ""),
            "workspace_banco": registro.get("workspace_banco", ""),
            "temp_pai": registro.get("temp_pai", ""),
        })

    # Pasta no disco sem registro: o caso de quem trocou o executável de lugar
    # ou reinstalou. O nome ainda diz de quem ela é.
    for pasta in pastas_de_instancia(base_path):
        if pasta.name.casefold() in conhecidas:
            continue
        origem, _slot = decompor(pasta.name)
        situacao = INDEFINIDA if not online else NAO_REGISTRADA
        itens.append({
            "ambiente": pasta.name,
            "origem": origem,
            "banco": "",
            "registrada": False,
            "situacao": situacao,
            "motivo": MOTIVOS[situacao],
            "removivel": situacao in REMOVIVEIS,
            "estado": "",
            "caminhos": [str(pasta)],
            "pasta": str(pasta),
            "workspace_banco": "",
            "temp_pai": "",
        })

    resumo = {}
    for item in itens:
        resumo[item["situacao"]] = resumo.get(item["situacao"], 0) + 1
    return {"ok": True, "online": online, "instancias": itens, "resumo": resumo}


def tamanho_em_disco(caminho: str | Path) -> int:
    """Bytes ocupados por uma árvore. Arquivo ilegível não derruba a conta."""
    total = 0
    for atual, _subdirs, arquivos in os.walk(caminho, onerror=lambda e: None):
        for arquivo in arquivos:
            try:
                total += (Path(atual) / arquivo).stat().st_size
            except OSError:
                continue
    return total


def medir(caminhos: list[str]) -> dict:
    """Espaço de uma instância, caminho a caminho.

    Separado do levantamento de propósito: percorrer um ambiente Protheus
    inteiro leva segundos, e a listagem precisa abrir na hora.
    """
    detalhe = {c: tamanho_em_disco(c) for c in caminhos if _existe(c)}
    return {"ok": True, "bytes": sum(detalhe.values()), "detalhe": detalhe}
