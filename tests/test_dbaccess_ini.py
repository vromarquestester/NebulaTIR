"""Consolidação dos aliases do DbAccess (`services.dbaccess_ini`).

Este módulo nasceu de um defeito real: os três AppServer chegavam à tela de
login, preenchiam programa e ambiente, e travavam no usuário até o timeout.
Causa: o clone grava `[MSSQL/<banco>]` no `dbaccess.ini` do próprio ambiente,
mas só UM DbAccess roda — o do ambiente principal, que conhecia apenas o
próprio banco.
"""

import pytest

from services import dbaccess_ini

PRINCIPAL = """\
[GENERAL]
LogFile=dbaccess.log

[MSSQL/BASE_V1]
Server=127.0.0.1
Database=BASE_V1
User=sa
"""

CLONE = """\
[GENERAL]
LogFile=dbaccess.log

[MSSQL/BASE_V1]
Server=127.0.0.1
Database=BASE_V1
User=sa

[MSSQL/BASE_V1_TIR1]
Server=127.0.0.1
Database=BASE_V1_TIR1
User=sa
"""


@pytest.fixture
def inis(tmp_path):
    principal = tmp_path / "principal" / "dbaccess.ini"
    principal.parent.mkdir(parents=True)
    principal.write_text(PRINCIPAL, encoding="latin-1")

    clones = []
    for i in (1, 2, 3):
        c = tmp_path / f"clone{i}" / "dbaccess.ini"
        c.parent.mkdir(parents=True)
        c.write_text(CLONE.replace("TIR1", f"TIR{i}"), encoding="latin-1")
        clones.append(c)
    return principal, clones


def test_le_os_aliases(inis):
    principal, clones = inis
    assert dbaccess_ini.aliases(principal) == ["BASE_V1"]
    assert dbaccess_ini.aliases(clones[0]) == ["BASE_V1", "BASE_V1_TIR1"]


def test_traz_os_aliases_que_faltam(inis):
    principal, clones = inis
    r = dbaccess_ini.consolidar(principal, clones)
    assert r["mudou"] is True
    assert r["adicionados"] == ["MSSQL/BASE_V1_TIR1", "MSSQL/BASE_V1_TIR2",
                                "MSSQL/BASE_V1_TIR3"]
    assert dbaccess_ini.aliases(principal) == [
        "BASE_V1", "BASE_V1_TIR1", "BASE_V1_TIR2", "BASE_V1_TIR3"]


def test_nao_duplica_o_que_ja_existe(inis):
    principal, clones = inis
    dbaccess_ini.consolidar(principal, clones)
    r = dbaccess_ini.consolidar(principal, clones)
    assert r["mudou"] is False          # nada a fazer na segunda vez
    assert len(dbaccess_ini.aliases(principal)) == 4


def test_preserva_o_conteudo_original(inis):
    principal, clones = inis
    dbaccess_ini.consolidar(principal, clones)
    texto = principal.read_text(encoding="latin-1")
    assert "[GENERAL]" in texto
    assert "LogFile=dbaccess.log" in texto
    assert "Database=BASE_V1\n" in texto


def test_copia_o_conteudo_da_secao_nao_so_o_titulo(inis):
    principal, clones = inis
    dbaccess_ini.consolidar(principal, clones)
    texto = principal.read_text(encoding="latin-1")
    assert "Database=BASE_V1_TIR2" in texto
    assert texto.count("User=sa") == 4


def test_ignora_o_proprio_arquivo_como_origem(inis):
    principal, _ = inis
    r = dbaccess_ini.consolidar(principal, [principal])
    assert r["mudou"] is False


def test_origem_inexistente_nao_quebra(inis, tmp_path):
    principal, clones = inis
    r = dbaccess_ini.consolidar(principal, clones + [tmp_path / "nao-existe.ini"])
    assert r["ok"] is True
    assert len(r["adicionados"]) == 3


def test_destino_inexistente_avisa(tmp_path):
    r = dbaccess_ini.consolidar(tmp_path / "nao-existe.ini", [])
    assert r["ok"] is False


# ── Diagnóstico ─────────────────────────────────────────────

def test_faltando_aponta_o_banco_desconhecido(inis):
    """A mensagem que o usuário precisava ver em vez de um login pendurado."""
    principal, _ = inis
    ausentes = dbaccess_ini.faltando(principal, ["BASE_V1", "BASE_V1_TIR1"])
    assert ausentes == ["BASE_V1_TIR1"]


def test_faltando_vazio_apos_consolidar(inis):
    principal, clones = inis
    dbaccess_ini.consolidar(principal, clones)
    assert dbaccess_ini.faltando(
        principal, ["BASE_V1_TIR1", "BASE_V1_TIR2", "BASE_V1_TIR3"]) == []


def test_comparacao_ignora_caixa(inis):
    principal, clones = inis
    dbaccess_ini.consolidar(principal, clones)
    assert dbaccess_ini.faltando(principal, ["base_v1_tir1"]) == []


# ── Porta de escuta ─────────────────────────────────────────
# Sem a chave o DbAccess assume 7890, e um segundo processo na mesma máquina
# não sobe. A TOTVS documenta várias instâncias lado a lado, cada uma em outra
# porta, e diz que o `.ini` tem precedência sobre o `-pNNNN`.

def test_escreve_a_porta_na_secao_geral(tmp_path):
    ini = tmp_path / "dbaccess.ini"
    ini.write_text("[GENERAL]\nLicenseServer=x\nLicensePort=8009\n\n"
                   "[MSSQL/B1]\nuser=sa\n", encoding="latin-1")

    r = dbaccess_ini.aplicar_porta(ini, 7891)
    assert r["ok"] is True
    texto = ini.read_text(encoding="latin-1")
    assert "Port=7891" in texto
    # Não pode encostar na licença, que tem chave parecida.
    assert "LicensePort=8009" in texto
    assert "LicenseServer=x" in texto
    assert "[MSSQL/B1]" in texto


def test_substitui_a_porta_ja_existente(tmp_path):
    ini = tmp_path / "dbaccess.ini"
    ini.write_text("[GENERAL]\nPort=7890\n", encoding="latin-1")
    dbaccess_ini.aplicar_porta(ini, 7893)
    texto = ini.read_text(encoding="latin-1")
    assert "Port=7893" in texto
    assert "Port=7890" not in texto


def test_cria_a_secao_quando_falta(tmp_path):
    ini = tmp_path / "dbaccess.ini"
    ini.write_text("[MSSQL/B1]\nuser=sa\n", encoding="latin-1")
    dbaccess_ini.aplicar_porta(ini, 7892)
    texto = ini.read_text(encoding="latin-1")
    assert "[GENERAL]" in texto
    assert "Port=7892" in texto
    assert "user=sa" in texto


def test_secao_geral_no_fim_do_arquivo(tmp_path):
    ini = tmp_path / "dbaccess.ini"
    ini.write_text("[MSSQL/B1]\nuser=sa\n\n[GENERAL]\nLicensePort=8009\n",
                   encoding="latin-1")
    dbaccess_ini.aplicar_porta(ini, 7894)
    texto = ini.read_text(encoding="latin-1")
    assert texto.count("[GENERAL]") == 1
    assert "Port=7894" in texto


def test_le_a_porta_de_volta(tmp_path):
    ini = tmp_path / "dbaccess.ini"
    ini.write_text("[GENERAL]\nPort=7895\n", encoding="latin-1")
    assert dbaccess_ini.ler_porta(ini) == 7895


def test_sem_a_chave_a_porta_e_a_padrao_do_produto(tmp_path):
    ini = tmp_path / "dbaccess.ini"
    ini.write_text("[GENERAL]\nLicensePort=8009\n", encoding="latin-1")
    assert dbaccess_ini.ler_porta(ini) == 7890


def test_porta_zero_e_recusada(tmp_path):
    ini = tmp_path / "dbaccess.ini"
    ini.write_text("[GENERAL]\n", encoding="latin-1")
    assert dbaccess_ini.aplicar_porta(ini, 0)["ok"] is False


def test_arquivo_inexistente_avisa(tmp_path):
    r = dbaccess_ini.aplicar_porta(tmp_path / "nao-existe.ini", 7891)
    assert r["ok"] is False
    assert "não encontrado" in r["erro"]
