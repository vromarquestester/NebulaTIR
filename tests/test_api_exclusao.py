"""Exclusão de paralelos em segundo plano (`webui.api`).

Cada remoção leva minutos — detach do banco, apagar pastas de GB, DSN. Antes
a chamada era síncrona: a tela congelava e todos os ambientes sumiam de uma
vez no fim, enquanto o Gerenciador ainda apagava um a um.
"""

import time

import pytest

from services.gerenciador_client import EstadoGerenciador, GerenciadorClient
from webui.api import Api


@pytest.fixture
def api(tmp_path, registro):
    estado = EstadoGerenciador(client=GerenciadorClient(registro=registro),
                               tolerancia_seg=0)
    estado.atualizar()
    a = Api(arquivo_importados=tmp_path / "importados.json",
            iniciar_monitores=False, instalar_log=False, estado=estado,
            arquivo_preferencias=tmp_path / "preferencias.json",
            arquivo_instancias=tmp_path / "instancias.json",
            checar_porta=lambda p: True)
    for i in (1, 2, 3):
        a._instancias.registrar(ambiente=f"PAR_2510_TIR{i}", origem="PAR_2510",
                                slot=i, banco=f"B{i}", portas={})
    return a


def _esperar_fim(api, limite=10):
    fim = time.monotonic() + limite
    while time.monotonic() < fim:
        if not api.estado_exclusao()["ativa"]:
            return True
        time.sleep(0.05)
    return False


@pytest.fixture
def sem_gerenciador(api, monkeypatch):
    """Ambientes que já não existem lá: a remoção é só limpar o registro."""
    monkeypatch.setattr(api._estado, "indice_por_nome", lambda nome: None)
    return api


# ── Assincronia ─────────────────────────────────────────────

def test_devolve_na_hora_e_informa_o_total(sem_gerenciador):
    r = sem_gerenciador.excluir_paralelos(["PAR_2510_TIR1", "PAR_2510_TIR2"])
    assert r["ok"] is True
    assert r["iniciado"] is True
    assert r["total"] == 2
    _esperar_fim(sem_gerenciador)


def test_estado_acompanha_o_progresso(sem_gerenciador):
    sem_gerenciador.excluir_paralelos(["PAR_2510_TIR1", "PAR_2510_TIR2"])
    assert _esperar_fim(sem_gerenciador)
    estado = sem_gerenciador.estado_exclusao()
    assert estado["ativa"] is False
    assert estado["feitos"] == 2
    assert sorted(estado["removidos"]) == ["PAR_2510_TIR1", "PAR_2510_TIR2"]


def test_registro_encolhe_aos_poucos(sem_gerenciador):
    """Cada ambiente sai do registro assim que termina, para a lista encolher
    na tela no mesmo ritmo em que o Gerenciador apaga."""
    sem_gerenciador.excluir_paralelos(["PAR_2510_TIR1"])
    assert _esperar_fim(sem_gerenciador)
    assert "PAR_2510_TIR1" not in sem_gerenciador._instancias.nomes()
    assert len(sem_gerenciador._instancias.nomes()) == 2


def test_recusa_segunda_exclusao_simultanea(api, monkeypatch):
    """Duas exclusões ao mesmo tempo brigariam pela fila do Gerenciador."""
    monkeypatch.setattr(api._estado, "indice_por_nome", lambda nome: 0)
    monkeypatch.setattr(api._estado, "remover_ambiente",
                        lambda nome: time.sleep(0.5) or {"ok": True})
    api.excluir_paralelos(["PAR_2510_TIR1"])
    segunda = api.excluir_paralelos(["PAR_2510_TIR2"])
    assert segunda["ok"] is False
    assert "andamento" in segunda["erro"]


# ── Trava dos comandos ──────────────────────────────────────

def test_status_expoe_a_exclusao_para_a_tela(sem_gerenciador):
    sem_gerenciador.excluir_paralelos(["PAR_2510_TIR1"])
    assert "exclusao" in sem_gerenciador.get_status()
    _esperar_fim(sem_gerenciador)


def test_executar_tir_bloqueado_durante_a_exclusao(api, monkeypatch):
    monkeypatch.setattr(api._estado, "indice_por_nome", lambda nome: 0)
    monkeypatch.setattr(api._estado, "remover_ambiente",
                        lambda nome: time.sleep(0.5) or {"ok": True})
    api.importar_ambiente("PAR_2510")
    api.salvar_selecao("PAR_2510", ["MATA143"])
    api.excluir_paralelos(["PAR_2510_TIR1"])

    r = api.pode_executar("PAR_2510")
    assert r["ok"] is False
    assert "aguarde" in r["motivo"].lower()


def test_nada_selecionado_nao_inicia(api):
    assert api.excluir_paralelos([])["ok"] is False
    assert api.estado_exclusao()["ativa"] is False
