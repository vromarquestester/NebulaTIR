"""Runner de testsuites TIR que grava o resultado da execução em arquivo .log.

O TIR só escreve arquivo quando "DebugLog": true está no config.json, e mesmo assim
o que ele grava é o trace DEBUG completo — que não registra o nome do caso de teste
quando a execução passa. Este módulo resolve isso pelo lado do unittest: coleta
nome/resultado/tempo de cada caso e grava um .log no formato que o LogNebula lê,
tanto em execução bem-sucedida quanto com falha.

Os arquivos de teste passam por code review e rodam em esteira, então não podem ser
alterados. Por isso o uso normal é através do nebula_run.py, que intercepta o runner
do testsuite sem tocar no arquivo. A função `run` abaixo existe para o caso — hoje não
aplicável — de um testsuite que possa chamar este módulo diretamente.

A pasta de destino é a `LogFolder` do config.json em uso; se não houver, uma subpasta
`Log` no diretório de execução.
"""
import json
import os
import re
import sys
import time
import unittest
from datetime import datetime
from pathlib import Path

FERRAMENTA = "TIR"

# Limite do texto de erro gravado por caso. Traceback inteiro estoura a largura da
# tabela que o LogNebula desenha.
MAX_MENSAGEM = 300

# Variável de ambiente que aponta o config.json a usar, ignorando a busca automática.
VAR_CONFIG = "TIR_CONFIG"
NOME_CONFIG = "config.json"

# Prefixo das linhas de progresso na saída padrão. É o único canal que existe
# em tempo real: o resultado só é conhecido no fim, e um caso do TIR leva
# minutos. O painel do NebulaTIR lê estas linhas para pintar a árvore.
MARCA_PROGRESSO = "[nebula_caso]"


def _procura_config(inicio: Path) -> Path | None:
    """Primeiro config.json subindo a árvore de diretórios a partir de `inicio`."""
    for pasta in [inicio, *inicio.parents]:
        candidato = pasta / NOME_CONFIG
        if candidato.is_file():
            return candidato
    return None


def config(path=None, carregar=True, inicio=None) -> str:
    """Resolve qual config.json usar e devolve o caminho para `Webapp(config_path=...)`.

    Ordem: o parâmetro > a variável de ambiente TIR_CONFIG > o primeiro config.json
    encontrado subindo a partir da pasta do testsuite. Assim dá para manter um único
    arquivo na raiz e espalhar os testes em subpastas por módulo.

    Devolve "" quando não acha nada, que é o padrão do TIR — o comportamento antigo
    (config.json ao lado do testsuite) continua valendo.

    :param path: caminho explícito, tem prioridade sobre tudo.
    :param carregar: além de resolver, já instancia o ConfigLoader do TIR. Evita que
        o logging_config do TIR procure um config.json relativo ao diretório atual.
    :param inicio: pasta onde começar a busca. Necessário quando quem chama não é o
        testsuite — o nebula_run.py roda de outra pasta e precisa apontar a do teste.
    """
    escolhido = None

    if path:
        escolhido = Path(path)
    elif os.environ.get(VAR_CONFIG):
        escolhido = Path(os.environ[VAR_CONFIG])
    else:
        if inicio:
            origem = str(inicio)
        else:
            origem = sys.path[0] if sys.path and sys.path[0] else os.getcwd()
        escolhido = _procura_config(Path(origem).resolve())

    if not escolhido or not escolhido.is_file():
        return ""

    escolhido = escolhido.resolve()

    if carregar:
        try:
            from tir.technologies.core.config import ConfigLoader
            ConfigLoader(str(escolhido))
        except Exception as e:
            print(f"[tir_report] Nao foi possivel pre-carregar {escolhido}: {e}", file=sys.stderr)

    return str(escolhido)


def _versao_tir() -> str:
    """Versão instalada do tir-framework. Vazio se não der para descobrir."""
    try:
        from importlib.metadata import version
        return version("tir_framework")
    except Exception:
        return ""


def _nome_suite(explicito: str | None) -> str:
    """Nome da suite: o parâmetro, senão o nome do arquivo que está executando."""
    if explicito:
        return explicito
    origem = sys.argv[0] if sys.argv and sys.argv[0] else ""
    nome = Path(origem).stem
    return nome if nome else "TESTSUITE"


def _pasta_saida(explicito: str | None) -> Path:
    """LogFolder do config.json em uso, senão ./Log. Cria a pasta se faltar."""
    if explicito:
        destino = Path(explicito)
    else:
        destino = None
        arquivo = config(carregar=False)
        if arquivo:
            try:
                with open(arquivo, "r", encoding="utf-8") as f:
                    dados = json.load(f)
                if dados.get("LogFolder"):
                    destino = Path(str(dados["LogFolder"]))
            except Exception as e:
                print(f"[tir_report] Ignorando LogFolder de {arquivo}: {e}", file=sys.stderr)
                destino = None
        if destino is None:
            destino = Path.cwd() / "Log"

    destino.mkdir(parents=True, exist_ok=True)
    return destino


def _limpa(texto: str) -> str:
    """Deixa a mensagem em uma linha só, sem quebrar o parser do LogNebula."""
    if not texto:
        return ""
    texto = re.sub(r"\s+", " ", texto).strip()
    # "Tempo do Teste" e "Mensagens:" são delimitadores do formato: não podem
    # aparecer dentro do texto da mensagem.
    texto = texto.replace("Tempo do Teste", "Tempo do teste").replace("Mensagens:", "Mensagens")
    if len(texto) > MAX_MENSAGEM:
        texto = texto[:MAX_MENSAGEM - 3] + "..."
    return texto


def _iso(carimbo) -> str:
    """Carimbo em ISO, venha ele como datetime ou como texto.

    Na execução direta o registro carrega um `datetime`. Vindo de uma parcial
    (rotina dividida entre instâncias), ele passou por JSON e chega como
    **string** — e `str.isoformat()` não existe. Sem esta função o
    `nebula_merge` estourava aqui dentro do `gravar`, que já tinha aberto o
    arquivo em modo "w": o resultado era um .log de zero byte e nenhum PNG.
    """
    if hasattr(carimbo, "isoformat"):
        return carimbo.isoformat()
    return str(carimbo or "")


def _duracao(segundos: float) -> str:
    total = int(round(segundos))
    return f"{total // 3600:02d}:{(total % 3600) // 60:02d}:{total % 60:02d}"


class _ResultadoDetalhado(unittest.TextTestResult):
    """TextTestResult que guarda nome, situação, tempo e mensagem de cada caso."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.registros = []
        self._inicio = None
        self._carimbo = None
        self._passou = True
        self._mensagem = ""

    def startTest(self, test):
        self._inicio = time.perf_counter()
        self._carimbo = datetime.now().astimezone()
        self._passou = True
        self._mensagem = ""
        # Marcador de progresso: quem lê a saída do lançador (o painel do
        # NebulaTIR) descobre por aqui qual caso está rodando AGORA. O
        # resultado só existe no fim, e uma suite leva minutos por caso.
        print(f"{MARCA_PROGRESSO} INICIO {getattr(test, '_testMethodName', test)}",
              flush=True)
        super().startTest(test)

    def addSuccess(self, test):
        super().addSuccess(test)

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self._passou = False
        self._mensagem = self._ultima_linha(err, test)

    def addError(self, test, err):
        super().addError(test, err)
        self._passou = False
        self._mensagem = self._ultima_linha(err, test)

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        self._passou = False
        self._mensagem = f"Ignorado: {reason}"

    def stopTest(self, test):
        decorrido = time.perf_counter() - self._inicio if self._inicio else 0.0
        nome = getattr(test, "_testMethodName", str(test))
        self.registros.append({
            "nome": nome,
            "passou": self._passou,
            "carimbo": self._carimbo,
            "segundos": decorrido,
            "mensagem": _limpa(self._mensagem),
        })
        print(f"{MARCA_PROGRESSO} FIM {nome} "
              f"{'ok' if self._passou else 'erro'}", flush=True)
        super().stopTest(test)

    def _ultima_linha(self, err, test):
        """Última linha útil do traceback — normalmente o AssertionError do TIR."""
        texto = self._exc_info_to_string(err, test)
        linhas = [l.strip() for l in texto.splitlines() if l.strip()]
        return linhas[-1] if linhas else ""


def _monta_log(nome_suite: str, registros: list, inicio, fim) -> str:
    """Gera o conteúdo do .log no formato consumido pelo LogNebula."""
    passou = sum(1 for r in registros if r["passou"])
    falhou = len(registros) - passou
    versao = _versao_tir()

    linhas = [
        f"{_iso(inicio)} Inicio da suite: {nome_suite}",
        f"Ferramenta: {FERRAMENTA} {versao}".strip(),
        f"Estacao: {os.environ.get('COMPUTERNAME', '')}",
        "",
    ]

    for r in registros:
        situacao = "passou" if r["passou"] else "falhou"
        linhas.append(f"{_iso(r['carimbo'])} Caso de teste '{r['nome']}': {situacao}.")
        linhas.append(f"Mensagens: {r['mensagem']}")
        linhas.append(f"Tempo do Teste ({r['nome']}) foi {_duracao(r['segundos'])}")
        linhas.append("")

    linhas.append(f"Total:  {len(registros)}")
    linhas.append(f"Passou: {passou}")
    linhas.append(f"Falhou: {falhou}")
    linhas.append(f"{_iso(fim)} Fim da suite: {nome_suite}")
    linhas.append("")

    return "\n".join(linhas)


def gravar(nome_suite: str, registros: list, inicio, fim, out_dir=None) -> Path | None:
    """Grava o .log da execução e devolve o caminho. None se não conseguir gravar.

    Separado de `run` porque o nebula_run.py monta os registros por conta própria,
    interceptando o runner do testsuite sem alterar o arquivo de teste.
    """
    try:
        # O texto é montado ANTES de abrir o arquivo: falha aqui dentro com o
        # arquivo já aberto em "w" deixava um .log de zero byte no disco, que
        # parece log gerado e não é.
        conteudo = _monta_log(nome_suite, registros, inicio, fim)
        pasta = _pasta_saida(out_dir)
        carimbo = inicio.strftime('%Y%m%d%H%M%S') if hasattr(inicio, "strftime") \
            else datetime.now().strftime('%Y%m%d%H%M%S')
        arquivo = pasta / f"{nome_suite}_{carimbo}.log"
        with open(arquivo, "w", encoding="utf-8") as f:
            f.write(conteudo)
        return arquivo
    except Exception as e:
        print(f"\n[tir_report] Falha ao gravar o log da execucao: {e}", file=sys.stderr)
        return None


def run(suite, name=None, out_dir=None, verbosity=2):
    """Executa a suite, imprime no console como sempre e grava o .log da execução.

    :param suite: unittest.TestSuite já montada.
    :param name: nome da suite. Padrão: nome do arquivo em execução.
    :param out_dir: pasta de destino. Padrão: LogFolder do config.json, senão ./Log.
    :param verbosity: verbosidade do TextTestRunner. Padrão 2, igual ao uso atual.
    :return: o unittest.TestResult, para o chamador decidir código de saída.
    """
    nome_suite = _nome_suite(name)
    inicio = datetime.now().astimezone()

    runner = unittest.TextTestRunner(verbosity=verbosity, resultclass=_ResultadoDetalhado)
    resultado = runner.run(suite)

    fim = datetime.now().astimezone()

    arquivo = gravar(nome_suite, getattr(resultado, "registros", []), inicio, fim, out_dir)
    if arquivo:
        print(f"\n[tir_report] Log da execucao: {arquivo}")

    return resultado
