"""Configuração do TIR (`services.config_tir`).

O contrato aqui é com o framework, não com a tela: as chaves do `config.json`
têm que sair exatamente como o TIR espera, e as travas precisam valer no
backend — a UI pode ser contornada.

Chaves conferidas em https://totvs.github.io/tir/configjson.html
"""

from services import config_tir


def test_chaves_do_arquivo_sao_as_do_tir():
    """Rótulo em português é da UI; chave é do TIR e não se traduz."""
    assert list(config_tir.PADRAO) == [
        "Url", "Browser", "Environment", "Language", "User", "Password",
        "Headless", "POUILogin", "DebugLog", "LogFolder", "TimeOut",
        "CheckValue", "Coverage", "ChromeDriverAutoInstall",
    ]


def test_chromedriver_vem_ligado():
    """O Chrome se atualiza sozinho e o driver do TIR fica para trás: sem
    isto, todo caso falha no setUpClass com `session not created`."""
    assert config_tir.PADRAO["ChromeDriverAutoInstall"] is True
    assert config_tir.normalizar({})["ChromeDriverAutoInstall"] is True


def test_todo_campo_da_ui_existe_no_arquivo():
    assert {c["chave"] for c in config_tir.CAMPOS} == set(config_tir.PADRAO)


def test_campos_agrupados_em_blocos_contiguos():
    """A UI corta o formulário na troca de grupo — grupo repetido depois de
    outro criaria dois títulos iguais na mesma tela."""
    grupos = [c["grupo"] for c in config_tir.CAMPOS]
    assert all(grupos)
    blocos = [g for i, g in enumerate(grupos) if i == 0 or g != grupos[i - 1]]
    assert blocos == ["Ambiente", "Execução"]


def test_pasta_de_log_e_o_ultimo_campo():
    assert config_tir.CAMPOS[-1]["chave"] == "LogFolder"


def test_grade_fecha_em_linhas_cheias():
    """Simetria: cada grupo tem número par de campos de meia largura, senão
    sobra um campo solto numa linha pela metade."""
    for grupo in ("Ambiente", "Execução"):
        meia = [c for c in config_tir.CAMPOS
                if c["grupo"] == grupo and not c.get("largo")]
        assert len(meia) % 2 == 0, f"grupo {grupo} tem campo sobrando"


def test_campos_de_linha_inteira_fecham_o_grupo():
    """Os dois de largura total ficam no fim: caminho não pode ser truncado, e
    o switch do ChromeDriver carrega uma explicação longa. Estar no fim é o
    que mantém par o número de campos de meia largura."""
    largos = [c["chave"] for c in config_tir.CAMPOS if c.get("largo")]
    assert largos == ["ChromeDriverAutoInstall", "LogFolder"]
    assert [c["chave"] for c in config_tir.CAMPOS][-2:] == largos


def test_padrao_para_preenche_da_importacao():
    c = config_tir.padrao_para(url="http://127.0.0.1:4321/",
                               ambiente_ini="PAR_2510", navegador="Firefox")
    assert c["Url"] == "http://127.0.0.1:4321/"
    assert c["Environment"] == "PAR_2510"
    assert c["Browser"] == "Firefox"
    assert c["User"] == "ADMIN" and c["Password"] == "1234"
    assert c["TimeOut"] == 90
    assert c["LogFolder"] == config_tir.LOG_PADRAO


# ── Travas: valem no backend, não só na tela ────────────────

def test_debuglog_nao_pode_ser_desligado():
    """O NebulaTIR depende deste log; a UI trava e o backend confirma."""
    assert config_tir.normalizar({"DebugLog": False})["DebugLog"] is True
    assert config_tir.normalizar({"DebugLog": "off"})["DebugLog"] is True


def test_usuario_e_senha_sao_impostos():
    c = config_tir.normalizar({"User": "hacker", "Password": "outra"})
    assert c["User"] == "ADMIN"
    assert c["Password"] == "1234"


def test_chave_desconhecida_e_descartada():
    c = config_tir.normalizar({"Coverage": True, "ChaveInventada": "x"})
    assert "ChaveInventada" not in c
    assert c["Coverage"] is True


# ── Tipos ───────────────────────────────────────────────────

def test_switches_viram_booleano():
    c = config_tir.normalizar({"Headless": "true", "CheckValue": "on",
                               "Coverage": False})
    assert c["Headless"] is True
    assert c["CheckValue"] is True
    assert c["Coverage"] is False


def test_timeout_vira_inteiro_e_respeita_faixa():
    assert config_tir.normalizar({"TimeOut": "120"})["TimeOut"] == 120
    assert config_tir.normalizar({"TimeOut": 0})["TimeOut"] == 1
    assert config_tir.normalizar({"TimeOut": 99999})["TimeOut"] == 3600
    # Texto inválido mantém o que já estava, em vez de zerar o campo.
    base = config_tir.normalizar({"TimeOut": 45})
    assert config_tir.normalizar({"TimeOut": "abc"}, base=base)["TimeOut"] == 45


def test_idioma_invalido_volta_ao_padrao():
    assert config_tir.normalizar({"Language": "fr-FR"})["Language"] == "pt-BR"
    for idioma in config_tir.IDIOMAS:
        assert config_tir.normalizar({"Language": idioma})["Language"] == idioma


def test_pasta_de_log_e_obrigatoria():
    """Campo de digitação vazio não passa: chave vazia no config.json vira
    erro no meio da execução do TIR, longe da causa."""
    base = config_tir.padrao_para(url="http://x:1/", ambiente_ini="env",
                                  navegador="Chrome")
    c = config_tir.normalizar({"LogFolder": "   "}, base=base)
    assert c["LogFolder"] == ""
    assert "Pasta dos logs" in config_tir.validar(c)
    assert config_tir.MARCA_CASO in config_tir.LOG_PADRAO


def test_url_vazia_e_recusada():
    c = config_tir.padrao_para(url="", ambiente_ini="env", navegador="Chrome")
    assert "URL do ambiente" in config_tir.validar(c)


def test_campo_ausente_no_payload_mantem_o_que_havia():
    """Salvar sem mandar a chave não apaga o valor guardado."""
    base = config_tir.padrao_para(url="http://x:1/", ambiente_ini="env",
                                  navegador="Chrome")
    c = config_tir.normalizar({"TimeOut": 30}, base=base)
    assert c["Url"] == "http://x:1/"
    assert c["LogFolder"] == config_tir.LOG_PADRAO


def test_pasta_de_log_escolhida_e_mantida():
    c = config_tir.normalizar({"LogFolder": r"D:\logs\tir"})
    assert c["LogFolder"] == r"D:\logs\tir"


# ── Validação ───────────────────────────────────────────────

def test_validar_aceita_configuracao_completa():
    c = config_tir.padrao_para(url="http://localhost:4321",
                               ambiente_ini="environment", navegador="Chrome")
    assert config_tir.validar(c) == ""


def test_validar_recusa_url_e_ambiente_vazios():
    # Obrigatórios primeiro, na ordem da tela; formato da URL depois.
    c = config_tir.padrao_para(url="", ambiente_ini="", navegador="Chrome")
    assert "URL do ambiente" in config_tir.validar(c)
    c["Url"] = "http://localhost:4321"
    assert "ambiente" in config_tir.validar(c)
    c["Environment"] = "env"
    assert config_tir.validar(c) == ""
    c["Url"] = "localhost:4321"
    assert "http" in config_tir.validar(c)


def test_validar_exige_navegador():
    c = config_tir.padrao_para(url="http://x:1/", ambiente_ini="env", navegador="")
    assert "navegador" in config_tir.validar(c).lower()


# ── Idioma pelo país do ambiente ────────────────────────────
# Foi o que derrubou a primeira corrida de verdade num ambiente MEX: o padrão
# `pt-BR` era aplicado sem olhar a localização, o Protheus subia em português e
# o testcase — escrito em espanhol — falhava em todo `SetValue`, com
# "Element '¿Normal/Benef./Anticipo' not found!". Fora do NebulaTIR o mesmo
# teste passava, porque o config.json da pessoa tinha o idioma certo.

def test_ambiente_mexicano_nasce_em_espanhol():
    config = config_tir.padrao_para(url="http://127.0.0.1:4321/",
                                    ambiente_ini="environment",
                                    navegador="Firefox", pais="México")
    assert config["Language"] == "es-ES"


def test_aceita_sigla_e_nome_por_extenso():
    """`localizacao` vem como sigla; a tabela do Gerenciador traduz. Os dois
    caminhos chegam aqui, dependendo de quem pergunta."""
    assert config_tir.idioma_do_pais("mex") == "es-ES"
    assert config_tir.idioma_do_pais("Paraguai") == "es-ES"
    assert config_tir.idioma_do_pais("bra") == "pt-BR"
    assert config_tir.idioma_do_pais("Brasil") == "pt-BR"


def test_pais_desconhecido_cai_no_padrao():
    assert config_tir.idioma_do_pais("narnia") == ""
    config = config_tir.padrao_para(pais="narnia")
    assert config["Language"] == config_tir.PADRAO["Language"]


def test_so_os_idiomas_que_o_tir_conhece():
    """A lista sai de `get_language_pack` em `tir/technologies/core/language.py`.
    `in-US` estava lá por engano e não casa com nenhum ramo daquele if — o TIR
    caía no pacote padrão e procurava rótulo em português."""
    assert config_tir.IDIOMAS == ["pt-BR", "es-ES", "en-US", "ru-RU"]
    assert "in-US" not in config_tir.IDIOMAS
    for idioma in config_tir.IDIOMA_POR_PAIS.values():
        assert idioma in config_tir.IDIOMAS


def test_idioma_invalido_no_arquivo_volta_para_o_padrao():
    config = config_tir.normalizar({"Language": "in-US"})
    assert config["Language"] == config_tir.PADRAO["Language"]


# ── POUILogin ───────────────────────────────────────────────
# REGRA: sempre ligado. Os ambientes atendidos aqui sobem com a tela de entrada
# POUI; desligado, o TIR procura o campo de usuário do WebApp clássico, que não
# existe ali, e a execução não passa do login. Verificado nos dois modos, no
# ambiente real.

def test_poui_login_nasce_ligado():
    assert config_tir.PADRAO["POUILogin"] is True
    assert config_tir.padrao_para()["POUILogin"] is True


def test_poui_login_e_travado():
    """Travado no backend, não só desabilitado na tela: a UI pode ser
    contornada, `normalizar` não."""
    campo = next(c for c in config_tir.CAMPOS if c["chave"] == "POUILogin")
    assert campo["trava"] is True
    assert config_tir.normalizar({"POUILogin": False})["POUILogin"] is True
    assert config_tir.normalizar({"POUILogin": 0})["POUILogin"] is True


def test_arquivo_antigo_com_poui_desligado_volta_a_ligar():
    """Ambiente gravado antes desta regra é corrigido na leitura, sem
    migração: a trava vale em todo `normalizar`."""
    guardado = {**config_tir.PADRAO, "POUILogin": False}
    assert config_tir.normalizar(guardado)["POUILogin"] is True
