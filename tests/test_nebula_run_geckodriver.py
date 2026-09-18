"""geckodriver resolvido como o TIR resolve o ChromeDriver, e navegador
encerrado quando `Start()` falha (`services/tir/nebula_run.py`).

Caso real de 2026-09-18 (DESKTOP-QA): o Firefox abria, ficava aberto, e o
TIR parava em "Browsing context has been discarded" ao maximizar. O TIR usa
um `geckodriver.exe` fixo de dentro do pacote (0.30.0, de 2021) e ignora o
PATH — a pasta `drivers/` nunca valeu para o Firefox. E ao falhar só chama
`driver.close()`, nunca `quit()`: driver e navegador ficam vivos.
"""

import sys
import types
from pathlib import Path

import pytest

CAMINHO = (Path(__file__).resolve().parent.parent
           / "src" / "services" / "tir" / "nebula_run.py")


class ServicoFalso:
    """Imita `selenium...firefox.service.Service` no que interessa."""
    instancias = []

    def __init__(self, executable_path=None, port=0, service_args=None,
                 log_output=None, env=None, **kwargs):
        self.executable_path = executable_path
        self.log_output = log_output
        self.kwargs = kwargs
        ServicoFalso.instancias.append(self)


@pytest.fixture
def run(monkeypatch):
    tir = types.ModuleType("tir")
    base = types.ModuleType("tir.technologies.core.base")
    base.FirefoxService = ServicoFalso
    base.FirefoxOpt = type("Opt", (), {"set_preference": lambda *a: None})
    for nome, modulo in [("tir", tir), ("tir.technologies", types.ModuleType("tir.technologies")),
                         ("tir.technologies.core", types.ModuleType("tir.technologies.core")),
                         ("tir.technologies.core.base", base)]:
        monkeypatch.setitem(sys.modules, nome, modulo)
    monkeypatch.setitem(sys.modules, "tir_report", types.ModuleType("tir_report"))
    sys.modules["tir_report"]._ResultadoDetalhado = object
    ServicoFalso.instancias.clear()

    modulo = types.ModuleType("nebula_run_teste")
    modulo.__file__ = str(CAMINHO)
    exec(compile(CAMINHO.read_text(encoding="utf-8"), str(CAMINHO), "exec"),
         modulo.__dict__)
    return modulo, base, tir


# ── Resolução do driver ────────────────────────────────────

def test_ordem_webdriver_manager_primeiro(run, tmp_path, monkeypatch):
    modulo, base, _ = run
    gerenciado = tmp_path / "wdm" / "geckodriver.exe"
    gerenciado.parent.mkdir()
    gerenciado.write_bytes(b"x")
    monkeypatch.setattr(modulo, "_geckodriver_gerenciado", lambda: str(gerenciado))
    monkeypatch.setattr(modulo, "_geckodriver_da_pasta", lambda: "")

    assert modulo._instala_driver_firefox(tmp_path / "log") is True
    base.FirefoxService(executable_path="C:/pacote/geckodriver.exe", log_path="nul")

    s = ServicoFalso.instancias[-1]
    assert s.executable_path == str(gerenciado)
    assert s.log_output == str(tmp_path / "log" / "geckodriver.log")
    assert "log_path" not in s.kwargs


def test_reserva_e_a_pasta_drivers_depois_o_pacote(run, tmp_path, monkeypatch):
    modulo, base, _ = run
    monkeypatch.setattr(modulo, "_geckodriver_gerenciado", lambda: "")
    pasta = tmp_path / "drivers"
    pasta.mkdir()
    (pasta / "geckodriver.exe").write_bytes(b"x")
    monkeypatch.setenv("PATH", str(pasta) + ";C:/Windows")
    pacote = tmp_path / "pacote" / "geckodriver.exe"
    pacote.parent.mkdir()
    pacote.write_bytes(b"x")

    modulo._instala_driver_firefox(None)
    base.FirefoxService(executable_path=str(pacote))
    assert ServicoFalso.instancias[-1].executable_path == str(pasta / "geckodriver.exe")

    (pasta / "geckodriver.exe").unlink()
    base.FirefoxService(executable_path=str(pacote))
    assert ServicoFalso.instancias[-1].executable_path == str(pacote)


def test_webdriver_manager_com_prazo_nao_pendura(run, monkeypatch):
    """Sem rede o `install()` pode travar; passado o prazo, segue com a reserva."""
    import time
    modulo, _, _ = run
    wdm = types.ModuleType("webdriver_manager")
    firefox = types.ModuleType("webdriver_manager.firefox")

    class Lento:
        def install(self):
            time.sleep(5)
            return "nunca"
    firefox.GeckoDriverManager = Lento
    monkeypatch.setitem(sys.modules, "webdriver_manager", wdm)
    monkeypatch.setitem(sys.modules, "webdriver_manager.firefox", firefox)
    monkeypatch.setattr(modulo, "TEMPO_LIMITE_DRIVER_SEG", 0.2)
    assert modulo._geckodriver_gerenciado() == ""


def test_webdriver_manager_que_falha_devolve_vazio(run, monkeypatch):
    modulo, _, _ = run
    firefox = types.ModuleType("webdriver_manager.firefox")

    class Quebrado:
        def install(self):
            raise ConnectionError("sem GitHub")
    firefox.GeckoDriverManager = Quebrado
    monkeypatch.setitem(sys.modules, "webdriver_manager", types.ModuleType("webdriver_manager"))
    monkeypatch.setitem(sys.modules, "webdriver_manager.firefox", firefox)
    assert modulo._geckodriver_gerenciado() == ""


def test_sem_firefox_service_no_tir_nao_quebra(run):
    modulo, base, _ = run
    del base.FirefoxService
    assert modulo._instala_driver_firefox(None) is False


# ── Navegador encerrado quando Start() falha ───────────────

class DriverFalso:
    def __init__(self):
        self.quit_chamado = False

    def quit(self):
        self.quit_chamado = True


def test_start_que_falha_encerra_o_navegador(run):
    modulo, _, tir = run
    driver = DriverFalso()

    class WebappOriginal:
        def __init__(self, config_path="", autostart=True):
            self._Webapp__webapp = types.SimpleNamespace(driver=driver)

        def Start(self):
            raise AssertionError("Wasn't possible execute Start() method")

    tir.Webapp = WebappOriginal
    assert modulo._instala_webapp("C:/x/config.json") is True
    w = tir.Webapp()
    with pytest.raises(AssertionError):
        w.Start()
    assert driver.quit_chamado is True


def test_autostart_que_falha_no_construtor_encerra(run):
    modulo, _, tir = run
    driver = DriverFalso()

    class WebappOriginal:
        def __init__(self, config_path="", autostart=True):
            self._Webapp__webapp = types.SimpleNamespace(driver=driver)
            if autostart:
                raise AssertionError("falhou no Start")

    tir.Webapp = WebappOriginal
    modulo._instala_webapp("C:/x/config.json")
    with pytest.raises(AssertionError):
        tir.Webapp()
    assert driver.quit_chamado is True


def test_start_que_funciona_nao_mexe_no_driver(run):
    modulo, _, tir = run
    driver = DriverFalso()

    class WebappOriginal:
        def __init__(self, config_path="", autostart=True):
            self._Webapp__webapp = types.SimpleNamespace(driver=driver)

        def Start(self):
            return "ok"

    tir.Webapp = WebappOriginal
    modulo._instala_webapp("C:/x/config.json")
    assert tir.Webapp().Start() == "ok"
    assert driver.quit_chamado is False
