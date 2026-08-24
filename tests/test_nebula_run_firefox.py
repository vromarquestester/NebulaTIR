"""Prefs de memória no Firefox do TIR (`services/tir/nebula_run.py`).

Por que existe: numa corrida paralela o Firefox é o maior consumidor —
~580 MB por instância, mais que um AppServer inteiro. O TIR monta
`FirefoxOpt()` sem preferência nenhuma, e o `pip install tir_framework
--upgrade` roda antes de cada execução, então editar o pacote seria desfeito.
O patch é em memória, como o do runner e o do Webapp.

O lançador roda no venv do TIR (Python 3.12), não no do NebulaTIR: aqui ele é
carregado por caminho, com um `tir` de mentira no `sys.modules`.
"""

import sys
import types
from pathlib import Path

import pytest

CAMINHO = (Path(__file__).resolve().parent.parent
           / "src" / "services" / "tir" / "nebula_run.py")


class OpcoesFalsas:
    """Imita `selenium...firefox.options.Options` no que interessa."""

    def __init__(self):
        self.prefs = {}

    def set_preference(self, chave, valor):
        self.prefs[chave] = valor


@pytest.fixture
def run(monkeypatch):
    """Carrega o lançador com um TIR de mentira montado no `sys.modules`."""
    tir = types.ModuleType("tir")
    tecnologias = types.ModuleType("tir.technologies")
    core = types.ModuleType("tir.technologies.core")
    base = types.ModuleType("tir.technologies.core.base")
    base.FirefoxOpt = OpcoesFalsas
    tir.Webapp = type("Webapp", (), {"__init__": lambda self, *a, **k: None})
    for nome, modulo in [("tir", tir), ("tir.technologies", tecnologias),
                         ("tir.technologies.core", core),
                         ("tir.technologies.core.base", base)]:
        monkeypatch.setitem(sys.modules, nome, modulo)
    # `tir_report` é irmão do lançador e não interessa a este teste.
    monkeypatch.setitem(sys.modules, "tir_report", types.ModuleType("tir_report"))
    sys.modules["tir_report"]._ResultadoDetalhado = object

    codigo = CAMINHO.read_text(encoding="utf-8")
    modulo = types.ModuleType("nebula_run_teste")
    modulo.__file__ = str(CAMINHO)
    exec(compile(codigo, str(CAMINHO), "exec"), modulo.__dict__)
    return modulo, base


def test_prefs_chegam_no_options_do_firefox(run):
    modulo, base = run
    assert modulo._instala_prefs_firefox() is True

    opcoes = base.FirefoxOpt()
    assert opcoes.prefs["dom.ipc.processCount"] == 1
    # Sozinha, a pref acima perde efeito com Fission ligado.
    assert opcoes.prefs["fission.autostart"] is False
    assert opcoes.prefs["browser.cache.memory.capacity"] == 32768


def test_a_subclasse_preserva_o_options_original(run):
    """O TIR passa esse objeto para o `webdriver.Firefox`: ele tem que
    continuar sendo um Options de verdade, não um substituto."""
    modulo, base = run
    original = base.FirefoxOpt
    modulo._instala_prefs_firefox()
    assert issubclass(base.FirefoxOpt, original)


def test_desligado_nao_toca_no_tir(run):
    modulo, base = run
    antes = base.FirefoxOpt
    assert modulo._instala_prefs_firefox(ativo=False) is False
    assert base.FirefoxOpt is antes


def test_tir_sem_firefoxopt_nao_derruba_a_corrida(run, monkeypatch):
    """Perder memória é melhor que não rodar: se o TIR mudar de estrutura, a
    execução segue com o Firefox padrão."""
    modulo, base = run
    monkeypatch.delattr(base, "FirefoxOpt")
    assert modulo._instala_prefs_firefox() is False


def test_pref_recusada_nao_interrompe_as_outras(run):
    """Versão de Firefox que não conhece uma pref não pode custar as demais."""
    modulo, base = run

    class OpcoesChatas(OpcoesFalsas):
        def set_preference(self, chave, valor):
            if chave == "fission.autostart":
                raise ValueError("pref desconhecida")
            super().set_preference(chave, valor)

    base.FirefoxOpt = OpcoesChatas
    modulo._instala_prefs_firefox()

    opcoes = base.FirefoxOpt()
    assert "fission.autostart" not in opcoes.prefs
    assert opcoes.prefs["dom.ipc.processCount"] == 1


def test_o_padrao_e_enxuto_e_a_flag_desliga(run):
    """Quem quiser o Firefox do TIR pede; o normal é economizar."""
    modulo, _ = run
    parser = modulo._parser()
    assert parser.parse_args(["X.py"]).firefox_enxuto is True
    assert parser.parse_args(["X.py", "--firefox-padrao"]).firefox_enxuto is False
