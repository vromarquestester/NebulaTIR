"""Quem provisiona o venv do TIR (`services.venv_tir`).

Na máquina de desenvolvimento há `uv`; na do usuário há só o Python instalado.
O primeiro usuário real viu "O `uv` não foi encontrado no PATH" (2026-09-18) —
o venv tem que nascer com `python -m venv` quando o `uv` não existe, e o Python
usado tem que ser 3.12, porque o TIR exige `==3.12.*`.
"""

from pathlib import Path

import pytest

from services import venv_tir


@pytest.fixture
def programa(tmp_path, monkeypatch):
    prog = tmp_path / "programa"
    prog.mkdir()
    monkeypatch.setattr(venv_tir, "pasta_do_programa", lambda: prog)
    return prog


@pytest.fixture
def comandos(monkeypatch):
    """Grava cada comando disparado; quem cria o venv em disco é o teste."""
    rodados: list[list[str]] = []

    def _rodar(cmd, tempo):
        rodados.append([str(c) for c in cmd])
        return True, ""

    monkeypatch.setattr(venv_tir, "_rodar", _rodar)
    return rodados


def _which(mapa: dict[str, str]):
    return lambda nome: mapa.get(nome)


# ── com uv ─────────────────────────────────────────────────────────────────

def test_com_uv_cria_pelo_uv(programa, comandos, monkeypatch):
    monkeypatch.setattr(venv_tir.shutil, "which", _which({"uv": "C:/uv.exe"}))

    r = venv_tir.criar()

    assert r["ok"] and r["via"] == "uv"
    assert comandos == [["C:/uv.exe", "venv", "--python", "3.12",
                         str(programa / ".venv")]]


def test_com_uv_atualiza_pelo_uv_pip(programa, comandos, monkeypatch):
    monkeypatch.setattr(venv_tir.shutil, "which", _which({"uv": "C:/uv.exe"}))
    monkeypatch.setattr(venv_tir, "existe", lambda: True)

    r = venv_tir.atualizar_tir()

    assert r["ok"]
    assert comandos[0][:3] == ["C:/uv.exe", "pip", "install"]
    assert "ensurepip" not in " ".join(comandos[0])


# ── sem uv, com Python 3.12 global ────────────────────────────────────────

def _python_falso(monkeypatch, comandos, respostas: dict[str, str]):
    """`_rodar` que responde à sondagem de versão conforme o executável."""
    def _rodar(cmd, tempo):
        comandos.append([str(c) for c in cmd])
        if "-c" in cmd:
            chave = cmd[0] if len(cmd) == 3 else f"{cmd[0]} {cmd[1]}"
            resposta = respostas.get(chave)
            if resposta is None:
                return False, "não encontrado"
            return True, resposta
        return True, ""
    monkeypatch.setattr(venv_tir, "_rodar", _rodar)


def test_sem_uv_usa_py_launcher_312(programa, comandos, monkeypatch):
    monkeypatch.setattr(venv_tir.os, "name", "nt")
    monkeypatch.setattr(venv_tir.shutil, "which",
                        _which({"py": "C:/py.exe", "python": "C:/p313/python.exe"}))
    _python_falso(monkeypatch, comandos, {
        "C:/py.exe -3.12": "C:/p312/python.exe\n3.12",
        "C:/p313/python.exe": "C:/p313/python.exe\n3.13",
    })

    r = venv_tir.criar()

    assert r["ok"] and r["via"] == "python"
    assert comandos[-1] == ["C:/p312/python.exe", "-m", "venv",
                            str(programa / ".venv")]


def test_sem_uv_python_do_path_se_for_312(programa, comandos, monkeypatch):
    monkeypatch.setattr(venv_tir.os, "name", "nt")
    monkeypatch.setattr(venv_tir.shutil, "which",
                        _which({"python": "C:/p312/python.exe"}))
    _python_falso(monkeypatch, comandos,
                  {"C:/p312/python.exe": "C:/p312/python.exe\n3.12"})

    r = venv_tir.criar()

    assert r["ok"] and r["via"] == "python"
    assert comandos[-1][:3] == ["C:/p312/python.exe", "-m", "venv"]


def test_sem_uv_atualiza_pelo_pip_do_venv(programa, comandos, monkeypatch):
    monkeypatch.setattr(venv_tir.shutil, "which", _which({}))
    monkeypatch.setattr(venv_tir, "existe", lambda: True)

    r = venv_tir.atualizar_tir()

    assert r["ok"]
    python = str(venv_tir.python_do_venv())
    assert comandos[0] == [python, "-m", "ensurepip", "--upgrade"]
    assert comandos[1][:4] == [python, "-m", "pip", "install"]
    assert "tir_framework" in comandos[1]


# ── sem uv e sem 3.12 ─────────────────────────────────────────────────────

def test_python_de_outra_versao_nao_serve(programa, comandos, monkeypatch):
    monkeypatch.setattr(venv_tir.os, "name", "nt")
    monkeypatch.setattr(venv_tir.shutil, "which",
                        _which({"python": "C:/p313/python.exe"}))
    _python_falso(monkeypatch, comandos,
                  {"C:/p313/python.exe": "C:/p313/python.exe\n3.13"})

    r = venv_tir.criar()

    assert not r["ok"]
    assert "3.13" in r["erro"] and "3.12" in r["erro"]
    assert not any("venv" in c for c in comandos)


def test_sem_nada_erro_diz_o_que_instalar(programa, comandos, monkeypatch):
    monkeypatch.setattr(venv_tir.shutil, "which", _which({}))

    r = venv_tir.criar()

    assert not r["ok"]
    assert "uv" in r["erro"] and "python.org" in r["erro"]


def test_provisionador(monkeypatch):
    monkeypatch.setattr(venv_tir.shutil, "which", _which({"uv": "C:/uv.exe"}))
    assert venv_tir.provisionador() == "uv"

    monkeypatch.setattr(venv_tir.shutil, "which", _which({}))
    monkeypatch.setattr(venv_tir, "_python_global", lambda: ("C:/p/python.exe", []))
    assert venv_tir.provisionador() == "python"

    monkeypatch.setattr(venv_tir, "_python_global", lambda: (None, ["3.13"]))
    assert venv_tir.provisionador() is None


def test_venv_existente_nao_recria(programa, comandos, monkeypatch):
    python = programa / ".venv" / "Scripts" / "python.exe"
    python.parent.mkdir(parents=True)
    python.write_bytes(b"")
    monkeypatch.setattr(venv_tir.os, "name", "nt")

    r = venv_tir.criar()

    assert r["ok"] and r["criado"] is False and comandos == []
