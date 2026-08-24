"""Escrita das portas no appserver.ini (`services.appserver_ini`).

Este módulo existe por causa de um defeito real: o `clonar_ambiente` do
Gerenciador ajusta só a porta do `[WEBAPP]`, e os três clones saíam com
`[TCP] 8881` e `[HTTPREST] 8080` idênticos. Só o primeiro AppServer subia.
"""

from pathlib import Path

import pytest

from services import appserver_ini

# Recorte fiel do arquivo real, inclusive a caixa mista das chaves e a seção
# [LICENSECLIENT], cuja porta NÃO pode ser tocada.
INI = """\
[General]
app_environment=environment

[environment]
SourcePath=C:\\TOTVS\\X\\apo
DBPort=7890
SQLitePort=5056

[Drivers]
Active=TCP

[TCP]
TYPE=TCPIP
Port=8881

[LICENSECLIENT]
server=licensedev.totvs.com.br
port=8009

[WEBAPP]
port=4321

[WEBAGENT]
VERSION=1.0.25
Port=21021

[HTTPREST]
Port=8080
Security=1
"""


@pytest.fixture
def ini(tmp_path):
    caminho = tmp_path / "appserver.ini"
    caminho.write_text(INI, encoding="latin-1")
    return caminho


PLANO = {"webapp": 4323, "tcp": 8883, "httprest": 8082,
         "webagent": 21023, "sqlite": 5058}


def test_aplica_todas_as_portas_do_plano(ini):
    r = appserver_ini.aplicar_portas(ini, PLANO)
    assert r["ok"] is True
    # `dbaccess` não está no plano deste teste, então o DBPort do arquivo
    # continua onde estava — a leitura devolve o valor original.
    assert appserver_ini.ler_portas(ini) == {
        "webapp": "4323", "tcp": "8883", "httprest": "8082",
        "webagent": "21023", "sqlite": "5058", "dbaccess": "7890"}


def test_licenseclient_fica_intacto(ini):
    """A licença é compartilhada de propósito: 8009 nunca entra na alocação.

    O `DBPort` já foi imutável junto com ela, quando um DbAccess só atendia
    todos os bancos. Deixou de ser: sem `dbaccess` no plano ele fica onde
    está, e com `dbaccess` no plano ele aponta o processo daquela instância.
    """
    appserver_ini.aplicar_portas(ini, PLANO)
    texto = ini.read_text(encoding="latin-1")
    assert "port=8009" in texto
    assert "DBPort=7890" in texto        # não estava no plano


def test_preserva_o_resto_do_arquivo(ini):
    antes = ini.read_text(encoding="latin-1").splitlines()
    appserver_ini.aplicar_portas(ini, PLANO)
    depois = ini.read_text(encoding="latin-1").splitlines()
    assert len(antes) == len(depois)
    # Só as linhas de porta mudaram.
    mudadas = [d for a, d in zip(antes, depois) if a != d]
    assert len(mudadas) == 5
    assert "SourcePath=C:\\TOTVS\\X\\apo" in depois


def test_mantem_a_caixa_original_da_chave(ini):
    """`Port` no TCP e `port` no WEBAPP: reescrever com outra caixa poderia
    quebrar leitura de quem compara literal."""
    appserver_ini.aplicar_portas(ini, PLANO)
    texto = ini.read_text(encoding="latin-1")
    assert "Port=8883" in texto      # [TCP]
    assert "port=4323" in texto      # [WEBAPP]


def test_conferir_aponta_divergencia(ini):
    assert appserver_ini.conferir(ini, PLANO)       # antes de aplicar, diverge
    appserver_ini.aplicar_portas(ini, PLANO)
    assert appserver_ini.conferir(ini, PLANO) == []


def test_arquivo_inexistente_avisa(tmp_path):
    r = appserver_ini.aplicar_portas(tmp_path / "nao-existe.ini", PLANO)
    assert r["ok"] is False
    assert "não encontrado" in r["erro"]


def test_secao_ausente_e_reportada(tmp_path):
    caminho = tmp_path / "appserver.ini"
    caminho.write_text("[WEBAPP]\nport=4321\n", encoding="latin-1")
    r = appserver_ini.aplicar_portas(caminho, PLANO)
    assert r["ok"] is True
    assert set(r["faltando"]) == {"tcp", "httprest", "webagent"}


def test_caminho_do_ini_sai_do_executavel():
    exe = r"C:\TOTVS\AMB\Protheus\bin\appserver\appserver.exe"
    assert appserver_ini.caminho_do_ini(exe) == \
        Path(r"C:\TOTVS\AMB\Protheus\bin\appserver\appserver.ini")


def test_instancias_diferentes_ficam_com_portas_diferentes(tmp_path):
    """O ponto do módulo: dois clones não podem sair iguais."""
    portas_por_slot = [
        {"webapp": 4321, "tcp": 8881, "httprest": 8080, "webagent": 21021},
        {"webapp": 4322, "tcp": 8882, "httprest": 8081, "webagent": 21022},
    ]
    lidas = []
    for i, portas in enumerate(portas_por_slot):
        caminho = tmp_path / f"ini{i}.ini"
        caminho.write_text(INI, encoding="latin-1")
        appserver_ini.aplicar_portas(caminho, portas)
        lidas.append(appserver_ini.ler_portas(caminho))
    assert lidas[0]["tcp"] != lidas[1]["tcp"]
    assert lidas[0]["httprest"] != lidas[1]["httprest"]
    assert lidas[0]["webagent"] != lidas[1]["webagent"]


# ── WebMonitor desligado nos clones ─────────────────────────
# Ele abre uma porta fixa (3434) em todo AppServer que sobe, fora do plano de
# portas. Com três clones, dois falham com `error 10048` e enchem o console de
# ruído. O TIR fala com o WebApp, não com o monitor.

def test_desliga_o_webmonitor_da_secao_existente(tmp_path):
    ini = tmp_path / "appserver.ini"
    ini.write_text("[WEBMONITOR]\nSCPingEnabled=0\nENABLE=1\n", encoding="latin-1")

    assert appserver_ini.desativar_webmonitor(ini)["ok"] is True
    texto = ini.read_text(encoding="latin-1")
    assert "ENABLE=0" in texto
    assert "ENABLE=1" not in texto
    assert "SCPingEnabled=0" in texto      # o resto da seção fica


def test_acrescenta_a_chave_quando_a_secao_nao_tem(tmp_path):
    ini = tmp_path / "appserver.ini"
    ini.write_text("[WEBMONITOR]\nSCPingEnabled=0\n\n[TCP]\nPort=8881\n",
                   encoding="latin-1")

    appserver_ini.desativar_webmonitor(ini)
    texto = ini.read_text(encoding="latin-1")
    assert "ENABLE=0" in texto
    # A chave entra na seção certa, não no meio do [TCP].
    antes_do_tcp = texto.split("[TCP]")[0]
    assert "ENABLE=0" in antes_do_tcp
    assert "Port=8881" in texto


def test_cria_a_secao_quando_ela_nao_existe(tmp_path):
    ini = tmp_path / "appserver.ini"
    ini.write_text("[TCP]\nPort=8881\n", encoding="latin-1")

    appserver_ini.desativar_webmonitor(ini)
    texto = ini.read_text(encoding="latin-1")
    assert "[WEBMONITOR]" in texto
    assert "ENABLE=0" in texto
    assert "Port=8881" in texto


def test_secao_no_fim_do_arquivo(tmp_path):
    """Sem seção seguinte, o fim da seção é o fim do arquivo."""
    ini = tmp_path / "appserver.ini"
    ini.write_text("[TCP]\nPort=8881\n\n[WEBMONITOR]\nSCPingEnabled=0\n",
                   encoding="latin-1")

    appserver_ini.desativar_webmonitor(ini)
    texto = ini.read_text(encoding="latin-1")
    assert texto.count("[WEBMONITOR]") == 1
    assert "ENABLE=0" in texto


def test_arquivo_inexistente_devolve_erro(tmp_path):
    r = appserver_ini.desativar_webmonitor(tmp_path / "nao-existe.ini")
    assert r["ok"] is False


# ── DBPort ──────────────────────────────────────────────────
# Aponta o DbAccess daquela instância. Era fixo em 7890 enquanto um processo
# só atendia todas; com um DbAccess por instância, cada AppServer precisa
# achar o seu.

def test_escreve_o_dbport_da_instancia(tmp_path):
    ini = tmp_path / "appserver.ini"
    ini.write_text("[environment]\nDBServer=127.0.0.1\nDBPort=7890\n"
                   "SQLitePort=5056\n\n[TCP]\nPort=8881\n", encoding="latin-1")

    r = appserver_ini.aplicar_portas(ini, {"dbaccess": 7891, "tcp": 8882})
    assert r["ok"] is True
    texto = ini.read_text(encoding="latin-1")
    assert "DBPort=7891" in texto
    assert "Port=8882" in texto
    # DBServer continua local: só a porta muda.
    assert "DBServer=127.0.0.1" in texto


def test_dbport_e_sqliteport_convivem(tmp_path):
    """As duas moram na seção do ambiente, cujo nome muda por instalação."""
    ini = tmp_path / "appserver.ini"
    ini.write_text("[qualquer_nome]\nDBPort=7890\nSQLitePort=5056\n",
                   encoding="latin-1")

    appserver_ini.aplicar_portas(ini, {"dbaccess": 7892, "sqlite": 5058})
    texto = ini.read_text(encoding="latin-1")
    assert "DBPort=7892" in texto
    assert "SQLitePort=5058" in texto


def test_sem_dbaccess_no_plano_o_dbport_nao_e_tocado(tmp_path):
    """Modo compartilhado: a 7890 do template continua valendo."""
    ini = tmp_path / "appserver.ini"
    ini.write_text("[environment]\nDBPort=7890\n", encoding="latin-1")
    appserver_ini.aplicar_portas(ini, {"tcp": 8882})
    assert "DBPort=7890" in ini.read_text(encoding="latin-1")


def test_le_o_dbport_de_volta(tmp_path):
    ini = tmp_path / "appserver.ini"
    ini.write_text("[environment]\nDBPort=7893\n", encoding="latin-1")
    assert appserver_ini.ler_portas(ini)["dbaccess"] == "7893"


# ── SpecialKey ──────────────────────────────────────────────
# É a identidade do ambiente para o semáforo e para o controle de RPO. O clone
# nasce com a chave do original; com ela igual, o Protheus trata os clones como
# o MESMO ambiente, e como cada um tem o RPO no próprio caminho, o segundo a
# entrar leva "Identificados acessos utilizando RPO divergentes" e não abre.
# Sem isto o paralelismo não existe, por mais separados que estejam porta,
# banco e DbAccess.

def test_clone_ganha_specialkey_propria(tmp_path):
    ini = tmp_path / "appserver.ini"
    ini.write_text("[environment]\nSpecialKey=PAR25xx\nDBAlias=B1\n",
                   encoding="latin-1")

    r = appserver_ini.aplicar_specialkey(ini, "T2")
    assert r["ok"] is True
    assert r["mudou"] is True
    assert appserver_ini.ler_specialkey(ini) == "PAR25xx_T2"
    assert "DBAlias=B1" in ini.read_text(encoding="latin-1")


def test_instancias_diferentes_recebem_chaves_diferentes(tmp_path):
    chaves = []
    for slot in (1, 2, 3):
        ini = tmp_path / f"appserver{slot}.ini"
        ini.write_text("[environment]\nSpecialKey=PAR25xx\n", encoding="latin-1")
        appserver_ini.aplicar_specialkey(ini, f"T{slot}")
        chaves.append(appserver_ini.ler_specialkey(ini))
    assert len(set(chaves)) == 3


def test_reaplicar_nao_empilha_sufixo(tmp_path):
    """Subir a mesma instância duas vezes não pode virar PAR25xx_T2_T2."""
    ini = tmp_path / "appserver.ini"
    ini.write_text("[environment]\nSpecialKey=PAR25xx\n", encoding="latin-1")

    appserver_ini.aplicar_specialkey(ini, "T2")
    r = appserver_ini.aplicar_specialkey(ini, "T2")
    assert r["mudou"] is False
    assert appserver_ini.ler_specialkey(ini) == "PAR25xx_T2"


def test_chave_longa_e_truncada(tmp_path):
    """A chave entra em funções de semáforo; não pode crescer sem limite."""
    ini = tmp_path / "appserver.ini"
    ini.write_text("[environment]\nSpecialKey=" + "X" * 60 + "\n",
                   encoding="latin-1")
    appserver_ini.aplicar_specialkey(ini, "T1")
    chave = appserver_ini.ler_specialkey(ini)
    assert len(chave) <= 20
    assert chave.endswith("_T1")


def test_arquivo_sem_specialkey_nao_inventa_uma(tmp_path):
    """Ambiente que não usa a chave não passa a usar por nossa conta."""
    ini = tmp_path / "appserver.ini"
    ini.write_text("[environment]\nDBAlias=B1\n", encoding="latin-1")
    r = appserver_ini.aplicar_specialkey(ini, "T1")
    assert r["ok"] is True
    assert r["mudou"] is False
    assert "SpecialKey" not in ini.read_text(encoding="latin-1")


def test_sufixo_vazio_e_recusado(tmp_path):
    ini = tmp_path / "appserver.ini"
    ini.write_text("[environment]\nSpecialKey=PAR25xx\n", encoding="latin-1")
    assert appserver_ini.aplicar_specialkey(ini, "")["ok"] is False
