"""Cliente do canal e cache de estado (`services.gerenciador_client`).

O foco é o gate: `online` tem que virar False na hora em que o Gerenciador
some, e voltar sozinho quando ele reaparece — inclusive numa porta nova, que é
o que acontece de verdade a cada reabertura.
"""

import json
import os

from services.gerenciador_client import (
    EstadoGerenciador,
    GerenciadorClient,
    GerenciadorOffline,
)


def _client(registro):
    return GerenciadorClient(registro=registro)


# ── Descoberta ──────────────────────────────────────────────

def test_descobre_porta_e_token(registro, bridge_falso):
    dados = _client(registro).descobrir()
    assert dados["port"] == bridge_falso.port
    assert dados["token"] == bridge_falso.token


def test_sem_registro_fica_offline(tmp_path):
    c = _client(tmp_path / "nao-existe.json")
    assert c.descobrir() is None
    try:
        c.health()
        assert False, "deveria ter levantado GerenciadorOffline"
    except GerenciadorOffline:
        pass


def test_registro_orfao_de_processo_morto_e_ignorado(tmp_path, bridge_falso):
    """Crash do Gerenciador deixa o bridge.json para trás; o PID desempata."""
    arquivo = tmp_path / "bridge.json"
    arquivo.write_text(json.dumps({
        "app": "gerenciador-ambientes",
        "port": bridge_falso.port,
        "token": bridge_falso.token,
        "pid": 999999,          # PID que não existe
    }), encoding="utf-8")
    assert _client(arquivo).descobrir() is None


def test_registro_de_outro_app_e_ignorado(tmp_path, bridge_falso):
    arquivo = tmp_path / "bridge.json"
    arquivo.write_text(json.dumps({
        "app": "outra-coisa", "port": bridge_falso.port,
        "token": bridge_falso.token, "pid": os.getpid(),
    }), encoding="utf-8")
    assert _client(arquivo).descobrir() is None


# ── Chamadas ────────────────────────────────────────────────

def test_health_e_ambientes(registro):
    c = _client(registro)
    assert c.health()["versao"] == "1.2.3"
    payload = c.ambientes()
    assert [b["ambiente"] for b in payload["bancos"]] == ["PAR_2510", "BRA_2410"]


def test_token_recusado_vira_offline(registro, bridge_falso):
    bridge_falso.token = "outro-token"   # o Gerenciador reiniciou
    try:
        _client(registro).health()
        assert False, "deveria ter levantado GerenciadorOffline"
    except GerenciadorOffline as e:
        assert "Token" in str(e)


def test_timeout_vira_offline(registro, bridge_falso, monkeypatch):
    import services.gerenciador_client as mod
    monkeypatch.setattr(mod, "TIMEOUT_SEG", 0.2)
    bridge_falso.atraso = 1.0
    try:
        _client(registro).health()
        assert False, "deveria ter levantado GerenciadorOffline"
    except GerenciadorOffline:
        pass


# ── EstadoGerenciador (o cache que o gate lê) ───────────────

def test_estado_online_traz_ambientes(registro):
    estado = EstadoGerenciador(client=_client(registro))
    instantaneo = estado.atualizar()
    assert instantaneo["online"] is True
    assert instantaneo["vpn"] is True
    assert instantaneo["ambientes"]["PAR_2510"]["estado"] == "running"
    assert estado.banco_por_nome("BRA_2410")["port"] == "4322"
    assert estado.indice_por_nome("BRA_2410") == 1


def test_gerenciador_cai_no_meio_da_sessao(registro, bridge_falso):
    """O caso que mais quebra na prática: o gate tem que fechar.

    `tolerancia_seg=0` para testar a queda em si. Em produção há uma janela de
    tolerância, porque o Gerenciador some do ar durante operações longas — ver
    os testes de instabilidade mais abaixo.
    """
    estado = EstadoGerenciador(client=_client(registro), tolerancia_seg=0)
    assert estado.atualizar()["online"] is True

    bridge_falso.parar()
    instantaneo = estado.atualizar()
    assert instantaneo["online"] is False
    assert instantaneo["ambientes"] == {}
    assert instantaneo["vpn"] is None      # sem link, não se afirma nada
    assert instantaneo["motivo"]


def test_gerenciador_volta_em_outra_porta(tmp_path, bridge_falso):
    """A porta é efêmera: reabrir o Gerenciador muda porta e token."""
    from tests.conftest import BridgeFalso

    arquivo = tmp_path / "bridge.json"
    arquivo.write_text(json.dumps({
        "app": "gerenciador-ambientes", "port": bridge_falso.port,
        "token": bridge_falso.token, "pid": os.getpid(),
    }), encoding="utf-8")
    estado = EstadoGerenciador(client=_client(arquivo), tolerancia_seg=0)
    assert estado.atualizar()["online"] is True

    bridge_falso.parar()
    assert estado.atualizar()["online"] is False

    novo = BridgeFalso()
    try:
        arquivo.write_text(json.dumps({
            "app": "gerenciador-ambientes", "port": novo.port,
            "token": novo.token, "pid": os.getpid(),
        }), encoding="utf-8")
        assert estado.atualizar()["online"] is True
    finally:
        novo.parar()


# ── Tolerância a indisponibilidade passageira ───────────────

def test_falha_curta_nao_derruba_o_link(registro, bridge_falso):
    """Restaurar banco faz attach de arquivos de vários GB e o Gerenciador
    some do ar por dezenas de segundos. Derrubar o link nesse intervalo
    interrompia a corrida por causa de uma indisponibilidade passageira."""
    estado = EstadoGerenciador(client=_client(registro), tolerancia_seg=60)
    assert estado.atualizar()["online"] is True

    bridge_falso.parar()
    instantaneo = estado.atualizar()
    assert instantaneo["online"] is True       # ainda dentro da tolerância
    assert instantaneo["instavel"] is True
    assert "Sem resposta" in instantaneo["motivo"]


def test_estado_anterior_e_preservado_durante_a_instabilidade(registro, bridge_falso):
    """A UI não pode piscar tudo em branco por um timeout."""
    estado = EstadoGerenciador(client=_client(registro), tolerancia_seg=60)
    estado.atualizar()
    bridge_falso.parar()
    instantaneo = estado.atualizar()
    assert instantaneo["ambientes"]["PAR_2510"]["estado"] == "running"
    assert [b["ambiente"] for b in instantaneo["bancos"]] == ["PAR_2510", "BRA_2410"]


def test_falha_longa_derruba_o_link(registro, bridge_falso):
    estado = EstadoGerenciador(client=_client(registro), tolerancia_seg=0)
    estado.atualizar()
    bridge_falso.parar()
    assert estado.atualizar()["online"] is False


def test_volta_a_responder_zera_a_tolerancia(tmp_path, bridge_falso):
    """Depois de uma instabilidade, a próxima falha começa a contar do zero."""
    import json
    import os
    arquivo = tmp_path / "bridge.json"
    arquivo.write_text(json.dumps({
        "app": "gerenciador-ambientes", "port": bridge_falso.port,
        "token": bridge_falso.token, "pid": os.getpid(),
    }), encoding="utf-8")

    estado = EstadoGerenciador(client=_client(arquivo), tolerancia_seg=60)
    estado.atualizar()
    bridge_falso.token = "errado"          # canal recusa: falha
    assert estado.atualizar()["instavel"] is True
    bridge_falso.token = arquivo and json.loads(
        arquivo.read_text(encoding="utf-8"))["token"]
    assert estado.atualizar()["instavel"] is False


def test_link_offline_desde_o_inicio_nao_e_instavel(tmp_path):
    """Sem nunca ter conectado, não há o que tolerar."""
    estado = EstadoGerenciador(client=_client(tmp_path / "nao-existe.json"),
                               tolerancia_seg=60)
    instantaneo = estado.atualizar()
    assert instantaneo["online"] is False
    assert instantaneo["instavel"] is False


def test_indice_e_nome_nao_se_confundem(registro, bridge_falso):
    """Remover um ambiente no Gerenciador desloca os índices; o nome é a chave."""
    estado = EstadoGerenciador(client=_client(registro))
    estado.atualizar()
    assert estado.indice_por_nome("BRA_2410") == 1

    bridge_falso.payload["bancos"].pop(0)   # PAR_2510 removido lá
    estado.atualizar()
    assert estado.indice_por_nome("BRA_2410") == 0
    assert estado.banco_por_nome("PAR_2510") is None
