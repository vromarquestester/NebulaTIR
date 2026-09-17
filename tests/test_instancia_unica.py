"""Uma instância por programa (`services.instancia_unica`).

A trava é um mutex nomeado do Windows: solta sozinha quando o processo morre.
O relançamento do atualizador não conta como segunda instância.
"""

import os
import subprocess
import sys
import time

import pytest

from services import instancia_unica

so_windows = pytest.mark.skipif(os.name != "nt", reason="mutex nomeado do Windows")


@pytest.fixture(autouse=True)
def solta_a_trava():
    yield
    instancia_unica.liberar()


@so_windows
def test_primeiro_adquire_e_o_mesmo_processo_nao_conta_duas_vezes():
    nome = f"NebulaTIR-teste-{os.getpid()}"
    assert instancia_unica.adquirir(nome) is True
    # Mesmo nome, mesmo processo: é a mesma trava, não uma segunda instância.
    assert instancia_unica.adquirir(nome) is True


@so_windows
def test_outro_processo_com_a_trava_bloqueia():
    nome = f"NebulaTIR-teste-{os.getpid()}-outro"
    codigo = (
        "import sys, time; sys.path.insert(0, sys.argv[1]);"
        "from services import instancia_unica as u;"
        f"print(u.adquirir({nome!r}), flush=True); time.sleep(3)"
    )
    src = os.path.join(os.path.dirname(os.path.dirname(__file__)), "src")
    outro = subprocess.Popen([sys.executable, "-c", codigo, src],
                             stdout=subprocess.PIPE, text=True)
    try:
        assert outro.stdout.readline().strip() == "True"
        assert instancia_unica.adquirir(nome) is False
    finally:
        outro.kill()
        outro.wait()
    # Com o outro morto o sistema soltou a trava.
    fim = time.monotonic() + 5
    while time.monotonic() < fim and not instancia_unica.adquirir(nome):
        time.sleep(0.1)
    assert instancia_unica.adquirir(nome) is True


@so_windows
def test_relancamento_espera_o_pai_soltar():
    nome = f"NebulaTIR-teste-{os.getpid()}-relanca"
    codigo = (
        "import sys, time; sys.path.insert(0, sys.argv[1]);"
        "from services import instancia_unica as u;"
        f"print(u.adquirir({nome!r}), flush=True); time.sleep(1.0)"
    )
    src = os.path.join(os.path.dirname(os.path.dirname(__file__)), "src")
    pai = subprocess.Popen([sys.executable, "-c", codigo, src],
                           stdout=subprocess.PIPE, text=True)
    try:
        assert pai.stdout.readline().strip() == "True"
        inicio = time.monotonic()
        # Como o relançado: insiste enquanto o pai termina.
        assert instancia_unica.adquirir(nome, espera_seg=5.0) is True
        assert time.monotonic() - inicio < 4.5
    finally:
        pai.kill()
        pai.wait()


def test_marca_de_relancamento_vem_do_ambiente():
    assert instancia_unica.e_relancamento({}) is False
    assert instancia_unica.e_relancamento({"PYINSTALLER_RESET_ENVIRONMENT": "1"}) is True


@so_windows
def test_janela_inexistente_nao_e_trazida():
    assert instancia_unica.trazer_para_frente("Titulo-que-nao-existe-9f8e7d") is False


@so_windows
def test_garantir_libera_quando_ninguem_tem_a_trava(monkeypatch):
    monkeypatch.setattr(instancia_unica, "e_relancamento", lambda ambiente=None: False)
    assert instancia_unica.garantir(f"NebulaTIR-teste-{os.getpid()}-g", "x") is True


@so_windows
def test_garantir_com_outro_dono_traz_a_janela_e_nao_avisa(monkeypatch):
    nome = f"NebulaTIR-teste-{os.getpid()}-g2"
    monkeypatch.setattr(instancia_unica, "adquirir", lambda n, e=0.0: False)
    trazidas, avisos = [], []
    monkeypatch.setattr(instancia_unica, "trazer_para_frente",
                        lambda titulo: trazidas.append(titulo) or True)
    monkeypatch.setattr(instancia_unica, "_avisar", lambda titulo: avisos.append(titulo))
    assert instancia_unica.garantir(nome, "NebulaTIR") is False
    assert trazidas == ["NebulaTIR"]
    assert avisos == []


@so_windows
def test_garantir_sem_janela_achada_avisa(monkeypatch):
    monkeypatch.setattr(instancia_unica, "adquirir", lambda n, e=0.0: False)
    monkeypatch.setattr(instancia_unica, "trazer_para_frente", lambda titulo: False)
    avisos = []
    monkeypatch.setattr(instancia_unica, "_avisar", lambda titulo: avisos.append(titulo))
    assert instancia_unica.garantir("x", "NebulaTIR") is False
    assert avisos == ["NebulaTIR"]


@so_windows
def test_relancado_espera_e_nao_relancado_nao(monkeypatch):
    esperas = []
    monkeypatch.setattr(instancia_unica, "adquirir",
                        lambda n, e=0.0: esperas.append(e) or True)
    monkeypatch.setattr(instancia_unica, "e_relancamento", lambda ambiente=None: True)
    instancia_unica.garantir("x", "y")
    monkeypatch.setattr(instancia_unica, "e_relancamento", lambda ambiente=None: False)
    instancia_unica.garantir("x", "y")
    assert esperas == [instancia_unica.ESPERA_RELANCAMENTO_SEG, 0.0]
