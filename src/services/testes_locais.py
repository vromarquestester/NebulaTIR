"""Testes locais: uma pasta escolhida pelo usuário, fora da árvore dos fontes.

A aba "Casos de teste" tem duas origens. **Pelos fontes** é o catálogo por
país (`catalogo_testes`). **Locais** é isto aqui: teste escrito pela equipe de
desenvolvimento ou de QA, ou um script avulso para experimentar — qualquer
pasta que siga a estrutura padrão do TIR:

    <pasta>\\<ROTINA>TESTSUITE.py
            \\<ROTINA>TESTCASE.py
            \\config.json

Um teste é o par TESTSUITE/TESTCASE (a mesma regra do catálogo); a pasta pode
ter N pares. O `config.json` é **um por pasta** e é o que manda na execução —
ele não é gerado nem sobrescrito. O que este módulo faz com ele é conferir os
campos que fazem o TIR não passar do login aqui (`POUILogin`, `DebugLog`,
`Language` do país do ambiente) e o navegador recomendado (`Firefox`), e
devolver a lista do que diverge. Corrigir é decisão do usuário, feita na tela.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from services import catalogo_testes

log = logging.getLogger(__name__)

ARQUIVO_CONFIG = "config.json"

# Campos conferidos no `config.json` da pasta, na ordem em que aparecem na
# mensagem. `nivel` separa o que quebra a corrida do que é só recomendação.
CAMPOS = [
    {"chave": "Language", "rotulo": "Idioma",
     "nivel": "erro",
     "motivo": "É o idioma do país do ambiente. Com outro, o TIR procura os "
               "rótulos das telas na língua errada e falha em todo SetValue."},
    {"chave": "POUILogin", "rotulo": "Login POUI", "esperado": True,
     "nivel": "erro",
     "motivo": "Os ambientes atendidos aqui sobem com a tela de entrada POUI; "
               "desligado, o TIR procura o campo de usuário do WebApp "
               "clássico e não passa do login."},
    {"chave": "DebugLog", "rotulo": "Log de depuração", "esperado": True,
     "nivel": "erro",
     "motivo": "É o log que o relatório do NebulaTIR lê. Sem ele a corrida "
               "termina sem evidência."},
    {"chave": "Browser", "rotulo": "Navegador", "esperado": "Firefox",
     "nivel": "aviso",
     "motivo": "Recomendado: o TIR roda melhor no Firefox. O Chrome se "
               "atualiza sozinho e deixa o ChromeDriver para trás."},
]


def esperado_para(idioma: str) -> dict:
    """Valores esperados no `config.json`, dado o idioma do ambiente."""
    valores = {c["chave"]: c.get("esperado") for c in CAMPOS}
    valores["Language"] = idioma or ""
    return valores


# ─────────────────────────────────────────────────────────────
# VARREDURA
# ─────────────────────────────────────────────────────────────

def escanear(pasta: str | Path) -> dict:
    """Testes (pares) e `config.json` da pasta.

    Desce a árvore inteira abaixo da pasta, como o catálogo: quem organiza o
    teste em `Suite\\` e `Cases\\` não pode ficar de fora. O `config.json`
    vale o da raiz da pasta; sem ele, o primeiro encontrado descendo, que é o
    arranjo de quem copia a pasta inteira de uma rotina.
    """
    texto = str(pasta or "").strip()
    pasta = Path(texto or "?")
    # `Path("")` é `.`, a pasta do programa — varrer isso listaria os testes
    # de corridas anteriores como se fossem do usuário.
    if not texto or not pasta.is_dir():
        return {"ok": False, "erro": f"Pasta não encontrada: {texto or '(vazia)'}",
                "pasta": texto, "testes": [], "config": _sem_config(pasta)}

    testes, vistos = [], {}
    for suite in catalogo_testes._varrer_suites(pasta):
        nome = catalogo_testes._nome_da_suite(suite.name)
        if not nome:
            continue
        chave = nome.casefold()
        if chave in vistos:
            # Mesmo nome em duas subpastas: a execução usa o nome como pasta
            # de trabalho, então só um pode valer. O primeiro vence e o outro
            # fica no log — sumir calado viraria teste fantasma.
            log.warning("[LOCAL] %s repetida; mantida %s, ignorada %s",
                        nome, vistos[chave], suite)
            continue
        vistos[chave] = suite
        case = catalogo_testes._case_ao_lado(suite)
        relativo = suite.parent.relative_to(pasta)
        testes.append({
            "rotina": nome,
            # O catálogo tem módulo; aqui o equivalente é a subpasta, só para
            # distinguir dois testes quando a pasta tem vários.
            "modulo": str(relativo) if str(relativo) != "." else "",
            "subpasta": str(relativo) if str(relativo) != "." else "",
            "casos": catalogo_testes.casos_do_suite(suite, case),
            "suite": str(suite),
            "case": str(case),
            "tem_case": case.exists(),
        })
    testes.sort(key=lambda t: t["rotina"].casefold())

    config = _achar_config(pasta)
    log.info("[LOCAL] %s: %d teste(s); config %s.", pasta, len(testes),
             config["caminho"] if config["existe"] else "ausente")
    return {"ok": True, "pasta": str(pasta), "testes": testes, "config": config}


def _sem_config(pasta: Path) -> dict:
    return {"caminho": str(pasta / ARQUIVO_CONFIG), "existe": False,
            "conteudo": None, "erro": ""}


def _achar_config(pasta: Path) -> dict:
    candidato = pasta / ARQUIVO_CONFIG
    if not candidato.is_file():
        candidato = None
        for atual, subdirs, arquivos in os.walk(pasta):
            subdirs[:] = sorted(
                d for d in subdirs
                if d.casefold() not in catalogo_testes._PULAR_NA_VARREDURA)
            for arquivo in arquivos:
                if arquivo.casefold() == ARQUIVO_CONFIG:
                    candidato = Path(atual) / arquivo
                    break
            if candidato is not None:
                break
    if candidato is None:
        return _sem_config(pasta)
    conteudo, erro = ler_config(candidato)
    return {"caminho": str(candidato), "existe": True,
            "conteudo": conteudo, "erro": erro}


def ler_config(caminho: str | Path) -> tuple[dict | None, str]:
    """Conteúdo do `config.json` como dicionário, ou (None, motivo)."""
    caminho = Path(caminho)
    try:
        texto = caminho.read_text(encoding="utf-8-sig")
    except OSError as e:
        return None, f"Não foi possível ler {caminho.name}: {e}"
    try:
        dados = json.loads(texto)
    except ValueError as e:
        return None, f"{caminho.name} não é um JSON válido: {e}"
    if not isinstance(dados, dict):
        return None, f"{caminho.name} precisa ser um objeto JSON."
    return dados, ""


# ─────────────────────────────────────────────────────────────
# VALIDAÇÃO
# ─────────────────────────────────────────────────────────────

def _igual(chave: str, encontrado, esperado) -> bool:
    if isinstance(esperado, bool):
        if isinstance(encontrado, bool):
            return encontrado is esperado
        return str(encontrado).strip().lower() in ("true", "1", "on") if esperado \
            else str(encontrado).strip().lower() in ("false", "0", "off")
    return str(encontrado or "").strip().casefold() == str(esperado or "").casefold()


def _texto(valor) -> str:
    """Como o valor aparece na mensagem: `true`/`false` como no JSON."""
    if valor is None:
        return "ausente"
    if isinstance(valor, bool):
        return "true" if valor else "false"
    texto = str(valor).strip()
    return texto if texto else "vazio"


def validar(config: dict | None, idioma: str) -> list[dict]:
    """O que diverge do esperado no `config.json`. Lista vazia = tudo certo.

    `idioma` é o do país do ambiente selecionado; vazio pula a conferência de
    idioma — sem saber o país não há como dizer que está errado.
    """
    esperados = esperado_para(idioma)
    divergencias = []
    for campo in CAMPOS:
        chave = campo["chave"]
        esperado = esperados[chave]
        if esperado in (None, ""):
            continue
        encontrado = _valor(config, chave)
        if _igual(chave, encontrado, esperado):
            continue
        divergencias.append({
            "chave": chave,
            "rotulo": campo["rotulo"],
            "encontrado": _texto(encontrado),
            "esperado": _texto(esperado),
            "nivel": campo["nivel"],
            "motivo": campo["motivo"],
        })
    return divergencias


def _valor(config: dict | None, chave: str):
    """Valor da chave sem olhar caixa: `language` e `Language` são a mesma."""
    if not config:
        return None
    if chave in config:
        return config[chave]
    for k, v in config.items():
        if str(k).casefold() == chave.casefold():
            return v
    return None


def corrigir(caminho: str | Path, idioma: str) -> dict:
    """Grava no `config.json` os valores esperados, mantendo o resto.

    Só mexe no que diverge; chave grafada em outra caixa é trocada pela
    grafia do TIR, que é a que ele lê.
    """
    caminho = Path(caminho)
    config, erro = ler_config(caminho)
    if config is None:
        return {"ok": False, "erro": erro}

    esperados = esperado_para(idioma)
    alteradas = []
    for div in validar(config, idioma):
        chave = div["chave"]
        for k in [k for k in config if str(k).casefold() == chave.casefold()
                  and k != chave]:
            config.pop(k)
        config[chave] = esperados[chave]
        alteradas.append(chave)

    if alteradas:
        try:
            caminho.write_text(json.dumps(config, ensure_ascii=False, indent=2)
                               + "\n", encoding="utf-8")
        except OSError as e:
            return {"ok": False, "erro": f"Não foi possível gravar "
                                         f"{caminho.name}: {e}"}
        log.info("[LOCAL] %s ajustado: %s", caminho, ", ".join(alteradas))
    return {"ok": True, "alteradas": alteradas, "config": config}
