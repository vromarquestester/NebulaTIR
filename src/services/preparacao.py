"""Monta a pasta de execução de cada rotina, ao lado do executável.

    <pasta do programa>\\tests\\<ambiente base>\\<rotina>\\
        <ROTINA>TESTSUITE.py     cópia do fonte
        <ROTINA>TESTCASE.py      cópia do fonte
        config.json              gerado a partir da configuração da instância
        nebula_run.py            lançador embutido
        tir_report.py            dependência do lançador
        nebula_parser*.py        leitura do log (veio do LogNebula)
        nebula_exporter.py       geração do PNG (veio do LogNebula)
        assets\\fonts\\          fontes do relatório
        log\\                    LogFolder desta rotina

Por que copiar em vez de executar no lugar: os fontes vão para code review e
rodam em esteira, então não podem receber `config.json` nem pasta de log ao
lado. A cópia isola a execução e deixa o repositório de testes intocado.

**A pasta é do ambiente BASE, não da instância paralela.** Separar por
instância espalharia o mesmo trabalho em três árvores quase idênticas, e o
resultado de um teste não pertence à instância que calhou de rodá-lo.

**Mas o `config.json` é por instância.** A premissa antiga — "cada rotina roda
numa instância só, então o config da pasta é o dela" — morreu quando a divisão
de casos entrou: duas fatias da MESMA rotina rodam ao mesmo tempo, na mesma
pasta. As duas escreviam `config.json` uma por cima da outra, e as duas
acabavam apontando para a URL da última a gravar. Os dois navegadores batiam
no mesmo AppServer enquanto o outro ficava ocioso — com RPO divergente,
`REST could not be initialized!` e nenhum teste avançando. Como o vencedor da
corrida de escrita muda a cada corrida, o sintoma era intermitente.

Por isso o arquivo leva o nome da instância (`config.<instancia>.json`) e o
lançador recebe `--config`. A pasta continua sendo uma só.
"""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path

from services import config_tir
from services.recursos import pasta_do_programa, recurso

log = logging.getLogger(__name__)

NOME_TESTS = "tests"
NOME_LOG = "log"
NOME_PARCIAIS = "parciais"      # resultados por caso, na execução dividida
ARQUIVO_CONFIG = "config.json"

# Vão junto para a pasta do teste: o lançador roda com a pasta como diretório
# atual, e é lá que ele precisa achar as dependências. São só arquivos `.py` —
# o LogNebula deixou de vir como executável e virou código incorporado.
ANEXOS = ("nebula_run.py", "nebula_merge.py", "tir_report.py",
          "nebula_parser.py", "nebula_parser_tir.py", "nebula_exporter.py")

# As fontes do relatório PNG. Ficam numa subpasta porque o exportador as
# procura em `assets/fonts/`.
ANEXOS_ASSETS = ("assets/fonts/DejaVuSans.ttf", "assets/fonts/DejaVuSans-Bold.ttf")


def raiz_execucao() -> Path:
    return pasta_do_programa() / NOME_TESTS


def pasta_da_rotina(ambiente: str, rotina: str) -> Path:
    return raiz_execucao() / ambiente / rotina


def pasta_de_log(ambiente: str, rotina: str) -> Path:
    return pasta_da_rotina(ambiente, rotina) / NOME_LOG


def nome_do_config(instancia: str = "") -> str:
    """`config.json`, ou `config.<instancia>.json` quando há paralelo.

    O nome sai do ambiente da instância (`PAR2510_V1_TIR2`), que é único por
    definição — é o que garante que duas fatias da mesma rotina não escrevam
    no mesmo arquivo.
    """
    limpo = "".join(c for c in (instancia or "")
                    if c.isalnum() or c in "-_").strip("-_")
    return f"config.{limpo}.json" if limpo else ARQUIVO_CONFIG


def _origem_do_anexo(nome: str) -> Path | None:
    partes = nome.split("/")
    return (recurso("src", "services", "tir", *partes)
            or recurso("services", "tir", *partes))


def _copiar_anexos(destino: Path) -> list[str]:
    faltando = []
    for nome in ANEXOS:
        origem = _origem_do_anexo(nome)
        if origem is None:
            faltando.append(nome)
            continue
        shutil.copy2(origem, destino / nome)

    for nome in ANEXOS_ASSETS:
        origem = _origem_do_anexo(nome)
        if origem is None:
            faltando.append(nome)
            continue
        alvo = destino / nome
        alvo.parent.mkdir(parents=True, exist_ok=True)
        # Fonte não muda; recopiar 700 KB a cada preparo seria desperdício.
        if not alvo.exists() or alvo.stat().st_size != origem.stat().st_size:
            shutil.copy2(origem, alvo)
    return faltando


def preparar_rotina(ambiente: str, rotina: dict, config: dict,
                    instancia: str = "") -> dict:
    """Cria a pasta da rotina e devolve os caminhos que a execução usa.

    `rotina` vem do catálogo (`suite`, `case`, `rotina`, `modulo`); `config` é
    a configuração do TIR do ambiente, já normalizada.

    `instancia` é o ambiente paralelo que vai rodar (`PAR2510_V1_TIR2`). Ela
    nomeia o `config.json`: sem isso, duas fatias da mesma rotina rodando em
    instâncias diferentes sobrescrevem o arquivo uma da outra e acabam as duas
    no mesmo AppServer.
    """
    nome = rotina.get("rotina") or ""
    suite = Path(rotina.get("suite") or "")
    case = Path(rotina.get("case") or "")
    if not nome:
        return {"ok": False, "erro": "Rotina sem nome."}
    if not suite.is_file():
        return {"ok": False, "erro": f"TESTSUITE não encontrado: {suite}"}
    if not case.is_file():
        # Sem o caso, o suite quebra no import — melhor parar aqui, com o
        # motivo, do que no meio da execução.
        return {"ok": False, "erro": f"TESTCASE não encontrado: {case}"}

    destino = pasta_da_rotina(ambiente, nome)
    pasta_log = destino / NOME_LOG
    pasta_log.mkdir(parents=True, exist_ok=True)

    shutil.copy2(suite, destino / suite.name)
    shutil.copy2(case, destino / case.name)
    faltando = _copiar_anexos(destino)

    # O LogFolder aponta para a pasta desta rotina — é o que separa os logs de
    # COMA222 dos de MATA101N, e o que o usuário pediu.
    config_final = config_tir.normalizar({**config, "LogFolder": str(pasta_log)})

    arquivo_config = destino / nome_do_config(instancia)
    arquivo_config.write_text(
        json.dumps(config_final, ensure_ascii=False, indent=2), encoding="utf-8")

    log.info("[PREPARO] %s/%s pronto em %s (config: %s)", ambiente, nome,
             destino, arquivo_config.name)
    return {
        "ok": True,
        "rotina": nome,
        "pasta": str(destino),
        "suite": str(destino / suite.name),
        "config": str(arquivo_config),
        "log": str(pasta_log),
        "faltando": faltando,
    }


def preparar_selecao(ambiente: str, rotinas: list[dict], config: dict) -> dict:
    """Prepara todas as rotinas escolhidas. Erro numa não impede as outras."""
    prontas, erros = [], []
    for rotina in rotinas:
        resultado = preparar_rotina(ambiente, rotina, config)
        if resultado.get("ok"):
            prontas.append(resultado)
        else:
            erros.append({"rotina": rotina.get("rotina", "?"),
                          "erro": resultado.get("erro", "")})
    return {"ok": bool(prontas), "prontas": prontas, "erros": erros,
            "raiz": str(raiz_execucao())}


def limpar_ambiente(ambiente: str) -> dict:
    """Apaga a pasta de execução de um ambiente. Não toca nos fontes."""
    alvo = raiz_execucao() / ambiente
    if not alvo.is_dir():
        return {"ok": True, "removido": False}
    shutil.rmtree(alvo, ignore_errors=True)
    log.info("[PREPARO] Pasta de execução de %s removida.", ambiente)
    return {"ok": True, "removido": True, "pasta": str(alvo)}
