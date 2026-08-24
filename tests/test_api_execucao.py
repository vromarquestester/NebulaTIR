"""Liberação do botão Executar TIR (`webui.api.pode_executar`).

O caso que motivou: depois de abortar, o botão ficava desabilitado para
sempre. Abortar deve interromper a corrida e devolver a liberdade de executar
de novo em seguida.
"""

import queue

import pytest

from services import config_tir, execucao
from services.gerenciador_client import EstadoGerenciador, GerenciadorClient
from webui.api import Api


@pytest.fixture
def api(tmp_path, registro):
    # Sem tolerância: estes testes exercitam o gate, não a janela de
    # indisponibilidade passageira (que tem testes próprios).
    estado = EstadoGerenciador(client=GerenciadorClient(registro=registro),
                               tolerancia_seg=0)
    estado.atualizar()
    a = Api(arquivo_importados=tmp_path / "importados.json",
            iniciar_monitores=False, instalar_log=False, estado=estado,
            arquivo_preferencias=tmp_path / "preferencias.json",
            arquivo_instancias=tmp_path / "instancias.json",
            checar_porta=lambda p: True)
    a.importar_ambiente("PAR_2510")
    a.salvar_selecao("PAR_2510", ["MATA143"])
    return a


def _corrida(api, ativa=True):
    """Coloca uma corrida no lugar, sem subir processo nenhum."""
    corrida = execucao.Execucao(
        ambiente="PAR_2510", rotinas=[], config=config_tir.PADRAO,
        estado_gerenciador=api._estado, fila_eventos=queue.Queue())

    class ThreadPresa:
        def is_alive(self):
            return ativa
    corrida._threads = [ThreadPresa()]
    api._execucao = corrida
    return corrida


def test_libera_com_tudo_pronto(api):
    assert api.pode_executar("PAR_2510")["ok"] is True


def test_bloqueia_durante_a_corrida(api):
    _corrida(api)
    r = api.pode_executar("PAR_2510")
    assert r["ok"] is False
    assert "andamento" in r["motivo"]


def test_libera_logo_apos_abortar(api):
    """O ponto: abortar interrompe os testes e devolve a liberdade de rodar
    de novo, mesmo que uma thread demore a morrer."""
    corrida = _corrida(api, ativa=True)
    api.abortar_tir()
    assert corrida.ativa is False
    assert api.pode_executar("PAR_2510")["ok"] is True


def test_abortar_sem_corrida_avisa(api):
    assert api.abortar_tir()["ok"] is False


def test_bloqueia_sem_rotina_confirmada(api):
    api.salvar_selecao("PAR_2510", [])
    r = api.pode_executar("PAR_2510")
    assert r["ok"] is False
    assert "rotina" in r["motivo"]


def test_bloqueia_com_gerenciador_offline(api, bridge_falso):
    bridge_falso.parar()
    api._estado.atualizar()
    assert api.pode_executar("PAR_2510")["ok"] is False


def test_bloqueia_com_vpn_offline(api, bridge_falso):
    bridge_falso.payload["vpn"] = False
    api._estado.atualizar()
    r = api.pode_executar("PAR_2510")
    assert r["ok"] is False
    assert "VPN" in r["motivo"]


def test_motivo_sempre_acompanha_o_bloqueio(api, bridge_falso):
    """Botão apagado sem explicação faz o usuário achar que travou."""
    bridge_falso.payload["config_valida"] = False
    api._estado.atualizar()
    r = api.pode_executar("PAR_2510")
    assert r["ok"] is False
    assert r["motivo"].strip()


# ── Parar instâncias e o DbAccess ───────────────────────────
# O DbAccess é um só para todas as instâncias (o `dbaccess.ini` indexa por
# alias). Enquanto sobrar uma de pé ele precisa continuar; sem nenhuma, ele
# fica órfão — some da tela, segue segurando arquivo, e derruba a exclusão do
# ambiente depois sem dizer por quê.

def _com_instancias(api, nomes):
    for slot, nome in enumerate(nomes, start=1):
        api._instancias.registrar(ambiente=nome, origem="PAR_2510", slot=slot,
                                  banco=f"BANCO_{slot}", portas={})
    return api


def test_parar_todas_leva_o_dbaccess_junto(api, monkeypatch):
    from webui import api as modulo

    _com_instancias(api, ["PAR_2510_TIR1", "PAR_2510_TIR2"])
    monkeypatch.setattr(api._instancias, "parar", lambda alvos: {"ok": True})
    parou = []
    monkeypatch.setattr(modulo.appservers, "parar_dbaccess",
                        lambda: parou.append(True) or True)

    r = api.parar_paralelos(["PAR_2510_TIR1", "PAR_2510_TIR2"])
    assert r["ok"] is True
    assert r["dbaccess_parado"] is True
    assert parou == [True]


def test_parar_uma_so_preserva_o_dbaccess(api, monkeypatch):
    """A instância que continua de pé precisa dele."""
    from webui import api as modulo

    _com_instancias(api, ["PAR_2510_TIR1", "PAR_2510_TIR2"])
    monkeypatch.setattr(api._instancias, "parar", lambda alvos: {"ok": True})
    monkeypatch.setattr(modulo.appservers, "parar_dbaccess",
                        lambda: pytest.fail("derrubou o DbAccess da outra"))

    r = api.parar_paralelos(["PAR_2510_TIR1"])
    assert r["ok"] is True
    assert "dbaccess_parado" not in r
