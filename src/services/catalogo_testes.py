"""Catálogo dos testes do TIR, lido da pasta de fontes.

Estrutura no disco:

    <raiz>\\<País>\\<MÓDULO>\\...\\<ROTINA>TESTSUITE.py
                             \\...\\<ROTINA>TESTCASE.py

**O que identifica um teste é o par de arquivos, não a pasta que o guarda.**
Exigir `Scripts Web` deixava de fora a maior parte do disco real: os fontes
antigos moram em `Scripts TIR`, e mesmo os novos costumam ficar um nível
abaixo, em `Scripts Web\\Suite`. Só no Brasil eram 248 rotinas vistas contra
1358 no disco. Agora a varredura desce a árvore inteira abaixo do módulo e casa
o nome sem olhar caixa — `TESTSUITE.py`, `TestSuite.py` e `TESTSUITE.PY`
existem todos.

O **módulo** continua sendo a primeira pasta abaixo do país (`SIGAFIN`); o
resto do caminho vira `subpasta`, só para exibição.

Os casos de teste saem do **TESTSUITE**, não do TESTCASE. Foi verificado no
repositório: o suite é a lista do que realmente roda (`suite.addTest(...)`),
enquanto vários TESTCASE não expõem os métodos de forma detectável — em
Colômbia e México, por exemplo, nenhum `def test_` aparece no arquivo do caso.

`PAD - Todos Paises` fica de fora: não é país e não faz parte destes testes.
"""

from __future__ import annotations

import logging
import os
import re
import unicodedata
from pathlib import Path

log = logging.getLogger(__name__)

NOME_RAIZ = "Automação Protheus"
PASTA_PAI = "Testes"
RAIZ_PADRAO = Path(r"C:\Dev\Fontes") / PASTA_PAI / NOME_RAIZ
# Mantida só como caminho canônico de exemplo: a varredura não a exige mais.
PASTA_WEB = "Scripts Web"
SUFIXO_SUITE = "TESTSUITE.py"
SUFIXO_CASE = "TESTCASE.py"

# Não é país. Fora do catálogo por decisão do usuário.
PASTAS_IGNORADAS = {"pad - todos paises"}

# Pastas puladas ao descer atrás dos fontes. `Dados` e `Mapa Mental` são as
# maiores do repositório e não guardam script nenhum; `Obsoletos` guarda teste
# aposentado, que não deve voltar para a lista por causa da varredura funda.
_PULAR_NA_VARREDURA = {
    "__pycache__", ".git", ".venv", "venv", "node_modules",
    "dados", "mapa mental", "mapamental", "obsoletos",
}

# `<ROTINA>TESTSUITE.py` em qualquer caixa, inclusive a extensão `.PY`.
_RE_SUITE = re.compile(r"^(?P<nome>.+)testsuite\.py$", re.IGNORECASE)

# `suite.addTest(COMA222("test_COMA222_CT006"))` — pega o nome entre aspas.
_RE_ADDTEST = re.compile(r"""addTest\s*\(\s*\w+\s*\(\s*["']([^"']+)["']""")
# Reserva: método declarado no próprio arquivo de caso.
_RE_DEF_TEST = re.compile(r"""^\s*def\s+(test_\w+)""", re.MULTILINE)


def _sem_acento(texto: str) -> str:
    """`Colômbia` e `Colombia` têm que casar: a tabela do Gerenciador tem
    acento e as pastas no disco nem sempre."""
    normal = unicodedata.normalize("NFKD", texto or "")
    return "".join(c for c in normal if not unicodedata.combining(c)).strip().casefold()


# ─────────────────────────────────────────────────────────────
# PAR DE ARQUIVOS
# ─────────────────────────────────────────────────────────────

def _nome_da_suite(arquivo: str) -> str | None:
    """Nome da rotina se o arquivo for um TESTSUITE; senão `None`."""
    achado = _RE_SUITE.match(arquivo)
    return achado.group("nome") if achado else None


def _achar_no_diretorio(pasta: Path, alvo: str) -> Path | None:
    """Arquivo de nome `alvo` (já em minúsculas) dentro de `pasta`."""
    try:
        for item in pasta.iterdir():
            if item.name.casefold() == alvo and item.is_file():
                return item
    except OSError:
        pass
    return None


def _case_ao_lado(suite: Path) -> Path:
    """TESTCASE do par, procurado sem olhar caixa.

    O par nem sempre divide a pasta com o suite: o arranjo mais comum no disco
    é `Scripts Web\\Suite\\X TESTSUITE.py` com o caso em `Scripts Web\\Cases`
    (ou `Case`) — 767 das 775 rotinas que apareciam “sem TESTCASE” estavam
    assim. Por isso a procura sobe um nível e olha as pastas irmãs, sempre por
    nome exato de arquivo, o que não deixa margem para pegar o caso errado.

    Devolve o caminho canônico ao lado do suite quando não acha nada — é o que
    aparece na mensagem de “sem TESTCASE”, e `.exists()` responde `False`.
    """
    nome = _nome_da_suite(suite.name) or suite.stem
    alvo = f"{nome}{SUFIXO_CASE}".casefold()

    achado = _achar_no_diretorio(suite.parent, alvo)
    if achado is not None:
        return achado

    pai = suite.parent.parent
    achado = _achar_no_diretorio(pai, alvo)
    if achado is not None:
        return achado

    try:
        irmas = sorted((p for p in pai.iterdir()
                        if p.is_dir() and p != suite.parent
                        and p.name.casefold() not in _PULAR_NA_VARREDURA),
                       key=lambda p: p.name.casefold())
    except OSError:
        irmas = []
    for irma in irmas:
        achado = _achar_no_diretorio(irma, alvo)
        if achado is not None:
            return achado

    return suite.with_name(nome + SUFIXO_CASE)


def _varrer_suites(pasta: Path, limite: int | None = None):
    """Todo TESTSUITE abaixo de `pasta`, em qualquer profundidade.

    `limite` corta a descida em N níveis — serve para a validação da raiz, que
    só precisa saber se existe algum, não achar todos.
    """
    base = len(pasta.parts)
    for atual, subdirs, arquivos in os.walk(pasta):
        fundo = limite is not None and len(Path(atual).parts) - base >= limite
        subdirs[:] = [] if fundo else sorted(
            d for d in subdirs if d.casefold() not in _PULAR_NA_VARREDURA)
        for arquivo in sorted(arquivos, key=str.casefold):
            if _nome_da_suite(arquivo):
                yield Path(atual) / arquivo


# ─────────────────────────────────────────────────────────────
# DESCOBERTA DA RAIZ
# ─────────────────────────────────────────────────────────────
# O download automático deixa os fontes na pasta do usuário, mas nada impede
# de moverem para outro lugar. Em vez de exigir configuração de todo mundo, o
# caminho é procurado nos lugares prováveis; o campo da tela sobrepõe quando
# o usuário aponta à mão.

def _bases_provaveis() -> list[Path]:
    perfil = Path.home()
    bases = [perfil, perfil / "Documents", perfil / "Downloads",
             perfil / "Desktop", perfil / "OneDrive"]
    if os.name == "nt":
        for letra in ("C", "D", "E"):
            raiz = Path(f"{letra}:\\")
            if raiz.exists():
                bases.append(raiz)
    return bases


# Dois níveis de pasta intermediária. Um não bastava: o caso real desta
# máquina é `C:\Dev\Fontes\Testes\Automação Protheus`, com `Dev` e `Fontes`
# no meio. Três níveis já custaria varredura perceptível no boot.
PROFUNDIDADE = 2

# Pastas grandes e sem chance de conter fontes de teste. Pular economiza a
# maior parte do trabalho quando a base é a raiz de um disco.
_IGNORAR_NA_BUSCA = {
    "windows", "$recycle.bin", "system volume information", "programdata",
    "program files", "program files (x86)", "appdata", "node_modules",
    ".git", ".venv", "venv", "__pycache__", "onedrivetemp",
}

# Quantos níveis abaixo do país a validação da raiz desce atrás do primeiro
# TESTSUITE. Cobre `<MÓDULO>\Scripts Web\Suite\` com folga; sem o teto, uma
# pasta parecida e sem teste nenhum custaria a varredura inteira.
PROFUNDIDADE_VALIDACAO = 4


def _subpastas(base: Path) -> list[Path]:
    try:
        return [p for p in base.iterdir()
                if p.is_dir() and p.name.casefold() not in _IGNORAR_NA_BUSCA]
    except OSError:
        return []


def _candidatos(base: Path):
    """`<base>/Testes/<raiz>`, com até `PROFUNDIDADE` pastas no meio."""
    nivel = [base]
    for _ in range(PROFUNDIDADE + 1):
        for pasta in nivel:
            yield pasta / PASTA_PAI / NOME_RAIZ
        seguinte = []
        for pasta in nivel:
            seguinte.extend(_subpastas(pasta)[:80])
        if not seguinte:
            return
        nivel = seguinte


def _e_raiz_valida(caminho: Path) -> bool:
    """Tem cara de raiz de testes: existe e traz país com algum TESTSUITE."""
    if not caminho.is_dir():
        return False
    try:
        for pais in caminho.iterdir():
            if not pais.is_dir() or _sem_acento(pais.name) in PASTAS_IGNORADAS:
                continue
            for _ in _varrer_suites(pais, limite=PROFUNDIDADE_VALIDACAO):
                return True
    except OSError:
        return False
    return False


def descobrir_raiz(sugestao: str | Path | None = None) -> Path | None:
    """Primeira raiz de testes encontrada, ou None.

    A sugestão (valor guardado nas preferências) é testada antes de qualquer
    busca — apontar à mão tem que ser mais rápido e mais forte que adivinhar.
    """
    if sugestao:
        caminho = Path(sugestao)
        if _e_raiz_valida(caminho):
            return caminho

    vistos = set()
    for base in _bases_provaveis():
        for candidato in _candidatos(base):
            if candidato in vistos:
                continue
            vistos.add(candidato)
            if _e_raiz_valida(candidato):
                log.info("[CATALOGO] Raiz dos testes encontrada em %s", candidato)
                return candidato
    return None


def pasta_do_pais(raiz: Path, pais: str) -> Path | None:
    """Acha a pasta do país tolerando acento e caixa."""
    alvo = _sem_acento(pais)
    if not alvo or not raiz.is_dir():
        return None
    for item in raiz.iterdir():
        if not item.is_dir():
            continue
        if _sem_acento(item.name) in PASTAS_IGNORADAS:
            continue
        if _sem_acento(item.name) == alvo:
            return item
    return None


def casos_do_suite(suite: Path, case: Path | None = None) -> list[str]:
    """Casos que o suite executa, na ordem em que ele os adiciona.

    `case` evita procurar o par de novo quando quem chama já o achou.
    """
    suite = Path(suite)
    try:
        texto = suite.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        log.warning("[CATALOGO] Não foi possível ler %s: %s", suite.name, e)
        return []

    casos, vistos = [], set()
    for nome in _RE_ADDTEST.findall(texto):
        if nome not in vistos:
            vistos.add(nome)
            casos.append(nome)
    if casos:
        return casos

    # Suite sem `addTest` legível: cai para os métodos do arquivo de caso, que
    # é uma aproximação — pode listar caso que o suite não roda.
    case = Path(case) if case is not None else _case_ao_lado(suite)
    if case.exists():
        try:
            return _RE_DEF_TEST.findall(case.read_text(encoding="utf-8",
                                                       errors="replace"))
        except OSError:
            pass
    return []


def escanear_pais(raiz: Path, pais: str) -> dict:
    """Rotinas de um país, agrupadas por módulo.

    Devolve `{"ok": True, "pais": ..., "rotinas": [...]}`. Cada rotina traz
    módulo, nome unificado (sem TESTSUITE/TESTCASE) e os casos.
    """
    raiz = Path(raiz)
    if not raiz.is_dir():
        return {"ok": False, "erro": f"Raiz dos testes não encontrada: {raiz}"}

    pasta = pasta_do_pais(raiz, pais)
    if pasta is None:
        return {"ok": False, "erro": f"Sem pasta de testes para “{pais}”.",
                "pais": pais, "rotinas": []}

    rotinas, vistos = [], {}
    for suite in _varrer_suites(pasta):
        nome = _nome_da_suite(suite.name)
        if not nome:
            continue
        relativo = suite.parent.relative_to(pasta).parts
        # Sem pasta de módulo o arquivo está solto na raiz do país; o país
        # serve de módulo para não sumir da lista.
        modulo = relativo[0] if relativo else pasta.name
        chave = (_sem_acento(modulo), nome.casefold())
        if chave in vistos:
            # Mesmo módulo, mesmo nome, pastas diferentes: o disco não tem
            # nenhum caso assim hoje, mas o primeiro vence e o segundo fica
            # registrado — sumir calado viraria rotina fantasma.
            log.warning("[CATALOGO] %s/%s repetida; mantida %s, ignorada %s",
                        modulo, nome, vistos[chave], suite)
            continue
        vistos[chave] = suite

        case = _case_ao_lado(suite)
        rotinas.append({
            "rotina": nome,
            "modulo": modulo,
            "pais": pasta.name,
            # Resto do caminho abaixo do módulo (`Scripts Web\Suite`), só para
            # exibição: ajuda a distinguir teste antigo de teste novo.
            "subpasta": str(Path(*relativo[1:])) if len(relativo) > 1 else "",
            "casos": casos_do_suite(suite, case),
            "suite": str(suite),
            "case": str(case),
            # Selecionar a rotina implica rodar os dois arquivos; sem o
            # caso, o suite quebra no import. Melhor avisar antes.
            "tem_case": case.exists(),
        })

    rotinas.sort(key=lambda r: (_sem_acento(r["modulo"]), r["rotina"].casefold()))

    homonimas = len(rotinas) - len({r["rotina"].casefold() for r in rotinas})
    if homonimas:
        # A seleção é gravada por nome; nome repetido em módulos diferentes
        # significa que marcar um marca o outro.
        log.warning("[CATALOGO] %s: %d rotina(s) com nome repetido entre módulos.",
                    pasta.name, homonimas)

    log.info("[CATALOGO] %s: %d rotinas em %d módulos.", pasta.name, len(rotinas),
             len({r["modulo"] for r in rotinas}))
    return {"ok": True, "pais": pasta.name, "rotinas": rotinas}


def filtrar(rotinas: list[dict], busca: str) -> list[dict]:
    """Busca do tipo “contém”, sem acento e sem caixa.

    Procura no nome da rotina e no módulo: digitar `222` acha COMA222, digitar
    `fin` acha o módulo SIGAFIN inteiro.
    """
    alvo = _sem_acento(busca)
    if not alvo:
        return list(rotinas)
    return [r for r in rotinas
            if alvo in _sem_acento(r["rotina"]) or alvo in _sem_acento(r["modulo"])]
