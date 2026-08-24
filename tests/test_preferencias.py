"""Preferências globais (`services.preferencias`)."""

import json

from services.preferencias import Preferencias


def _prefs(tmp_path):
    return Preferencias(tmp_path / "preferencias.json")


def test_padrao_tres_instancias_sequencial(tmp_path):
    p = _prefs(tmp_path)
    assert p.max_instancias == 3
    assert p.modo == "sequencial"
    assert p.paralelo is False


def test_limite_e_editavel_e_nao_tem_teto(tmp_path):
    """Máquina mais potente aguenta mais; quem sabe disso é o usuário."""
    p = _prefs(tmp_path)
    p.salvar({"max_instancias": 32})
    assert p.max_instancias == 32
    assert _prefs(tmp_path).max_instancias == 32     # persistiu


def test_piso_de_uma_instancia(tmp_path):
    """Zero instância não executa nada."""
    p = _prefs(tmp_path)
    for valor in (0, -5, "0"):
        p.salvar({"max_instancias": valor})
        assert p.max_instancias == 1


def test_valor_invalido_volta_ao_padrao(tmp_path):
    p = _prefs(tmp_path)
    p.salvar({"max_instancias": "muitas"})
    assert p.max_instancias == 3


def test_modo_invalido_volta_ao_padrao(tmp_path):
    p = _prefs(tmp_path)
    p.salvar({"modo": "turbo"})
    assert p.modo == "sequencial"
    p.salvar({"modo": "paralelo"})
    assert p.paralelo is True


def test_chave_desconhecida_e_descartada(tmp_path):
    p = _prefs(tmp_path)
    p.salvar({"modo": "paralelo", "invadido": True})
    dados = json.loads((tmp_path / "preferencias.json").read_text(encoding="utf-8"))
    assert "invadido" not in dados


def test_arquivo_corrompido_nao_derruba(tmp_path):
    (tmp_path / "preferencias.json").write_text("{ nao é json", encoding="utf-8")
    assert _prefs(tmp_path).max_instancias == 3


def test_log_fixado_persiste(tmp_path):
    """Governa só o fechamento automático — o painel nasce contraído sempre."""
    p = _prefs(tmp_path)
    assert p.tudo["log_fixado"] is False
    p.salvar({"log_fixado": True})
    assert _prefs(tmp_path).tudo["log_fixado"] is True
    p.salvar({"log_fixado": "não é bool"})
    assert p.tudo["log_fixado"] is False


def test_raiz_apontada_vence_a_deteccao(tmp_path, monkeypatch):
    import services.preferencias as mod
    monkeypatch.setattr(mod, "descobrir_raiz", lambda *a, **k: tmp_path / "detectada")
    p = _prefs(tmp_path)
    p.salvar({"raiz_testes": str(tmp_path / "minha")})
    assert p.raiz_testes == tmp_path / "minha"
    assert p.fontes["detectada"] is False


def test_raiz_vazia_usa_a_deteccao(tmp_path, monkeypatch):
    import services.preferencias as mod
    monkeypatch.setattr(mod, "descobrir_raiz", lambda *a, **k: tmp_path / "achada")
    p = _prefs(tmp_path)
    assert p.raiz_testes == tmp_path / "achada"
    assert p.fontes["detectada"] is True


def test_limpar_o_campo_volta_a_detectar(tmp_path, monkeypatch):
    import services.preferencias as mod
    monkeypatch.setattr(mod, "descobrir_raiz", lambda *a, **k: tmp_path / "achada")
    p = _prefs(tmp_path)
    p.salvar({"raiz_testes": str(tmp_path / "minha")})
    p.salvar({"raiz_testes": ""})
    assert p.raiz_testes == tmp_path / "achada"


def test_sem_deteccao_cai_no_caminho_classico(tmp_path, monkeypatch):
    import services.preferencias as mod
    monkeypatch.setattr(mod, "descobrir_raiz", lambda *a, **k: None)
    assert "Automação Protheus" in str(_prefs(tmp_path).raiz_testes)
