"""Aba "Casos de teste" com origem local (`Api` + `services.testes_locais`).

Origem, pasta e seleção são por ambiente. O `config.json` da pasta manda na
execução: é conferido, o usuário decide se ajusta, e vai para a corrida como
está — sem normalizar, sem trava, em sequencial.
"""

import json

import pytest

from services.gerenciador_client import EstadoGerenciador, GerenciadorClient
from webui import api as api_mod
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
    a.importar_ambiente("PAR_2510")
    return a


@pytest.fixture
def pasta_local(tmp_path):
    pasta = tmp_path / "meus_testes"
    pasta.mkdir()
    for nome in ("MATA010", "FINA050"):
        (pasta / f"{nome}TESTSUITE.py").write_text(
            f'suite.addTest({nome}("test_{nome}_001"))\n'
            f'suite.addTest({nome}("test_{nome}_002"))\n', encoding="utf-8")
        (pasta / f"{nome}TESTCASE.py").write_text("pass\n", encoding="utf-8")
    (pasta / "config.json").write_text(json.dumps({
        "Url": "http://localhost:4321", "Browser": "Chrome",
        "Environment": "PAR_2510", "Language": "pt-BR", "POUILogin": False,
        "DebugLog": True, "TimeOut": 45, "Coverage": False,
    }), encoding="utf-8")
    return pasta


# ── Origem ──────────────────────────────────────────────────

def test_origem_padrao_e_fontes(api):
    r = api.get_origem_testes("PAR_2510")
    assert r == {"ok": True, "origem": "fontes", "pasta": ""}


def test_trocar_origem_persiste_e_devolve_a_arvore(api, tmp_path):
    r = api.salvar_origem_testes("PAR_2510", "local")
    assert r["ok"] is True
    assert r["origem"] == "local"
    assert r["arvore"] == []
    dados = json.loads((tmp_path / "importados.json").read_text(encoding="utf-8"))
    assert dados["ambientes"][0]["origem_testes"] == "local"


def test_origem_desconhecida_cai_em_fontes(api):
    assert api.salvar_origem_testes("PAR_2510", "outra")["origem"] == "fontes"


def test_origem_de_nao_importado_falha(api):
    assert api.get_origem_testes("BRA_2410")["ok"] is False
    assert api.salvar_origem_testes("BRA_2410", "local")["ok"] is False


# ── Pasta e listagem ────────────────────────────────────────

def test_sem_pasta_a_lista_pede_a_pasta(api):
    r = api.listar_testes_locais("PAR_2510")
    assert r["ok"] is False
    assert "pasta" in r["erro"].lower()
    assert r["rotinas"] == []


def test_salvar_pasta_lista_os_testes_e_confere_o_config(api, pasta_local):
    r = api.salvar_pasta_local("PAR_2510", str(pasta_local))
    assert r["ok"] is True
    assert [t["rotina"] for t in r["rotinas"]] == ["FINA050", "MATA010"]
    assert r["total"] == 2
    assert r["idioma"] == "es-ES"           # Paraguai
    assert r["config"]["existe"] is True
    # `conteudo` não vai para a tela: a divergência já resume o que importa.
    assert "conteudo" not in r["config"]
    assert [d["chave"] for d in r["config"]["divergencias"]] == [
        "Language", "POUILogin", "Browser"]


def test_escolher_pasta_pelo_dialogo(api, pasta_local, monkeypatch):
    monkeypatch.setattr(api, "escolher_pasta",
                        lambda inicial="": {"ok": True, "caminho": str(pasta_local)})
    r = api.escolher_pasta_local("PAR_2510")
    assert r["ok"] is True
    assert r["pasta"] == str(pasta_local)
    assert api.get_origem_testes("PAR_2510")["pasta"] == str(pasta_local)


def test_cancelar_o_dialogo_nao_mexe_na_pasta(api, pasta_local, monkeypatch):
    api.salvar_pasta_local("PAR_2510", str(pasta_local))
    monkeypatch.setattr(api, "escolher_pasta",
                        lambda inicial="": {"ok": False, "cancelado": True})
    r = api.escolher_pasta_local("PAR_2510")
    assert r["ok"] is False
    assert api.get_origem_testes("PAR_2510")["pasta"] == str(pasta_local)


def test_busca_filtra_os_testes_locais(api, pasta_local):
    api.salvar_pasta_local("PAR_2510", str(pasta_local))
    r = api.listar_testes_locais("PAR_2510", "fina")
    assert [t["rotina"] for t in r["rotinas"]] == ["FINA050"]
    assert r["total"] == 2


def test_pasta_sem_config_lista_os_testes_mesmo_assim(api, pasta_local):
    (pasta_local / "config.json").unlink()
    r = api.salvar_pasta_local("PAR_2510", str(pasta_local))
    assert r["ok"] is True
    assert len(r["rotinas"]) == 2
    assert r["config"]["existe"] is False
    assert r["config"]["divergencias"] == []


# ── Seleção por origem ──────────────────────────────────────

def test_selecoes_das_duas_origens_nao_se_misturam(api, pasta_local):
    api.salvar_pasta_local("PAR_2510", str(pasta_local))
    api.salvar_selecao("PAR_2510", ["MATA143"])
    api.salvar_selecao_local("PAR_2510", ["MATA010", "FINA050"])

    api.salvar_origem_testes("PAR_2510", "local")
    arvore = api.get_selecao("PAR_2510")
    assert arvore["origem"] == "local"
    assert [r["rotina"] for r in arvore["arvore"]] == ["MATA010", "FINA050"]
    assert arvore["total_casos"] == 4
    assert all(r["ausente"] is False for r in arvore["arvore"])

    api.salvar_origem_testes("PAR_2510", "fontes")
    arvore = api.get_selecao("PAR_2510")
    assert arvore["origem"] == "fontes"
    assert [r["rotina"] for r in arvore["arvore"]] == ["MATA143"]
    # E a seleção local continua guardada para quando voltar.
    assert api.listar_testes_locais("PAR_2510")["rotinas"][0]["selecionada"] is True


def test_trocar_a_pasta_zera_a_selecao_local(api, pasta_local, tmp_path):
    api.salvar_pasta_local("PAR_2510", str(pasta_local))
    api.salvar_selecao_local("PAR_2510", ["MATA010"])
    outra = tmp_path / "outra"
    outra.mkdir()
    api.salvar_pasta_local("PAR_2510", str(outra))
    assert api._importados.testes_locais("PAR_2510")["selecao"] == []
    # Mesma pasta de novo: não é troca, a seleção fica.
    api.salvar_pasta_local("PAR_2510", str(outra))
    api.salvar_selecao_local("PAR_2510", ["X"])
    api.salvar_pasta_local("PAR_2510", str(outra))
    assert api._importados.testes_locais("PAR_2510")["selecao"] == ["X"]


def test_teste_local_que_sumiu_aparece_ausente(api, pasta_local):
    api.salvar_pasta_local("PAR_2510", str(pasta_local))
    api.salvar_selecao_local("PAR_2510", ["MATA010"])
    api.salvar_origem_testes("PAR_2510", "local")
    (pasta_local / "MATA010TESTSUITE.py").unlink()
    arvore = api.get_selecao("PAR_2510")["arvore"]
    assert arvore[0]["rotina"] == "MATA010"
    assert arvore[0]["ausente"] is True


def test_pode_executar_olha_a_selecao_da_origem_ativa(api, pasta_local):
    api.salvar_pasta_local("PAR_2510", str(pasta_local))
    api.salvar_selecao("PAR_2510", ["MATA143"])
    api.salvar_origem_testes("PAR_2510", "local")
    r = api.pode_executar("PAR_2510")
    assert r["ok"] is False
    assert "rotina" in r["motivo"]
    api.salvar_selecao_local("PAR_2510", ["MATA010"])
    assert api.pode_executar("PAR_2510")["ok"] is True


# ── Validação e correção do config.json ─────────────────────

def test_validar_config_local_lista_as_divergencias(api, pasta_local):
    api.salvar_pasta_local("PAR_2510", str(pasta_local))
    r = api.validar_config_local("PAR_2510")
    assert r["ok"] is True
    assert r["caminho"] == str(pasta_local / "config.json")
    assert r["idioma"] == "es-ES"
    por_chave = {d["chave"]: d for d in r["divergencias"]}
    assert por_chave["Language"]["esperado"] == "es-ES"
    assert por_chave["POUILogin"]["encontrado"] == "false"
    assert por_chave["Browser"]["nivel"] == "aviso"


def test_validar_sem_config_falha_com_o_caminho(api, pasta_local):
    (pasta_local / "config.json").unlink()
    api.salvar_pasta_local("PAR_2510", str(pasta_local))
    r = api.validar_config_local("PAR_2510")
    assert r["ok"] is False
    assert "config.json" in r["erro"]
    assert r["caminho"].endswith("config.json")


def test_corrigir_grava_na_pasta_do_usuario(api, pasta_local):
    api.salvar_pasta_local("PAR_2510", str(pasta_local))
    r = api.corrigir_config_local("PAR_2510")
    assert r["ok"] is True
    assert r["alteradas"] == ["Language", "POUILogin", "Browser"]
    gravado = json.loads((pasta_local / "config.json").read_text(encoding="utf-8"))
    assert gravado["Language"] == "es-ES"
    assert gravado["POUILogin"] is True
    assert gravado["Browser"] == "Firefox"
    assert gravado["TimeOut"] == 45                 # o resto fica
    assert api.validar_config_local("PAR_2510")["divergencias"] == []


def test_idioma_esperado_cai_na_config_do_ambiente_sem_pais(api, bridge_falso):
    """País sem tradução conhecida: vale o idioma da aba Configurações."""
    bridge_falso.payload["bancos"][0]["localizacao"] = "xyz"
    bridge_falso.payload["localizacoes"]["xyz"] = "Atlântida"
    api._estado.atualizar()
    base = api.obter_configuracao("PAR_2510")["config"]
    api.salvar_configuracao("PAR_2510", {**base, "Language": "ru-RU"})
    assert api._idioma_esperado("PAR_2510") == "ru-RU"


# ── Execução em modo local ──────────────────────────────────

class _CorridaFalsa:
    """Captura o que a Api entregaria à `Execucao` sem rodar nada."""
    ultima = None

    def __init__(self, **kwargs):
        _CorridaFalsa.ultima = kwargs
        self.ativa = False

    def iniciar(self):
        pass


@pytest.fixture
def pronto_para_rodar(api, pasta_local, monkeypatch):
    api.salvar_pasta_local("PAR_2510", str(pasta_local))
    api.salvar_selecao_local("PAR_2510", ["MATA010"])
    api.salvar_origem_testes("PAR_2510", "local")
    monkeypatch.setattr(api_mod.execucao, "preparar_ambiente_python",
                        lambda fila: {"ok": True, "versao_tir": "2.14.5"})
    # Ambiente já no ar e de outro dono: nem para, nem sobe, nem DbAccess.
    monkeypatch.setattr(api, "_porta_no_ar", lambda porta: True)
    monkeypatch.setattr(api, "_subir_principal", lambda nome: {"ok": True})
    monkeypatch.setattr(api_mod.execucao, "Execucao", _CorridaFalsa)
    _CorridaFalsa.ultima = None
    return api


def test_executar_local_usa_o_config_da_pasta_como_esta(pronto_para_rodar):
    api = pronto_para_rodar
    r = api.executar_tir("PAR_2510")
    assert r["ok"] is True, r
    kwargs = _CorridaFalsa.ultima
    assert kwargs["config_literal"] is True
    # O config vai como está: POUILogin desligado, Chrome, pt-BR — o usuário
    # disse "Não" ao ajuste, e o arquivo dele é a verdade.
    assert kwargs["config"]["POUILogin"] is False
    assert kwargs["config"]["Browser"] == "Chrome"
    assert kwargs["config"]["TimeOut"] == 45
    assert [x["rotina"] for x in kwargs["rotinas"]] == ["MATA010"]
    assert kwargs["rotinas"][0]["suite"].endswith("MATA010TESTSUITE.py")


def test_executar_local_roda_em_paralelo_com_a_url_de_cada_instancia(
        pronto_para_rodar, monkeypatch):
    """Regra de 2026-09-18: local também roda em paralelo e divide casos.
    O config da pasta vai literal para cada instância — só a Url muda."""
    api = pronto_para_rodar
    api._prefs.salvar({"modo": "paralelo", "max_instancias": 2,
                       "dividir_casos": True})
    api.salvar_selecao_local("PAR_2510", ["MATA010", "FINA050"])
    monkeypatch.setattr(api, "_preparar_paralelos",
                        lambda nome: {"ok": True, "ambientes": ["PAR_2510", "PAR_2510_TIR1"]})
    r = api.executar_tir("PAR_2510")
    assert r["ok"] is True, r
    kwargs = _CorridaFalsa.ultima
    assert kwargs["instancias"] == 2
    assert kwargs["ambientes_por_slot"] == ["PAR_2510", "PAR_2510_TIR1"]
    assert kwargs["config_literal"] is True
    cfg = kwargs["config_por_ambiente"]["PAR_2510"]
    assert cfg["Url"] == "http://127.0.0.1:4321/"
    # Literal: POUILogin desligado e Chrome continuam; nada normalizado.
    assert cfg["POUILogin"] is False and cfg["Browser"] == "Chrome"
    assert cfg["Environment"] == "PAR_2510"
    # (A divisão em si depende do TESTCASE real — coberta em test_analise_casos.)


def test_executar_local_headless_vem_do_ambiente(pronto_para_rodar):
    """O "Sem tela" da configuração do ambiente é o que vale — a pasta tinha
    Headless ausente/false e o Firefox aparecia (2026-09-18)."""
    api = pronto_para_rodar
    api._importados.salvar_configuracao(
        "PAR_2510", {**api._config_do_ambiente("PAR_2510"), "Headless": True})
    r = api.executar_tir("PAR_2510")
    assert r["ok"] is True, r
    assert _CorridaFalsa.ultima["config"]["Headless"] is True
    assert _CorridaFalsa.ultima["config"]["TimeOut"] == 45     # o resto é da pasta


def test_executar_local_exige_url_no_config(pronto_para_rodar, pasta_local):
    api = pronto_para_rodar
    config = json.loads((pasta_local / "config.json").read_text(encoding="utf-8"))
    config["Url"] = "localhost:4321"
    (pasta_local / "config.json").write_text(json.dumps(config), encoding="utf-8")
    r = api.executar_tir("PAR_2510")
    assert r["ok"] is False
    assert "Url" in r["erro"]
    assert _CorridaFalsa.ultima is None


def test_executar_local_sem_config_falha(pronto_para_rodar, pasta_local):
    api = pronto_para_rodar
    (pasta_local / "config.json").unlink()
    r = api.executar_tir("PAR_2510")
    assert r["ok"] is False
    assert "config.json" in r["erro"]


def test_executar_pelos_fontes_continua_normalizando(pronto_para_rodar, tmp_path):
    api = pronto_para_rodar
    web = tmp_path / "fontes" / "Paraguai" / "SIGACOM"
    web.mkdir(parents=True)
    (web / "MATA143TESTSUITE.py").write_text(
        'suite.addTest(MATA143("test_MATA143_005"))\n', encoding="utf-8")
    (web / "MATA143TESTCASE.py").write_text("pass\n", encoding="utf-8")
    api._prefs.salvar({"raiz_testes": str(tmp_path / "fontes")})
    api.salvar_selecao("PAR_2510", ["MATA143"])
    api.salvar_origem_testes("PAR_2510", "fontes")
    r = api.executar_tir("PAR_2510")
    assert r["ok"] is True, r
    kwargs = _CorridaFalsa.ultima
    assert kwargs["config_literal"] is False
    assert kwargs["config"]["POUILogin"] is True


# ── Configuração pela tela, com origem local ────────────────

def test_obter_configuracao_local_mostra_o_config_da_pasta(pronto_para_rodar, pasta_local):
    api = pronto_para_rodar
    r = api.obter_configuracao("PAR_2510")
    assert r["ok"] is True
    assert r["local"]["caminho"].endswith("config.json")
    assert r["config"]["TimeOut"] == 45 and r["config"]["Browser"] == "Chrome"
    origens = {c["chave"]: c.get("origem") for c in r["campos"]}
    assert origens["Url"] == "ambiente" and origens["Headless"] == "ambiente"
    assert origens["TimeOut"] is None


def test_salvar_configuracao_local_divide_entre_pasta_e_ambiente(pronto_para_rodar, pasta_local):
    api = pronto_para_rodar
    atual = api.obter_configuracao("PAR_2510")["config"]
    r = api.salvar_configuracao("PAR_2510", {**atual, "TimeOut": 90, "Headless": True,
                                             "Browser": "Firefox"})
    assert r["ok"] is True, r
    gravado = json.loads((pasta_local / "config.json").read_text(encoding="utf-8"))
    assert gravado["TimeOut"] == 90 and gravado["Browser"] == "Firefox"
    assert gravado["POUILogin"] is False           # chave da pasta preservada
    assert api._config_do_ambiente("PAR_2510")["Headless"] is True


def test_configuracao_com_origem_fontes_nao_muda(api, pasta_local):
    api.salvar_pasta_local("PAR_2510", str(pasta_local))
    r = api.obter_configuracao("PAR_2510")
    assert "local" not in r
