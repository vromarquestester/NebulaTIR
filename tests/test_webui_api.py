"""Ponte Python da interface (`webui.api.Api`).

Duas coisas sob teste: o gate (nenhuma ação passa com o Gerenciador offline,
mesmo que a UI chame assim mesmo) e a importação por nome, que não pode copiar
dados do ambiente para cá.
"""

import json
import time

import pytest

from services import config_tir
from services.gerenciador_client import EstadoGerenciador, GerenciadorClient
from webui import api as api_mod
from webui.api import Api


def _esperar(condicao, limite=10.0):
    """Espera uma thread da Api terminar (limpeza, exclusão)."""
    fim = time.monotonic() + limite
    while time.monotonic() < fim:
        if condicao():
            return True
        time.sleep(0.05)
    return False


@pytest.fixture
def raiz_testes(tmp_path):
    """Pasta de fontes de mentira, no formato real: País/MÓDULO/Scripts Web."""
    web = tmp_path / "fontes" / "Paraguai" / "SIGACOM" / "Scripts Web"
    web.mkdir(parents=True)
    (web / "MATA143TESTSUITE.py").write_text(
        'suite.addTest(MATA143("test_MATA143_005"))\n'
        'suite.addTest(MATA143("test_MATA143_011"))\n', encoding="utf-8")
    (web / "MATA143TESTCASE.py").write_text("class MATA143:\n    pass\n",
                                            encoding="utf-8")
    (web / "MATA994TESTSUITE.py").write_text(
        'suite.addTest(MATA994("test_MATA994_001"))\n', encoding="utf-8")
    (web / "MATA994TESTCASE.py").write_text("class MATA994:\n    pass\n",
                                            encoding="utf-8")
    return tmp_path / "fontes"


@pytest.fixture
def api(tmp_path, registro, raiz_testes):
    # `tolerancia_seg=0`: aqui se testa o gate de offline em si. A janela de
    # tolerância a indisponibilidade passageira tem testes próprios em
    # `test_gerenciador_client.py`.
    estado = EstadoGerenciador(client=GerenciadorClient(registro=registro),
                               tolerancia_seg=0)
    estado.atualizar()
    # `arquivo_instancias` no tmp_path também: sem ele o registro cai no
    # `config/instancias.json` do próprio projeto, e o teste passa a escrever
    # em arquivo de verdade e a herdar o que o teste anterior gravou.
    a = Api(arquivo_importados=tmp_path / "importados.json",
            iniciar_monitores=False, instalar_log=False, estado=estado,
            arquivo_preferencias=tmp_path / "preferencias.json",
            arquivo_instancias=tmp_path / "instancias.json",
            checar_porta=lambda p: True)
    a._prefs.salvar({"raiz_testes": str(raiz_testes)})
    return a


# ── Superfície exposta ao pywebview ────────────────────────
# Regressão herdada do Gerenciador: o pywebview varre `dir(js_api)` a cada load
# e desce recursivamente em todo atributo público NÃO-chamável. Estado da Api
# tem que ser todo privado, ou a janela trava minutos no boot.

def test_estado_da_api_e_todo_privado(api):
    publicos = [n for n in dir(api)
                if not n.startswith("_") and not callable(getattr(api, n))]
    assert publicos == []


# ── Gate ────────────────────────────────────────────────────

def test_status_reporta_link_online(api):
    s = api.get_status()
    assert s["link"] is True
    assert s["vpn"] is True
    assert s["config_valida"] is True


def test_status_offline_zera_vpn_e_sql(api, bridge_falso):
    bridge_falso.parar()
    api._estado.atualizar()
    s = api.get_status()
    assert s["link"] is False
    # Sem link o NebulaTIR não sabe da VPN — e não finge que sabe.
    assert s["vpn"] is None
    assert s["config_valida"] is None
    assert s["link_motivo"]


@pytest.mark.parametrize("acao,args", [
    ("listar_disponiveis", ()),
    ("importar_ambiente", ("PAR_2510",)),
    ("remover_importado", ("PAR_2510",)),
    ("detalhes_importado", ("PAR_2510",)),
    ("sincronizar", ()),
])
def test_nenhuma_acao_passa_com_gerenciador_offline(api, bridge_falso, acao, args):
    """O backend não confia na UI: revalida o gate em cada ação."""
    bridge_falso.parar()
    api._estado.atualizar()
    r = getattr(api, acao)(*args)
    assert r["ok"] is False
    assert r["erro"]


# ── Importação ──────────────────────────────────────────────

def test_listar_disponiveis_exclui_o_que_ja_foi_importado(api):
    assert {a["nome"] for a in api.listar_disponiveis()["ambientes"]} == {
        "PAR_2510", "BRA_2410"}
    api.importar_ambiente("PAR_2510")
    assert [a["nome"] for a in api.listar_disponiveis()["ambientes"]] == ["BRA_2410"]


def test_importar_ambiente_inexistente_falha(api):
    r = api.importar_ambiente("NAO_EXISTE")
    assert r["ok"] is False


def test_importar_duas_vezes_falha(api):
    assert api.importar_ambiente("PAR_2510")["ok"] is True
    assert api.importar_ambiente("PAR_2510")["ok"] is False


def test_persistencia_nao_copia_dados_do_gerenciador(api, tmp_path):
    """O Gerenciador continua sendo a fonte da verdade do ambiente.

    O que é gravado aqui é o nome e a configuração do TIR — dado do NebulaTIR.
    Nada de espelhar caminho de executável, banco ou estado de execução.
    """
    api.importar_ambiente("PAR_2510")
    dados = json.loads((tmp_path / "importados.json").read_text(encoding="utf-8"))
    item = dados["ambientes"][0]
    assert item["nome"] == "PAR_2510"
    # `idioma_manual` é metadado do NebulaTIR, não espelho do Gerenciador:
    # marca que a pessoa escolheu o idioma na tela e que a correção pelo país
    # do ambiente não deve mais mexer nele.
    assert set(item) == {"nome", "importado_em", "config", "selecao",
                         "idioma_manual"}
    assert set(item["config"]) == set(config_tir.PADRAO)
    for espelhado in ("appserver_exe", "dbaccess_exe", "nome_banco", "estado"):
        assert espelhado not in json.dumps(dados)


def test_porta_editada_no_gerenciador_aparece_na_hora(api, bridge_falso):
    api.importar_ambiente("PAR_2510")
    assert api.get_status()["ambientes"]["PAR_2510"]["port"] == "4321"

    bridge_falso.payload["bancos"][0]["port"] = "4999"
    api._estado.atualizar()
    assert api.get_status()["ambientes"]["PAR_2510"]["port"] == "4999"


def test_detalhes_traz_o_bloco_de_execucao(api):
    api.importar_ambiente("PAR_2510")
    d = api.detalhes_importado("PAR_2510")
    assert d["ok"] is True
    assert d["execucao"]["estado"] == "running"
    assert d["execucao"]["tem_appserver"] is True
    assert d["banco"]["port"] == "4321"


def test_detalhes_de_nao_importado_falha(api):
    assert api.detalhes_importado("BRA_2410")["ok"] is False


# ── Exclusão e sincronização ────────────────────────────────

def test_remover_nao_toca_no_gerenciador(api, bridge_falso):
    api.importar_ambiente("PAR_2510")
    assert api.remover_importado("PAR_2510")["ok"] is True
    assert api.get_status()["importados"] == []
    # O ambiente continua lá do outro lado.
    assert any(b["ambiente"] == "PAR_2510" for b in bridge_falso.payload["bancos"])


def test_sincronizar_aponta_orfaos(api, bridge_falso):
    api.importar_ambiente("PAR_2510")
    bridge_falso.payload["bancos"].pop(0)      # removido no Gerenciador
    r = api.sincronizar()
    assert r["ok"] is True
    assert r["orfaos"] == ["PAR_2510"]
    # A UI precisa marcar o item, então o status carrega a informação.
    assert api.get_status()["ambientes"]["PAR_2510"]["existe_no_gerenciador"] is False


def test_abrir_url_recusa_esquema_estranho(api):
    assert api.abrir_url("file:///C:/Windows/System32")["ok"] is False


# ── Configuração do TIR ─────────────────────────────────────

def test_importar_ja_traz_url_e_ambiente_preenchidos(api):
    """Os dois campos que o Gerenciador sabe e o usuário não deveria digitar."""
    api.importar_ambiente("PAR_2510")
    c = api.obter_configuracao("PAR_2510")["config"]
    assert c["Url"] == "http://127.0.0.1:4321/"
    assert c["Environment"] == "PAR_2510"      # veio do appserver.ini do bridge
    assert c["Browser"]                        # navegador da máquina


def test_configuracao_traz_o_esquema_do_formulario(api):
    api.importar_ambiente("PAR_2510")
    r = api.obter_configuracao("PAR_2510")
    # Ordem da TELA (`CAMPOS`), que não é a do arquivo (`PADRAO`).
    chaves = [c["chave"] for c in r["campos"]]
    assert chaves == [c["chave"] for c in config_tir.CAMPOS]
    assert set(chaves) == set(config_tir.PADRAO)
    navegador = next(c for c in r["campos"] if c["chave"] == "Browser")
    assert navegador["opcoes"]                 # lista da máquina, nunca vazia
    debug = next(c for c in r["campos"] if c["chave"] == "DebugLog")
    assert debug["trava"] is True


def test_salvar_configuracao_persiste_e_normaliza(api):
    api.importar_ambiente("PAR_2510")
    r = api.salvar_configuracao("PAR_2510", {
        "Url": "http://10.0.0.9:4321/", "Language": "es-ES",
        "TimeOut": "150", "Headless": False, "Coverage": True,
        "DebugLog": False,           # tentativa de desligar o log travado
        "User": "outro",             # tentativa de trocar o usuário fixo
    })
    assert r["ok"] is True
    c = api.obter_configuracao("PAR_2510")["config"]
    assert c["Url"] == "http://10.0.0.9:4321/"
    assert c["Language"] == "es-ES"
    assert c["TimeOut"] == 150
    assert c["Headless"] is False
    assert c["Coverage"] is True
    assert c["DebugLog"] is True     # trava imposta pelo backend
    assert c["User"] == "ADMIN"


def test_salvar_configuracao_invalida_e_recusada(api):
    api.importar_ambiente("PAR_2510")
    r = api.salvar_configuracao("PAR_2510", {"Url": "ftp://x/"})
    assert r["ok"] is False
    # E não gravou nada pela metade.
    assert api.obter_configuracao("PAR_2510")["config"]["Url"] == "http://127.0.0.1:4321/"


def test_configuracao_de_nao_importado_falha(api):
    assert api.obter_configuracao("BRA_2410")["ok"] is False
    assert api.salvar_configuracao("BRA_2410", {})["ok"] is False


def test_porta_mudou_no_gerenciador_vira_divergencia(api, bridge_falso):
    """Editar a porta lá não pode sobrescrever calado o que o usuário ajustou."""
    api.importar_ambiente("PAR_2510")
    bridge_falso.payload["bancos"][0]["port"] = "4999"
    api._estado.atualizar()

    r = api.obter_configuracao("PAR_2510")
    assert r["config"]["Url"] == "http://127.0.0.1:4321/"   # preservado
    divergencia = next(d for d in r["divergencias"] if d["chave"] == "Url")
    assert divergencia["gerenciador"] == "http://127.0.0.1:4999/"


def test_configuracao_funciona_com_gerenciador_offline(api, bridge_falso):
    """Config é dado local: não some junto com o link."""
    api.importar_ambiente("PAR_2510")
    bridge_falso.parar()
    api._estado.atualizar()
    r = api.obter_configuracao("PAR_2510")
    assert r["ok"] is True
    assert r["divergencias"] == []      # sem link, não há com o que comparar
    assert api.salvar_configuracao("PAR_2510", {"TimeOut": 30})["ok"] is True


def test_listar_navegadores_nunca_vem_vazio(api):
    assert api.listar_navegadores()["navegadores"]


# ── Catálogo de testes ──────────────────────────────────────

def test_lista_testes_do_pais_do_ambiente(api):
    """PAR_2510 tem localizacao `par`, que a tabela traduz para Paraguai."""
    api.importar_ambiente("PAR_2510")
    r = api.listar_testes("PAR_2510")
    assert r["ok"] is True
    assert r["pais"] == "Paraguai"
    assert [x["rotina"] for x in r["rotinas"]] == ["MATA143", "MATA994"]
    assert r["rotinas"][0]["casos"] == ["test_MATA143_005", "test_MATA143_011"]


def test_busca_filtra_a_lista(api):
    api.importar_ambiente("PAR_2510")
    assert [x["rotina"] for x in api.listar_testes("PAR_2510", "994")["rotinas"]] \
        == ["MATA994"]


def test_ambiente_de_pais_sem_pasta_avisa(api, bridge_falso):
    bridge_falso.payload["bancos"][1]["localizacao"] = "bra"
    api._estado.atualizar()
    api.importar_ambiente("BRA_2410")
    r = api.listar_testes("BRA_2410")
    assert r["ok"] is False
    assert "Brasil" in r["erro"]


def test_selecao_vira_arvore_de_casos(api):
    api.importar_ambiente("PAR_2510")
    r = api.salvar_selecao("PAR_2510", ["MATA143", "MATA994"])
    assert r["ok"] is True
    assert [x["rotina"] for x in r["arvore"]] == ["MATA143", "MATA994"]
    assert r["total_casos"] == 3


def test_selecao_persiste_e_marca_a_lista(api):
    api.importar_ambiente("PAR_2510")
    api.salvar_selecao("PAR_2510", ["MATA994"])
    marcadas = {x["rotina"] for x in api.listar_testes("PAR_2510")["rotinas"]
                if x["selecionada"]}
    assert marcadas == {"MATA994"}


def test_rotina_que_sumiu_do_disco_aparece_marcada(api, raiz_testes):
    """Some calada seria pior: o usuário executaria achando que rodou."""
    api.importar_ambiente("PAR_2510")
    api.salvar_selecao("PAR_2510", ["MATA143"])
    for arquivo in (raiz_testes / "Paraguai" / "SIGACOM" / "Scripts Web").glob("MATA143*"):
        arquivo.unlink()
    arvore = api.get_selecao("PAR_2510")["arvore"]
    assert arvore[0]["ausente"] is True
    assert arvore[0]["casos"] == []


# ── Portas ──────────────────────────────────────────────────

def test_sequencial_usa_uma_instancia_com_a_porta_do_ambiente(api):
    api.importar_ambiente("PAR_2510")
    r = api.plano_de_portas("PAR_2510")
    assert r["modo"] == "sequencial"
    assert r["slots"] == 1
    assert r["instancias"][0]["portas"]["webapp"] == 4321


def test_paralelo_abre_uma_instancia_por_limite(api):
    api.importar_ambiente("PAR_2510")
    api.salvar_preferencias({"modo": "paralelo", "max_instancias": 3})
    r = api.plano_de_portas("PAR_2510")
    assert r["slots"] == 3
    assert len(r["instancias"]) == 3
    webapps = [i["portas"]["webapp"] for i in r["instancias"]]
    assert len(set(webapps)) == 3


def test_listar_paralelos_completa_os_caminhos_que_faltam(api):
    """Instância criada antes desta versão não sabia onde ficou no disco."""
    api.importar_ambiente("PAR_2510")
    api._instancias.registrar(ambiente="PAR_2510_TIR1", origem="PAR_2510",
                              slot=1, banco="B1", portas={})

    item = api.listar_paralelos("PAR_2510")["instancias"][0]
    assert item["base_path"] == r"C:\TOTVS"
    assert item["temp_pai"] == r"C:\TOTVS\Downloads\PAR_2510\temp"
    assert item["workspace_banco"] == r"C:\TOTVS\Downloads\PAR_2510_TIR1"


def test_workspace_da_instancia_nova_fica_dentro_da_pasta_dela(api):
    """Nascendo em `NebulaInstancia`, o banco vai em `banco\\` dentro da
    própria pasta — apagar a pasta leva o MDF/LDF junto."""
    assert api._workspace_do_banco(
        r"C:\TOTVS\NebulaInstancia\A_TIR1", r"C:\TOTVS",
        r"C:\TOTVS\Downloads", "A_TIR1") == r"C:\TOTVS\NebulaInstancia\A_TIR1\banco"


def test_workspace_da_instancia_antiga_continua_no_downloads(api):
    """Layout anterior: `<pasta_destino>\\<amb>\\<data>`. A raiz é guardada; a
    pasta do dia é achada na hora de remover."""
    assert api._workspace_do_banco(
        r"C:\TOTVS\A_TIR1", r"C:\TOTVS", r"C:\TOTVS\Downloads",
        "A_TIR1") == r"C:\TOTVS\Downloads\A_TIR1"


def test_sem_gerenciador_nao_inventa_caminho(api, bridge_falso):
    """Apagar pasta por chute é pior que não ter o campo."""
    api.importar_ambiente("PAR_2510")
    api._instancias.registrar(ambiente="PAR_2510_TIR1", origem="PAR_2510",
                              slot=1, banco="B1", portas={})
    bridge_falso.parar()
    api._estado.atualizar()

    item = api.listar_paralelos("PAR_2510")["instancias"][0]
    assert "temp_pai" not in item
    assert "pasta" not in item


@pytest.fixture
def base_isolada(api, bridge_falso, tmp_path):
    """O inventário varre o disco de verdade: sem apontar a raiz para o
    `tmp_path`, o teste enxerga os ambientes da máquina."""
    bridge_falso.payload["base_path"] = str(tmp_path / "TOTVS")
    api._estado.atualizar()
    return api


def test_inventario_ve_instancia_de_qualquer_ambiente(base_isolada):
    """Instância cujo pai sumiu não pertence a lista nenhuma — e é a que ocupa
    disco à toa."""
    base_isolada._instancias.registrar(ambiente="SUMIU_TIR1", origem="SUMIU",
                                       slot=1, banco="B1", portas={})
    itens = base_isolada.inventario_instancias()["instancias"]
    assert [i["ambiente"] for i in itens] == ["SUMIU_TIR1"]
    assert itens[0]["situacao"] == "orfa"


def test_inventario_com_gerenciador_fechado_nao_acusa_orfa(base_isolada,
                                                           bridge_falso):
    api = base_isolada
    api._instancias.registrar(ambiente="SUMIU_TIR1", origem="SUMIU",
                              slot=1, banco="B1", portas={})
    bridge_falso.parar()
    api._estado.atualizar()

    r = api.inventario_instancias()
    assert r["online"] is False
    assert r["instancias"][0]["situacao"] == "indefinida"
    assert r["instancias"][0]["removivel"] is False


def test_limpeza_recusa_instancia_em_uso(base_isolada):
    """`sem_cadastro` com o pai vivo pode estar numa corrida — não é removível,
    e pedir a limpeza dela não pode passar."""
    api = base_isolada
    api._instancias.registrar(ambiente="PAR_2510_TIR1", origem="PAR_2510",
                              slot=1, banco="B1", portas={})
    r = api.limpar_instancias(["PAR_2510_TIR1"])
    assert r["ok"] is False
    assert "órfã" in r["erro"]


def test_limpeza_travada_com_gerenciador_fechado(base_isolada, bridge_falso):
    api = base_isolada
    api._instancias.registrar(ambiente="SUMIU_TIR1", origem="SUMIU",
                              slot=1, banco="B1", portas={})
    bridge_falso.parar()
    api._estado.atualizar()
    assert api.limpar_instancias(["SUMIU_TIR1"])["ok"] is False


def test_limpeza_roda_a_remocao_e_solta_o_registro(base_isolada, monkeypatch):
    """Órfã: o Gerenciador não a alcança, então quem remove é o NebulaTIR."""
    api = base_isolada
    api._instancias.registrar(ambiente="SUMIU_TIR1", origem="SUMIU",
                              slot=1, banco="B1", portas={})
    chamados = []
    monkeypatch.setattr(
        api_mod.limpeza, "remover",
        lambda item, servidor="", driver="": chamados.append(item["ambiente"])
        or {"ok": True, "ambiente": item["ambiente"], "passos": [], "erros": []})

    assert api.limpar_instancias(["SUMIU_TIR1"])["ok"] is True
    assert _esperar(lambda: not api.estado_limpeza()["ativa"])
    assert chamados == ["SUMIU_TIR1"]
    assert api.estado_limpeza()["removidos"] == ["SUMIU_TIR1"]
    assert api._instancias.contem("SUMIU_TIR1") is False


def test_limpeza_com_pendencia_reporta_e_nao_some_calada(base_isolada, monkeypatch):
    api = base_isolada
    api._instancias.registrar(ambiente="SUMIU_TIR1", origem="SUMIU",
                              slot=1, banco="B1", portas={})
    monkeypatch.setattr(
        api_mod.limpeza, "remover",
        lambda item, servidor="", driver="": {
            "ok": False, "ambiente": item["ambiente"], "passos": [],
            "erros": ["dsn: sem permissão"]})

    api.limpar_instancias(["SUMIU_TIR1"])
    assert _esperar(lambda: not api.estado_limpeza()["ativa"])
    estado = api.estado_limpeza()
    assert estado["removidos"] == []
    assert "sem permissão" in estado["erros"][0]["erro"]


def test_conexao_do_sql_vem_do_gerenciador(base_isolada):
    """Servidor e driver saem da conexão ativa; a senha é a constante de toda
    base — ela não atravessa o canal, por decisão."""
    assert base_isolada._dados_da_conexao() == {
        "servidor": "10.0.0.1", "driver": "ODBC Driver 17"}


def test_medir_instancia_desconhecida_e_erro_claro(base_isolada):
    r = base_isolada.medir_instancia("NAO_EXISTE_TIR9")
    assert r["ok"] is False
    assert "desconhecida" in r["erro"].lower()


def test_plano_marca_o_slot_que_ja_virou_instancia(api):
    """A tela tira o destaque amarelo de quem já foi criado."""
    api.importar_ambiente("PAR_2510")
    api.salvar_preferencias({"modo": "paralelo", "max_instancias": 3})
    api._instancias.registrar(ambiente="PAR_2510_TIR2", origem="PAR_2510",
                              slot=2, banco="B2", portas={})

    plano = api.plano_de_portas("PAR_2510")["instancias"]
    assert [i["criada"] for i in plano] == [False, True, False]
    assert plano[1]["ambiente"] == "PAR_2510_TIR2"


def test_instancia_de_outro_ambiente_nao_marca_o_plano(api):
    """O registro é global; o plano é de um ambiente só."""
    api.importar_ambiente("PAR_2510")
    api.salvar_preferencias({"modo": "paralelo", "max_instancias": 2})
    api._instancias.registrar(ambiente="BRA_2410_TIR1", origem="BRA_2410",
                              slot=1, banco="B1", portas={})

    plano = api.plano_de_portas("PAR_2510")["instancias"]
    assert [i["criada"] for i in plano] == [False, False]


def test_preferencias_sao_globais(api):
    """Não pertencem ao ambiente: o teto é da máquina."""
    api.importar_ambiente("PAR_2510")
    api.salvar_preferencias({"max_instancias": 8})
    assert api.get_preferencias()["preferencias"]["max_instancias"] == 8
    assert "max_instancias" not in api.obter_configuracao("PAR_2510")["config"]


# ── POUILogin: regra, não preferência ───────────────────────
# Os ambientes atendidos aqui sobem com a tela de entrada POUI. Desligado, o
# TIR procura o campo de usuário do WebApp clássico e a execução não passa do
# login. Verificado nos dois modos, no ambiente real.

def test_ambiente_importado_ja_vem_com_poui_login(api):
    api.importar_ambiente("PAR_2510")
    assert api.obter_configuracao("PAR_2510")["config"]["POUILogin"] is True


def test_arquivo_gravado_com_poui_desligado_e_corrigido_na_leitura(api):
    """Sem migração: a trava do `normalizar` vale em toda leitura."""
    api.importar_ambiente("PAR_2510")
    repo = api._importados
    repo._itens[0]["config"]["POUILogin"] = False
    repo._salvar()

    assert api.obter_configuracao("PAR_2510")["config"]["POUILogin"] is True


def test_salvar_com_poui_desligado_nao_desliga(api):
    """A UI pode ser contornada; a regra vale no backend."""
    api.importar_ambiente("PAR_2510")
    base = api.obter_configuracao("PAR_2510")["config"]
    r = api.salvar_configuracao("PAR_2510", {**base, "POUILogin": False})
    assert r["ok"] is True
    assert r["config"]["POUILogin"] is True

# ── Limpar resultados ───────────────────────────────────────

def test_limpar_execucao_zera_o_resultado(api):
    api._execucao = None
    assert api.limpar_execucao() == {"ok": True}
    assert api.estado_execucao()["rotinas"] == []


def test_limpar_recusa_com_corrida_em_andamento(api):
    """Soltar a referência com threads vivas faria o resultado sumir da tela
    enquanto o TIR continua rodando."""
    class Viva:
        ativa = True
        def instantaneo(self):
            return {"ativa": True, "rotinas": []}

    api._execucao = Viva()
    r = api.limpar_execucao()
    assert r["ok"] is False
    assert "andamento" in r["erro"]
    assert api._execucao is not None

# ── Estado efetivo do ambiente ──────────────────────────────
#
# O Gerenciador só responde `running` quando ELE é o pai do processo. Um
# AppServer subido pelo NebulaTIR ficava invisível para os dois lados: no ar,
# e ambos dizendo "parado".

def test_porta_que_responde_marca_o_ambiente_como_no_ar(api, monkeypatch):
    api.importar_ambiente("PAR_2510")
    monkeypatch.setattr(api, "_porta_no_ar", lambda porta: True)
    ambientes = api.get_status()["ambientes"]
    assert ambientes, "o teste precisa de ao menos um ambiente importado"
    for info in ambientes.values():
        assert info["estado"] == "running"
        assert info["porta_responde"] is True


def test_sem_porta_respondendo_o_estado_do_gerenciador_vale(api, monkeypatch):
    api.importar_ambiente("PAR_2510")
    monkeypatch.setattr(api, "_porta_no_ar", lambda porta: False)
    for info in api.get_status()["ambientes"].values():
        assert info["porta_responde"] is False
        # Sem porta respondendo, quem manda é o Gerenciador.
        assert info["fonte_estado"] == "gerenciador"


@pytest.mark.parametrize("valor", ["", None, "abc", 0, "0", "  "])
def test_porta_invalida_nao_explode(api, valor):
    """`port` vem do Gerenciador e pode chegar vazio ou sujo."""
    assert api._porta_no_ar(valor) is False
