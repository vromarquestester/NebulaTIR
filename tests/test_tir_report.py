"""Relatório da execução (`services.tir.tir_report`).

Este módulo roda **no venv do TIR**, num interpretador separado, e é copiado
para dentro da pasta de cada teste. Aqui ele é importado direto do repositório:
o que está sob teste é a montagem do log, não o empacotamento.

Dois defeitos reais motivaram este arquivo, e os dois se manifestaram do mesmo
jeito para o usuário — "só o .log foi gerado, e vazio; PNG nenhum":

1. numa rotina dividida entre instâncias, o carimbo do caso passa por JSON e
   volta como **string**; `carimbo.isoformat()` estourava dentro do `gravar`,
   que já tinha aberto o arquivo em modo "w" — sobrava um .log de zero byte e
   o PNG nunca era montado;
2. o progresso caso a caso não existia, e o painel ficava parado por minutos.
"""

import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src" /
                       "services" / "tir"))

import tir_report  # noqa: E402


AGORA = datetime(2026, 8, 14, 13, 3, 13).astimezone()


def _registro(nome="test_CT015", passou=True, carimbo=AGORA):
    return {"nome": nome, "passou": passou, "carimbo": carimbo,
            "segundos": 170.9, "mensagem": "" if passou else "AssertionError"}


# ── O log de zero byte ──────────────────────────────────────

def test_carimbo_que_veio_de_json_nao_derruba_o_log(tmp_path):
    """A parcial de uma rotina dividida devolve o carimbo como texto."""
    registro = _registro(carimbo=AGORA.isoformat())
    arquivo = tir_report.gravar("SUITE", [registro], AGORA, AGORA, str(tmp_path))
    assert arquivo is not None
    conteudo = arquivo.read_text(encoding="utf-8")
    assert conteudo.strip()
    assert "test_CT015" in conteudo
    assert AGORA.isoformat() in conteudo


def test_falha_ao_montar_nao_deixa_arquivo_truncado(tmp_path, monkeypatch):
    """Arquivo de zero byte parece log gerado e não é: ou sai inteiro, ou não
    sai. O texto é montado antes de qualquer `open`."""
    monkeypatch.setattr(tir_report, "_monta_log",
                        lambda *a: (_ for _ in ()).throw(RuntimeError("boom")))
    assert tir_report.gravar("SUITE", [_registro()], AGORA, AGORA,
                             str(tmp_path)) is None
    assert list(tmp_path.glob("*.log")) == []


def test_log_traz_o_resumo_que_o_relatorio_le(tmp_path):
    registros = [_registro("test_A"), _registro("test_B", passou=False)]
    arquivo = tir_report.gravar("SUITE", registros, AGORA, AGORA, str(tmp_path))
    conteudo = arquivo.read_text(encoding="utf-8")
    assert "Total:  2" in conteudo
    assert "Passou: 1" in conteudo
    assert "Falhou: 1" in conteudo


def test_iso_aceita_datetime_e_texto():
    assert tir_report._iso(AGORA) == AGORA.isoformat()
    assert tir_report._iso("2026-08-14T13:03:13") == "2026-08-14T13:03:13"
    assert tir_report._iso(None) == ""


# ── Progresso em tempo real ─────────────────────────────────

class _TesteFalso:
    _testMethodName = "test_CT015"

    def __init__(self):
        self.failureException = AssertionError

    def id(self):
        return self._testMethodName

    def shortDescription(self):
        return None


def _resultado():
    import io
    return tir_report._ResultadoDetalhado(io.StringIO(), True, 0)


def test_anuncia_inicio_e_fim_de_cada_caso(capsys):
    """O painel não tem outro canal: o resultado da suite só existe no fim, e
    um caso do TIR leva minutos."""
    resultado = _resultado()
    teste = _TesteFalso()
    resultado.startTest(teste)
    resultado.addSuccess(teste)
    resultado.stopTest(teste)

    linhas = [l for l in capsys.readouterr().out.splitlines()
              if l.startswith(tir_report.MARCA_PROGRESSO)]
    assert linhas == [
        f"{tir_report.MARCA_PROGRESSO} INICIO test_CT015",
        f"{tir_report.MARCA_PROGRESSO} FIM test_CT015 ok",
    ]


def test_caso_que_falha_anuncia_erro(capsys):
    resultado = _resultado()
    teste = _TesteFalso()
    resultado.startTest(teste)
    try:
        raise AssertionError("CT015 - [SetValue] Element not found!")
    except AssertionError:
        resultado.addFailure(teste, sys.exc_info())
    resultado.stopTest(teste)

    saida = capsys.readouterr().out
    assert f"{tir_report.MARCA_PROGRESSO} FIM test_CT015 erro" in saida
    assert resultado.registros[0]["passou"] is False
