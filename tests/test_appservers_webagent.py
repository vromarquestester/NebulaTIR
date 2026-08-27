"""WebAgent da estação na subida das instâncias (`services.appservers`).

O agente é **um só na estação** e a família dele acompanha a release do
Protheus: a 2610 pede `1.1.2-RC4`, as anteriores `1.0.25`. O Gerenciador troca
isso no "Subir" dele; quem sobe as instâncias paralelas é o NebulaTIR, que não
passa por lá — daí esta conferência.

A troca em si roda no Gerenciador (rota `/webagent`). Aqui só se verifica que a
decisão é tomada, que ela nunca derruba a corrida e que o aviso chega.
"""

import pytest

from services import appservers
from services.instancias import Instancias


def _detalhes(**webagent):
    """Dublê do `detalhes_por_nome` do canal."""
    def _ler(nome):
        d = {"ok": True, "banco": {"appserver_exe": "x"}}
        if webagent:
            d["webagent"] = dict(webagent)
        return d
    return _ler


# ── Decisão ────────────────────────────────────────────────

def test_agente_certo_nao_pede_troca():
    chamou = []
    r = appservers.garantir_webagent(
        "PAR_2610",
        _detalhes(sincronizado=True, alvo="1.1.2-rc4", estacao="1.1.2-RC4"),
        lambda nome: chamou.append(nome))
    assert r["ok"] is True and r["trocado"] is False
    assert chamou == []


def test_agente_de_outra_release_dispara_a_troca():
    """O caso real: estação com o agente da 2510 e ambiente 2610 para subir."""
    pedidos = []

    def _sincronizar(nome):
        pedidos.append(nome)
        return {"ok": True, "trocado": True, "de": "1.0.25", "para": "1.1.2-RC4"}

    r = appservers.garantir_webagent(
        "PAR_2610",
        _detalhes(sincronizado=False, alvo="1.1.2-rc4", estacao="1.0.25",
                  tem_instalador=True, versao_protheus="2610"),
        _sincronizar)
    assert pedidos == ["PAR_2610"]
    assert r["ok"] is True and r["trocado"] is True and r["para"] == "1.1.2-RC4"


def test_sem_instalador_avisa_e_nao_tenta_trocar():
    chamou = []
    r = appservers.garantir_webagent(
        "PAR_2610",
        _detalhes(sincronizado=False, alvo="1.1.2-rc4", estacao="1.0.25",
                  tem_instalador=False, versao_protheus="2610"),
        lambda nome: chamou.append(nome))
    assert r["ok"] is False and chamou == []
    assert "1.1.2-rc4" in r["aviso"] and "Executar completo" in r["aviso"]


def test_falha_da_troca_vira_aviso():
    r = appservers.garantir_webagent(
        "PAR_2610",
        _detalhes(sincronizado=False, alvo="1.1.2-rc4", estacao="1.0.25",
                  tem_instalador=True),
        lambda nome: {"ok": False, "erro": "instalador não respondeu"})
    assert r["ok"] is False
    assert "instalador não respondeu" in r["aviso"] and "1.0.25" in r["aviso"]


def test_gerenciador_sem_a_rota_nao_inventa_alvo():
    """Versão anterior do canal não devolve `webagent`. Deduzir a versão aqui
    criaria a segunda verdade que o canal existe para evitar."""
    r = appservers.garantir_webagent("PAR_2610", _detalhes(), None)
    assert r == {"ok": True, "conferido": False}


def test_ambiente_sumido_do_gerenciador_vira_aviso():
    r = appservers.garantir_webagent(
        "PAR_2610", lambda nome: {"ok": False, "erro": "não existe mais"}, None)
    assert r["ok"] is False and r["aviso"] == "não existe mais"


# ── Integração com a subida ────────────────────────────────

@pytest.fixture
def subida_sem_processo(monkeypatch):
    monkeypatch.setattr(appservers, "porta_responde", lambda *a, **k: True)
    monkeypatch.setattr(appservers, "INTERVALO_SONDA_SEG", 0)
    monkeypatch.setattr(appservers.appserver_ini, "aplicar_portas",
                        lambda ini, portas: {"ok": True})
    monkeypatch.setattr(appservers.appserver_ini, "desativar_webmonitor",
                        lambda ini: {"ok": True})
    monkeypatch.setattr(appservers.appserver_ini, "aplicar_specialkey",
                        lambda ini, sufixo: {"mudou": False})
    monkeypatch.setattr(appservers, "subir",
                        lambda exe, params="": {"ok": True, "pid": 555})


def test_conferencia_e_uma_so_por_corrida(tmp_path, subida_sem_processo):
    """As instâncias são clones do mesmo ambiente, logo da mesma release —
    uma chamada por instância seria só ida e volta no canal."""
    reg = Instancias(tmp_path / "instancias.json")
    for slot in (1, 2, 3):
        reg.registrar(ambiente=f"A_TIR{slot}", origem="A", slot=slot,
                      banco=f"B{slot}", portas={"webapp": 4320 + slot})

    pedidos = []
    appservers.subir_para_instancias(
        reg.listar("A"), reg,
        _detalhes(sincronizado=False, alvo="1.1.2-rc4", estacao="1.0.25",
                  tem_instalador=True),
        dbaccess_por_instancia=False,
        sincronizar_webagent=lambda nome: (pedidos.append(nome),
                                           {"ok": True, "trocado": True})[1])
    assert pedidos == ["A_TIR1"]


def test_agente_errado_avisa_mas_a_corrida_segue(tmp_path, subida_sem_processo):
    """Subir com o agente errado é problema de navegador; barrar a corrida
    por isso seria pior que avisar."""
    reg = Instancias(tmp_path / "instancias.json")
    reg.registrar(ambiente="A_TIR1", origem="A", slot=1, banco="B1",
                  portas={"webapp": 4321})

    r = appservers.subir_para_instancias(
        reg.listar("A"), reg,
        _detalhes(sincronizado=False, alvo="1.1.2-rc4", estacao="1.0.25",
                  tem_instalador=True),
        dbaccess_por_instancia=False,
        sincronizar_webagent=lambda nome: {"ok": False, "erro": "sem permissão"})
    assert [s["ambiente"] for s in r["subidos"]] == ["A_TIR1"]
    assert r["avisos"] and "1.1.2-rc4" in r["avisos"][0]
