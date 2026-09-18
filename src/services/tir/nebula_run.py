"""Executa um testsuite TIR capturando o log, sem alterar uma linha do arquivo de teste.

Os testsuites e testcases vão para code review e rodam em esteira, então não podem
conter nada de instrumentação. Este lançador resolve isso por fora: ele carrega o
testsuite original e, antes disso, substitui em memória duas coisas do processo:

- `unittest.TextTestRunner`, por uma subclasse que registra nome, situação, tempo e
  mensagem de cada caso — o `runner = unittest.TextTestRunner(verbosity=2)` do próprio
  testsuite passa a usá-la sem saber;
- `tir.Webapp`, por uma subclasse que injeta `config_path` quando o teste chama
  `Webapp()` sem argumento, o que permite manter um único config.json.

Nada disso toca em disco: os arquivos .py continuam byte a byte iguais ao original.

Uso:

    python nebula_run.py CAMINHO\\DO\\TESTSUITE.py
    python nebula_run.py MATA143TESTSUITE.py --config ...\\config.json
    python nebula_run.py MATA143TESTSUITE.py --sem-png

INCORPORADO AO NEBULATIR em 2026-08-13, a partir de
`C:\\Dev\\Dmicas_Issue\\Ferramentas\\nebula_run.py`. Duas mudanças em relação
ao original, ambas para o programa não depender de caminho fixo de máquina:

- o `LogNebula.exe` é procurado ao lado deste arquivo e na pasta do programa,
  porque agora vai empacotado junto;
- **o PNG passou a ser padrão** (`--sem-png` desliga). No NebulaTIR toda
  execução gera log e imagem, então exigir a flag só criaria chance de
  esquecer.

Roda no `.venv` do TIR (Python 3.12), como subprocesso — não no interpretador
do NebulaTIR, que pode ser mais novo.
"""
import argparse
import json
import os
import runpy
import sys
import unittest
from datetime import datetime
from pathlib import Path

import tir_report



class _RunnerCaptura(unittest.TextTestRunner):
    """TextTestRunner que guarda o resultado detalhado da última execução."""

    resultclass = tir_report._ResultadoDetalhado
    ultimo = None

    def run(self, test):
        resultado = super().run(test)
        _RunnerCaptura.ultimo = resultado
        return resultado


def _instala_runner():
    """Faz o `unittest.TextTestRunner` do testsuite ser o nosso, no processo atual."""
    unittest.TextTestRunner = _RunnerCaptura
    unittest.runner.TextTestRunner = _RunnerCaptura


def _instala_filtro(somente: set):
    """Faz o suite aceitar só os casos pedidos, sem tocar no arquivo.

    Mesma ideia do runner: o `suite.addTest(CLASSE("test_x"))` do testsuite
    passa a ser filtrado sem saber. É o que permite distribuir os casos de uma
    rotina entre instâncias — os arquivos vão para code review e não podem
    receber instrumentação.
    """
    if not somente:
        return

    original = unittest.TestSuite.addTest

    def addTest(self, teste):          # noqa: N802 — assinatura da stdlib
        nome = getattr(teste, "_testMethodName", None)
        if nome is not None and nome not in somente:
            return
        return original(self, teste)

    unittest.TestSuite.addTest = addTest


def _instala_webapp(config_path: str):
    """Injeta config_path nas instâncias de Webapp criadas sem argumento.

    Precisa rodar antes do testcase ser importado: o `from tir import Webapp` dele
    fixa a referência no momento do import.
    """
    if not config_path:
        return False
    try:
        import tir
    except ImportError:
        return False

    original = tir.Webapp
    padrao = config_path

    class WebappConfigurado(original):
        # A assinatura precisa ser idêntica à do TIR: os testes podem chamar
        # Webapp(config_path=...) por palavra-chave.
        def __init__(self, config_path="", autostart=True):
            try:
                super().__init__(config_path or padrao, autostart)
            except BaseException:
                # `autostart=True` chama Start() aqui dentro; falhou, o
                # navegador ficaria aberto para sempre (ver `_encerra_navegador`).
                _encerra_navegador(self)
                raise

        def Start(self):          # noqa: N802 — nome do TIR
            try:
                return super().Start()
            except BaseException:
                _encerra_navegador(self)
                raise

    tir.Webapp = WebappConfigurado
    return True


def _encerra_navegador(webapp) -> bool:
    """Mata driver e navegador de um `Webapp` cujo Start() falhou.

    Quando o Start() falha (2026-09-18 na DESKTOP-QA: "Browsing context has
    been discarded" ao maximizar), o `log_error` do TIR só chama
    `driver.close()` — que falha pelo mesmo motivo — e nunca `quit()`. O
    geckodriver e o Firefox ficam vivos, a janela fica aberta esperando um
    teste que não vem, e o lançador não termina enquanto eles seguram o
    pipe. `quit()` derruba os dois pelo próprio driver; se nem isso der,
    quem chamou o lançador varre os órfãos.
    """
    interno = getattr(webapp, "_Webapp__webapp", None)
    driver = getattr(interno, "driver", None) or getattr(webapp, "driver", None)
    if driver is None:
        return False
    try:
        driver.quit()
        print("[nebula_run] Start() falhou: navegador e driver encerrados.",
              file=sys.stderr)
        return True
    except Exception as e:
        print(f"[nebula_run] Start() falhou e o navegador nao fechou: {e}",
              file=sys.stderr)
        return False


# Prefs aplicadas ao Firefox de cada instância. O Firefox padrão abre vários
# processos de conteúdo e um cache de memória proporcional à RAM da máquina —
# desenho de navegador de gente, não de robô que abre uma tela por vez. Numa
# corrida paralela isso é o maior consumidor: ~580 MB por instância, contra
# ~550 MB de um AppServer inteiro.
#
# Nada aqui muda o que o teste enxerga: continua um Firefox real, com a mesma
# renderização. O que sai é multiprocesso, cache e histórico.
PREFS_FIREFOX = {
    # Um processo de conteúdo em vez de oito. Sozinha, esta pref perde efeito
    # com Fission ligado (que isola por site), daí a de baixo.
    "dom.ipc.processCount": 1,
    "fission.autostart": False,
    # Em KB. O padrão é automático e cresce com a RAM da máquina.
    "browser.cache.memory.capacity": 32768,
    "browser.cache.disk.enable": False,
    # Histórico de navegação por aba: o teste anda para frente, não volta.
    "browser.sessionhistory.max_entries": 5,
    "browser.sessionstore.max_tabs_undo": 0,
    "browser.sessionstore.max_windows_undo": 0,
    # Nada disso existe em headless, mas continua sendo carregado.
    "extensions.pocket.enabled": False,
    "browser.newtabpage.enabled": False,
    "toolkit.telemetry.enabled": False,
    "datareporting.healthreport.uploadEnabled": False,
}


def _instala_prefs_firefox(ativo: bool = True):
    """Injeta as prefs no `Options` do Firefox que o TIR monta.

    O TIR cria `FirefoxOpt()` sem perfil e sem preferência nenhuma
    (`technologies/core/base.py`), e o `pip install tir_framework --upgrade`
    roda antes de cada execução — editar o pacote seria desfeito no dia
    seguinte. Por isso o mesmo caminho do runner e do Webapp: troca em
    memória, no processo, sem tocar em disco.

    Devolve False quando o TIR muda de estrutura, e a execução segue com o
    Firefox padrão — perder memória é bem melhor que não rodar.
    """
    if not ativo:
        return False
    try:
        import tir.technologies.core.base as tir_base
    except ImportError:
        return False
    original = getattr(tir_base, "FirefoxOpt", None)
    if original is None:
        print("[nebula_run] AVISO: nao achei FirefoxOpt no TIR; "
              "o Firefox sobe com as prefs padrao.", file=sys.stderr)
        return False

    class OpcoesEnxutas(original):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            for chave, valor in PREFS_FIREFOX.items():
                try:
                    self.set_preference(chave, valor)
                except Exception:
                    # Pref recusada pela versão do Firefox não pode derrubar a
                    # corrida: cada uma é independente da outra.
                    pass

    tir_base.FirefoxOpt = OpcoesEnxutas
    return True


# ── geckodriver ─────────────────────────────────────────────
#
# Para o Chrome o TIR resolve o driver na hora (`ChromeDriverAutoInstall` →
# `webdriver_manager.ChromeDriverManager().install()`), e por isso o Chrome
# raramente quebra por driver. Para o Firefox não: `Start()` usa um
# `executable_path` fixo, o `geckodriver.exe` de dentro do pacote — 0.30.0,
# de 2021 — e ignora o PATH (a pasta `drivers/` do NebulaTIR nunca valeu
# para o Firefox, ao contrário do que se supôs em 2026-08-14). Aqui o
# Firefox passa a ter o mesmo tratamento do Chrome: o driver vem do
# `webdriver_manager` (cache em `~/.wdm`, baixa uma vez), com a pasta
# `drivers/` e o do pacote como reservas. E o log do geckodriver, que o TIR
# manda para o `os.devnull`, vai para a pasta de log da corrida.

TEMPO_LIMITE_DRIVER_SEG = 90


def _geckodriver_gerenciado() -> str:
    """Caminho do geckodriver do `webdriver_manager`, ou vazio.

    Roda numa thread com prazo: sem rede (VPN, proxy) o download pode
    pendurar, e um teste que não começa por causa do driver é pior que um
    teste com driver velho.
    """
    import threading
    resultado = {}

    def _instala():
        try:
            from webdriver_manager.firefox import GeckoDriverManager
            resultado["caminho"] = GeckoDriverManager().install()
        except Exception as e:      # rede, proxy, GitHub, disco
            resultado["erro"] = e

    t = threading.Thread(target=_instala, name="geckodriver", daemon=True)
    t.start()
    t.join(TEMPO_LIMITE_DRIVER_SEG)
    if t.is_alive():
        print(f"[nebula_run] geckodriver: webdriver_manager passou de "
              f"{TEMPO_LIMITE_DRIVER_SEG}s; usando a reserva.", file=sys.stderr)
        return ""
    if resultado.get("erro"):
        print(f"[nebula_run] geckodriver: webdriver_manager falhou "
              f"({resultado['erro']}); usando a reserva.", file=sys.stderr)
        return ""
    return resultado.get("caminho", "") or ""


def _geckodriver_da_pasta() -> str:
    """`drivers/geckodriver.exe` ao lado do NebulaTIR, se existir."""
    for pasta in os.environ.get("PATH", "").split(os.pathsep):
        candidato = Path(pasta) / "geckodriver.exe"
        if pasta.lower().endswith("drivers") and candidato.is_file():
            return str(candidato)
    return ""


def _resolve_geckodriver(padrao: str) -> str:
    """Ordem: webdriver_manager → `drivers/` do NebulaTIR → o do pacote do TIR."""
    for origem, caminho in (("webdriver_manager", _geckodriver_gerenciado()),
                            ("pasta drivers/", _geckodriver_da_pasta()),
                            ("pacote do TIR", padrao)):
        if caminho and Path(caminho).is_file():
            print(f"[nebula_run] geckodriver: {caminho} ({origem})")
            return caminho
    return padrao


def _instala_driver_firefox(pasta_log: Path | None = None) -> bool:
    """Troca o `FirefoxService` do TIR por um que resolve o driver e loga."""
    try:
        import tir.technologies.core.base as tir_base
    except ImportError:
        return False
    original = getattr(tir_base, "FirefoxService", None)
    if original is None:
        print("[nebula_run] AVISO: nao achei FirefoxService no TIR; "
              "o geckodriver fica o do pacote.", file=sys.stderr)
        return False

    class ServicoResolvido(original):
        def __init__(self, executable_path=None, *args, **kwargs):
            executable_path = _resolve_geckodriver(executable_path or "")
            # O TIR manda o log para o os.devnull; é justamente o que falta
            # quando o navegador abre e o teste não começa.
            if pasta_log is not None:
                kwargs.pop("log_path", None)
                kwargs.pop("log_output", None)
                try:
                    pasta_log.mkdir(parents=True, exist_ok=True)
                    kwargs["log_output"] = str(pasta_log / "geckodriver.log")
                except OSError:
                    pass
            super().__init__(executable_path, *args, **kwargs)

    tir_base.FirefoxService = ServicoResolvido
    return True


def _executa(testsuite: Path):
    """Roda o testsuite original como se tivesse sido chamado direto pelo Python."""
    pasta = str(testsuite.parent)

    # sys.path[0] e o diretório atual precisam ser a pasta do testsuite: é assim que
    # o "from MATA143TESTCASE import ..." resolve, e é onde o TIR procura o config.
    sys.path.insert(0, pasta)
    anterior = os.getcwd()
    os.chdir(pasta)

    argv_anterior = sys.argv[:]
    sys.argv = [str(testsuite)]

    try:
        runpy.run_path(str(testsuite), run_name="__main__")
    finally:
        os.chdir(anterior)
        sys.argv = argv_anterior


def _motivo_do_tir(pasta: Path, desde) -> str:
    """Erro que o TIR gravou no próprio JSON, quando nem o primeiro caso rodou.

    O TIR deixa um `<carimbo>.json` por execução na pasta do teste, com o erro
    em `nLogCtsErr`. É lá que mora a causa de falhas de `setUpClass` — driver
    incompatível, navegador ausente, ambiente fora do ar.
    """
    try:
        candidatos = [p for p in pasta.glob("*.json") if p.name != "config.json"]
        recentes = [p for p in candidatos
                    if p.stat().st_mtime >= desde.timestamp() - 5]
        if not recentes:
            return ""
        alvo = max(recentes, key=lambda p: p.stat().st_mtime)
        dado = json.loads(alvo.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return ""

    bruto = str(dado.get("nLogCtsErr") or "").strip()
    if not bruto:
        return ""
    # A mensagem traz um stacktrace enorme do driver; a primeira linha basta.
    return bruto.split("Stacktrace")[0].strip()[:300]


def nome_do_caso_que_falhou(somente) -> str:
    """Nome a usar na linha de falha quando nenhum caso chegou a registrar.

    Numa fatia de rotina dividida sabemos exatamente quais casos aquela
    instância deveria rodar; o relatório fica mais útil nomeando o primeiro
    deles do que dizendo só "setUpClass".
    """
    casos = list(somente or [])
    return casos[0] if len(casos) == 1 else ""


def _grava_parcial(destino: Path, suite: str, registros: list,
                   inicio, fim) -> None:
    """Guarda o resultado desta fatia para o relatório final juntar.

    Só os registros: o log e o PNG saem uma vez por rotina, depois que todas
    as instâncias terminarem a parte delas.
    """
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(json.dumps({
        "suite": suite,
        "inicio": inicio.isoformat(),
        "fim": fim.isoformat(),
        "registros": registros,
    }, ensure_ascii=False, default=str), encoding="utf-8")
    print(f"[nebula_run] Parcial: {destino}")


def _gera_png(log: Path) -> bool:
    """Monta o PNG do relatório no próprio processo.

    Antes isto chamava o `LogNebula.exe`, o que obrigava a copiar 20 MB de
    executável para dentro de cada pasta de teste. O código do LogNebula foi
    incorporado (`nebula_parser*`, `nebula_exporter`), então basta importar.
    """
    try:
        from nebula_exporter import export_png
        from nebula_parser import parse_log
    except ImportError as e:
        print(f"[nebula_run] Relatorio indisponivel: {e}", file=sys.stderr)
        return False

    try:
        relatorio = parse_log(str(log))
        if relatorio is None:
            print("[nebula_run] Log sem suite reconhecida; PNG nao gerado.",
                  file=sys.stderr)
            return False
        destino = export_png(relatorio, str(log.parent))
        print(f"[nebula_run] Relatorio: {destino}")
        return True
    except Exception as e:
        print(f"[nebula_run] Falha ao gerar o PNG: {e}", file=sys.stderr)
        return False


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="nebula_run",
        description="Roda um testsuite TIR e grava o log da execucao, sem alterar o teste.",
    )
    p.add_argument("testsuite", help="Arquivo *TESTSUITE.py a executar.")
    p.add_argument("--config", metavar="PATH", help="config.json a usar. Padrao: busca automatica.")
    p.add_argument("--out", metavar="DIR", help="Pasta do log. Padrao: LogFolder do config.")
    # PNG é padrão no NebulaTIR; a flag existe para desligar.
    p.add_argument("--sem-png", dest="png", action="store_false", default=True,
                   help="Não gerar o PNG do relatório.")
    p.add_argument("--somente", metavar="CASO", action="append",
                   help="Roda só estes casos do suite (pode repetir). Usado "
                        "quando a rotina é dividida entre instâncias.")
    p.add_argument("--parcial", metavar="JSON",
                   help="Grava os registros desta execução em JSON, para "
                        "serem juntados depois num relatório único.")
    p.add_argument("--firefox-padrao", dest="firefox_enxuto",
                   action="store_false", default=True,
                   help="Não aplicar as prefs de memória no Firefox (usa o "
                        "comportamento padrão do TIR).")
    return p


def main() -> int:
    args = _parser().parse_args()

    testsuite = Path(args.testsuite).resolve()
    if not testsuite.is_file():
        print(f"[nebula_run] Testsuite nao encontrado: {testsuite}", file=sys.stderr)
        return 2

    # A busca do config.json parte da pasta do testsuite, não da deste lançador.
    config = tir_report.config(args.config, inicio=testsuite.parent)
    print(f"[nebula_run] Testsuite: {testsuite}")
    print(f"[nebula_run] Config:    {config or '(padrao do TIR: config.json ao lado do teste)'}")

    _instala_runner()
    _instala_filtro(set(args.somente or []))
    _instala_webapp(config)
    # Mesma pasta do log da corrida (LogFolder do config, ou --out).
    try:
        pasta_log = tir_report._pasta_saida(args.out)
    except Exception:
        pasta_log = None
    _instala_driver_firefox(pasta_log)
    if _instala_prefs_firefox(args.firefox_enxuto):
        print(f"[nebula_run] Firefox enxuto: {len(PREFS_FIREFOX)} prefs "
              "(1 processo de conteúdo, cache de 32 MB, sem histórico).")
    if args.somente:
        print(f"[nebula_run] Somente: {', '.join(args.somente)}")

    inicio = datetime.now().astimezone()
    erro = None
    try:
        _executa(testsuite)
    except SystemExit:
        pass
    except Exception as e:
        erro = e
        print(f"[nebula_run] A execucao terminou com erro: {e}", file=sys.stderr)
    fim = datetime.now().astimezone()

    resultado = _RunnerCaptura.ultimo
    registros = getattr(resultado, "registros", []) if resultado else []

    if not registros:
        # Falha antes do primeiro caso (setUpClass) não entra nos registros, e
        # o motivo fica só no JSON que o TIR grava. Sem isto o painel mostra
        # "código 2" e o usuário não tem como saber que foi o ChromeDriver.
        motivo = _motivo_do_tir(testsuite.parent, inicio)
        if motivo:
            print(f"[nebula_run] Motivo: {motivo}", file=sys.stderr)
        # Relatório é para ler quando dá errado — é justamente aí que alguém
        # vai olhar. Sem caso nenhum registrado, o log e o PNG saem assim
        # mesmo, com uma linha que diz o que derrubou a suite.
        registros = [{
            "nome": nome_do_caso_que_falhou(args.somente) or "setUpClass",
            "passou": False,
            "carimbo": inicio,
            "segundos": (fim - inicio).total_seconds(),
            "mensagem": motivo or (str(erro) if erro else
                                   "A suite não chegou a executar nenhum caso."),
        }]
        codigo_sem_casos = 1 if erro else 2
    else:
        codigo_sem_casos = None

    if args.parcial:
        # Execução dividida: cada instância grava o que rodou, e o relatório
        # único é montado depois pelo `nebula_merge`.
        _grava_parcial(Path(args.parcial), testsuite.stem, registros,
                       inicio, fim)
        return codigo_sem_casos or (
            0 if all(r["passou"] for r in registros) else 1)

    log = tir_report.gravar(testsuite.stem, registros, inicio, fim, args.out)
    if not log:
        return 1

    print(f"\n[nebula_run] Log da execucao: {log}")

    if args.png:
        _gera_png(log)

    return codigo_sem_casos or (
        0 if all(r["passou"] for r in registros) else 1)


if __name__ == "__main__":
    sys.exit(main())
