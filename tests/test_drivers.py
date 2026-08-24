"""Drivers de navegador (`services.drivers`).

O TIR distribui `chromedriver.exe` e `geckodriver.exe`, mas eles envelhecem:
na primeira corrida real vieram ChromeDriver 126 com Chrome 148 instalado, e
geckodriver 0.30.0, de 2021. O sintoma é `session not created` no `setUpClass`
e nenhum caso roda.

A pasta `drivers/` ao lado do executável vem na frente do PATH, então trocar o
arquivo ali atualiza o driver sem mexer no venv — que é recriado a cada
atualização do framework.
"""

import os
from pathlib import Path

import pytest

from services import drivers, navegadores


@pytest.fixture
def cenario(tmp_path, monkeypatch):
    prog = tmp_path / "programa"
    prog.mkdir()
    monkeypatch.setattr(drivers, "pasta_do_programa", lambda: prog)

    venv = tmp_path / "venv" / "Scripts" / "python.exe"
    origem = (tmp_path / "venv" / "Lib" / "site-packages" / "tir"
              / "technologies" / "core" / "drivers" / "windows")
    origem.mkdir(parents=True)
    venv.parent.mkdir(parents=True, exist_ok=True)
    venv.write_bytes(b"")
    for nome in drivers.EXECUTAVEIS:
        (origem / nome).write_bytes(b"driver-do-tir")
    return prog, venv


def test_semeia_com_os_drivers_do_tir(cenario):
    prog, venv = cenario
    r = drivers.semear(venv)
    assert sorted(r["copiados"]) == sorted(drivers.EXECUTAVEIS)
    for nome in drivers.EXECUTAVEIS:
        assert (prog / "drivers" / nome).is_file()


def test_nao_sobrescreve_driver_que_o_usuario_trocou(cenario):
    """Se alguém largou um driver novo ali, ele manda."""
    prog, venv = cenario
    alvo = prog / "drivers" / "geckodriver.exe"
    alvo.parent.mkdir(parents=True, exist_ok=True)
    alvo.write_bytes(b"driver-novo-do-usuario")

    r = drivers.semear(venv)
    assert "geckodriver.exe" not in r["copiados"]
    assert alvo.read_bytes() == b"driver-novo-do-usuario"


def test_venv_sem_pasta_de_drivers_nao_quebra(tmp_path, monkeypatch):
    monkeypatch.setattr(drivers, "pasta_do_programa", lambda: tmp_path)
    r = drivers.semear(tmp_path / "sem" / "Scripts" / "python.exe")
    assert r["ok"] is True
    assert r["copiados"] == []


# ── PATH ────────────────────────────────────────────────────

def test_drivers_entram_na_frente_do_path(cenario):
    prog, _ = cenario
    env = drivers.ambiente_com_drivers({"PATH": r"C:\Windows"})
    assert env["PATH"].startswith(str(prog / "drivers"))
    assert r"C:\Windows" in env["PATH"]


def test_nao_duplica_no_path(cenario):
    prog, _ = cenario
    primeiro = drivers.ambiente_com_drivers({"PATH": r"C:\Windows"})
    segundo = drivers.ambiente_com_drivers(primeiro)
    assert segundo["PATH"].count(str(prog / "drivers")) == 1


def test_ambiente_sem_path_nao_quebra(cenario):
    env = drivers.ambiente_com_drivers({})
    assert "drivers" in env["PATH"]


# ── Limpeza dos processos deixados para trás ────────────────
# Quando o teste morre antes do fim (setUpClass estourando, por exemplo), o
# TIR não chega no TearDown e o driver com o navegador headless ficam vivos.
# Numa noite de tentativas isso acumulou 158 Firefox e 68 geckodriver.

def test_mata_so_os_drivers_do_proprio_lancador(monkeypatch):
    """O corte é por parentesco: navegador aberto pelo usuário não é filho de
    driver nenhum, e driver de outra corrida tem outro pai."""
    monkeypatch.setattr(drivers, "_consultar",
                        lambda filtro: [(100, 999), (200, 999), (300, 555)])
    mortos = []
    monkeypatch.setattr(drivers, "_matar_arvore",
                        lambda pid: mortos.append(pid) or True)

    assert drivers.encerrar_do_lancador(999) == 2
    assert sorted(mortos) == [100, 200]


def test_sem_lancador_nao_mata_nada(monkeypatch):
    monkeypatch.setattr(drivers, "_matar_arvore",
                        lambda pid: pytest.fail("matou sem lançador"))
    assert drivers.encerrar_do_lancador(0) == 0


def test_orfaos_sao_varridos(monkeypatch):
    """Driver de navegador só existe por automação: órfão é resto de corrida."""
    monkeypatch.setattr(drivers, "_consultar",
                        lambda filtro: [(100, 4242), (200, 7777)])
    monkeypatch.setattr(drivers, "_pai_existe", lambda pid: pid == 4242)
    monkeypatch.setattr(drivers, "_matar_arvore", lambda pid: True)
    monkeypatch.setattr(drivers.os, "kill",
                        lambda pid, sig: (_ for _ in ()).throw(OSError()))

    assert drivers.encerrar_orfaos() == 1


def test_taskkill_usa_arvore(monkeypatch):
    """`/T` leva o navegador junto; sem isso o Firefox headless sobrevive ao
    driver e continua ocupando memória."""
    comandos = []
    monkeypatch.setattr(drivers.subprocess, "run",
                        lambda cmd, **k: comandos.append(cmd) or
                        type("R", (), {"returncode": 0, "stdout": ""})())
    drivers._matar_arvore(4242)
    assert comandos == [["taskkill", "/F", "/T", "/PID", "4242"]]


# ── Navegador padrão ────────────────────────────────────────

def test_firefox_e_o_padrao():
    """É com ele que o TIR roda melhor hoje; o Chrome se atualiza sozinho e
    deixa o ChromeDriver para trás."""
    assert navegadores.preferido(["Chrome", "Firefox", "Edge"]) == "Firefox"
    assert navegadores.preferido(["Chrome", "Edge"]) == "Chrome"
    assert navegadores.preferido([]) == ""
