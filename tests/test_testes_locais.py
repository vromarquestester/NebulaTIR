"""Testes locais (`services.testes_locais`): pasta escolhida pelo usuário.

Um teste é o par TESTSUITE/TESTCASE; o `config.json` é um por pasta, manda na
execução e não é sobrescrito sem o usuário pedir.
"""

import json

import pytest

from services import testes_locais


def _par(pasta, nome, casos=("test_%s_001",)):
    pasta.mkdir(parents=True, exist_ok=True)
    (pasta / f"{nome}TESTSUITE.py").write_text(
        "".join(f'suite.addTest({nome}("{c % nome}"))\n' for c in casos),
        encoding="utf-8")
    (pasta / f"{nome}TESTCASE.py").write_text(f"class {nome}:\n    pass\n",
                                              encoding="utf-8")


def _config(pasta, **campos):
    dados = {"Url": "http://localhost:4321", "Browser": "Firefox",
             "Environment": "PAR", "Language": "es-ES", "POUILogin": True,
             "DebugLog": True, "TimeOut": 90}
    dados.update(campos)
    pasta.mkdir(parents=True, exist_ok=True)
    (pasta / "config.json").write_text(json.dumps(dados), encoding="utf-8")
    return pasta / "config.json"


# ── Varredura ───────────────────────────────────────────────

def test_pasta_com_um_teste(tmp_path):
    _par(tmp_path, "MATA010")
    _config(tmp_path)
    r = testes_locais.escanear(tmp_path)
    assert r["ok"] is True
    assert [t["rotina"] for t in r["testes"]] == ["MATA010"]
    teste = r["testes"][0]
    assert teste["tem_case"] is True
    assert teste["casos"] == ["test_MATA010_001"]
    assert teste["modulo"] == ""
    assert r["config"]["existe"] is True
    assert r["config"]["conteudo"]["Language"] == "es-ES"


def test_pasta_com_varios_testes_em_subpastas(tmp_path):
    _par(tmp_path / "fin", "FINA050")
    _par(tmp_path / "com", "COMA222")
    _par(tmp_path, "MATA010")
    _config(tmp_path)
    r = testes_locais.escanear(tmp_path)
    assert [t["rotina"] for t in r["testes"]] == ["COMA222", "FINA050", "MATA010"]
    assert {t["modulo"] for t in r["testes"]} == {"com", "fin", ""}


def test_suite_sem_case_aparece_marcado(tmp_path):
    (tmp_path / "SOLTOTESTSUITE.py").write_text("x", encoding="utf-8")
    r = testes_locais.escanear(tmp_path)
    assert r["testes"][0]["tem_case"] is False


def test_nome_repetido_o_primeiro_vence(tmp_path):
    _par(tmp_path / "a", "MATA010")
    _par(tmp_path / "b", "MATA010")
    r = testes_locais.escanear(tmp_path)
    assert len(r["testes"]) == 1
    assert r["testes"][0]["modulo"] == "a"


def test_pasta_inexistente(tmp_path):
    r = testes_locais.escanear(tmp_path / "nao")
    assert r["ok"] is False
    assert r["testes"] == []
    assert r["config"]["existe"] is False


def test_pasta_vazia_nao_e_erro(tmp_path):
    r = testes_locais.escanear("")
    assert r["ok"] is False


def test_config_ausente(tmp_path):
    _par(tmp_path, "MATA010")
    r = testes_locais.escanear(tmp_path)
    assert r["config"]["existe"] is False
    assert r["config"]["caminho"].endswith("config.json")


def test_config_na_subpasta_vale_quando_a_raiz_nao_tem(tmp_path):
    _par(tmp_path / "Suite", "MATA010")
    _config(tmp_path / "Suite")
    r = testes_locais.escanear(tmp_path)
    assert r["config"]["existe"] is True
    assert r["config"]["caminho"].endswith("config.json")
    assert "Suite" in r["config"]["caminho"]


def test_config_invalido_vem_com_o_motivo(tmp_path):
    (tmp_path / "config.json").write_text("{nao é json", encoding="utf-8")
    r = testes_locais.escanear(tmp_path)
    assert r["config"]["existe"] is True
    assert r["config"]["conteudo"] is None
    assert "JSON" in r["config"]["erro"]


def test_config_com_bom(tmp_path):
    (tmp_path / "config.json").write_text('{"Language": "es-ES"}',
                                          encoding="utf-8-sig")
    conteudo, erro = testes_locais.ler_config(tmp_path / "config.json")
    assert erro == ""
    assert conteudo == {"Language": "es-ES"}


# ── Validação ───────────────────────────────────────────────

def test_config_correto_nao_diverge():
    config = {"Language": "es-ES", "POUILogin": True, "DebugLog": True,
              "Browser": "Firefox"}
    assert testes_locais.validar(config, "es-ES") == []


def test_cada_campo_errado_aparece_uma_vez():
    config = {"Language": "pt-BR", "POUILogin": False, "DebugLog": "false",
              "Browser": "Chrome"}
    divs = testes_locais.validar(config, "es-ES")
    assert [d["chave"] for d in divs] == ["Language", "POUILogin", "DebugLog",
                                          "Browser"]
    por_chave = {d["chave"]: d for d in divs}
    assert por_chave["Language"]["encontrado"] == "pt-BR"
    assert por_chave["Language"]["esperado"] == "es-ES"
    assert por_chave["POUILogin"]["encontrado"] == "false"
    assert por_chave["POUILogin"]["esperado"] == "true"
    assert por_chave["Browser"]["nivel"] == "aviso"
    assert por_chave["POUILogin"]["nivel"] == "erro"
    assert all(d["motivo"] for d in divs)


def test_campo_ausente_diverge_como_ausente():
    divs = testes_locais.validar({"Language": "es-ES"}, "es-ES")
    assert {d["chave"]: d["encontrado"] for d in divs} == {
        "POUILogin": "ausente", "DebugLog": "ausente", "Browser": "ausente"}


def test_config_nulo_diverge_em_tudo():
    assert len(testes_locais.validar(None, "es-ES")) == 4


def test_sem_idioma_conhecido_nao_confere_idioma():
    config = {"Language": "pt-BR", "POUILogin": True, "DebugLog": True,
              "Browser": "Firefox"}
    assert testes_locais.validar(config, "") == []


def test_navegador_e_booleano_sem_olhar_caixa():
    config = {"Language": "ES-es", "POUILogin": "True", "DebugLog": "TRUE",
              "Browser": "firefox"}
    assert testes_locais.validar(config, "es-ES") == []


def test_chave_em_outra_caixa_e_lida():
    config = {"language": "es-ES", "pouilogin": True, "debuglog": True,
              "browser": "Firefox"}
    assert testes_locais.validar(config, "es-ES") == []


# ── Correção (só a pedido) ──────────────────────────────────

def test_corrigir_troca_so_o_que_diverge_e_mantem_o_resto(tmp_path):
    caminho = _config(tmp_path, Language="pt-BR", POUILogin=False,
                      Browser="Chrome", Coverage=True, Extra="fica")
    r = testes_locais.corrigir(caminho, "es-ES")
    assert r["ok"] is True
    assert r["alteradas"] == ["Language", "POUILogin", "Browser"]
    gravado = json.loads(caminho.read_text(encoding="utf-8"))
    assert gravado["Language"] == "es-ES"
    assert gravado["POUILogin"] is True
    assert gravado["DebugLog"] is True
    assert gravado["Browser"] == "Firefox"
    assert gravado["Coverage"] is True
    assert gravado["Extra"] == "fica"
    assert gravado["Url"] == "http://localhost:4321"
    assert testes_locais.validar(gravado, "es-ES") == []


def test_corrigir_sem_divergencia_nao_reescreve(tmp_path):
    caminho = _config(tmp_path)
    antes = caminho.stat().st_mtime_ns
    r = testes_locais.corrigir(caminho, "es-ES")
    assert r["ok"] is True
    assert r["alteradas"] == []
    assert caminho.stat().st_mtime_ns == antes


def test_corrigir_chave_em_outra_caixa_vira_a_grafia_do_tir(tmp_path):
    caminho = tmp_path / "config.json"
    caminho.write_text(json.dumps({"language": "pt-BR", "POUILogin": True,
                                   "DebugLog": True, "Browser": "Firefox"}),
                       encoding="utf-8")
    r = testes_locais.corrigir(caminho, "es-ES")
    gravado = json.loads(caminho.read_text(encoding="utf-8"))
    assert r["alteradas"] == ["Language"]
    assert "language" not in gravado
    assert gravado["Language"] == "es-ES"


def test_corrigir_config_invalido_falha_sem_gravar(tmp_path):
    caminho = tmp_path / "config.json"
    caminho.write_text("{quebrado", encoding="utf-8")
    r = testes_locais.corrigir(caminho, "es-ES")
    assert r["ok"] is False
    assert caminho.read_text(encoding="utf-8") == "{quebrado"


@pytest.mark.parametrize("valor,texto", [
    (None, "ausente"), (True, "true"), (False, "false"), ("", "vazio"),
    (" pt-BR ", "pt-BR"), (90, "90"),
])
def test_texto_do_valor_na_mensagem(valor, texto):
    assert testes_locais._texto(valor) == texto
