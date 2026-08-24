"""Preparo da pasta de execução (`services.preparacao`).

Copiar em vez de rodar no lugar é decisão de projeto: os fontes vão para code
review e não podem receber `config.json` nem pasta de log ao lado.
"""

import json
from pathlib import Path

import pytest

from services import config_tir, preparacao


@pytest.fixture
def programa(tmp_path, monkeypatch):
    """Finge a pasta do executável e os recursos empacotados."""
    prog = tmp_path / "programa"
    prog.mkdir()
    anexos = tmp_path / "anexos"
    (anexos / "services" / "tir" / "assets" / "fonts").mkdir(parents=True)
    for nome in preparacao.ANEXOS:
        (anexos / "services" / "tir" / nome).write_text(f"# {nome}\n",
                                                        encoding="utf-8")
    for nome in preparacao.ANEXOS_ASSETS:
        (anexos / "services" / "tir" / nome).write_bytes(b"fonte")

    monkeypatch.setattr(preparacao, "pasta_do_programa", lambda: prog)
    monkeypatch.setattr(
        preparacao, "recurso",
        lambda *partes: (anexos.joinpath(*partes[1:])
                         if partes and partes[0] == "src"
                         else anexos.joinpath(*partes)))
    return prog


@pytest.fixture
def rotina(tmp_path):
    """Rotina como o catálogo devolve, com os fontes em disco."""
    fontes = tmp_path / "fontes" / "Mexico" / "SIGACOM" / "Scripts Web"
    fontes.mkdir(parents=True)
    (fontes / "COMA222TESTSUITE.py").write_text("# suite\n", encoding="utf-8")
    (fontes / "COMA222TESTCASE.py").write_text("# case\n", encoding="utf-8")
    return {"rotina": "COMA222", "modulo": "SIGACOM",
            "suite": str(fontes / "COMA222TESTSUITE.py"),
            "case": str(fontes / "COMA222TESTCASE.py")}


def _config():
    return config_tir.padrao_para(url="http://127.0.0.1:4321/",
                                  ambiente_ini="environment",
                                  navegador="Chrome")


# ── Estrutura pedida ────────────────────────────────────────

def test_cria_a_arvore_tests_ambiente_rotina(programa, rotina):
    r = preparacao.preparar_rotina("Protheus_BOL_2510", rotina, _config())
    assert r["ok"] is True
    esperado = programa / "tests" / "Protheus_BOL_2510" / "COMA222"
    assert Path(r["pasta"]) == esperado
    assert (esperado / "COMA222TESTSUITE.py").is_file()
    assert (esperado / "COMA222TESTCASE.py").is_file()
    assert (esperado / "config.json").is_file()
    assert (esperado / "log").is_dir()


def test_logfolder_aponta_para_a_pasta_da_rotina(programa, rotina):
    """`\\tests\\<ambiente>\\<rotina>\\log`, separando o log de cada rotina."""
    r = preparacao.preparar_rotina("Protheus_BOL_2510", rotina, _config())
    config = json.loads(Path(r["config"]).read_text(encoding="utf-8"))
    assert config["LogFolder"].endswith(str(Path("Protheus_BOL_2510") /
                                            "COMA222" / "log"))
    assert Path(config["LogFolder"]).is_dir()


def test_config_gerado_mantem_as_chaves_do_tir(programa, rotina):
    r = preparacao.preparar_rotina("AMB", rotina, _config())
    config = json.loads(Path(r["config"]).read_text(encoding="utf-8"))
    assert list(config) == list(config_tir.PADRAO)
    assert config["Url"] == "http://127.0.0.1:4321/"
    assert config["DebugLog"] is True


def test_lancador_e_fontes_acompanham(programa, rotina):
    """O lançador roda com a pasta do teste como diretório atual."""
    r = preparacao.preparar_rotina("AMB", rotina, _config())
    pasta = Path(r["pasta"])
    for nome in preparacao.ANEXOS:
        assert (pasta / nome).is_file()
    for nome in preparacao.ANEXOS_ASSETS:
        assert (pasta / nome).is_file()
    assert r["faltando"] == []


def test_nenhum_executavel_na_pasta_do_teste(programa, rotina):
    """O LogNebula virou código incorporado: copiar 20 MB de .exe por rotina
    era desperdício e sujava a pasta de teste."""
    preparacao.preparar_rotina("AMB", rotina, _config())
    pasta = Path(programa) / "tests" / "AMB" / "COMA222"
    assert list(pasta.rglob("*.exe")) == []


def test_os_fontes_originais_nao_sao_tocados(programa, rotina):
    preparacao.preparar_rotina("AMB", rotina, _config())
    origem = Path(rotina["suite"]).parent
    assert sorted(p.name for p in origem.iterdir()) == \
        ["COMA222TESTCASE.py", "COMA222TESTSUITE.py"]


def test_rotinas_diferentes_nao_dividem_pasta(programa, rotina, tmp_path):
    """Duas instâncias escrevendo o mesmo config.json disputariam o arquivo."""
    outra = dict(rotina, rotina="MATA101N")
    preparacao.preparar_rotina("AMB", rotina, _config())
    preparacao.preparar_rotina("AMB", outra, _config())
    base = programa / "tests" / "AMB"
    assert {p.name for p in base.iterdir()} == {"COMA222", "MATA101N"}
    for nome in ("COMA222", "MATA101N"):
        config = json.loads((base / nome / "config.json").read_text(encoding="utf-8"))
        assert config["LogFolder"].endswith(str(Path(nome) / "log"))


# ── Falhas ──────────────────────────────────────────────────

def test_sem_testcase_para_antes_de_executar(programa, rotina):
    Path(rotina["case"]).unlink()
    r = preparacao.preparar_rotina("AMB", rotina, _config())
    assert r["ok"] is False
    assert "TESTCASE" in r["erro"]


def test_erro_numa_rotina_nao_impede_as_outras(programa, rotina):
    quebrada = dict(rotina, rotina="SUMIU", suite=str(Path(rotina["suite"]).parent / "X.py"))
    r = preparacao.preparar_selecao("AMB", [rotina, quebrada], _config())
    assert r["ok"] is True
    assert [x["rotina"] for x in r["prontas"]] == ["COMA222"]
    assert [x["rotina"] for x in r["erros"]] == ["SUMIU"]


def test_preparar_de_novo_sobrescreve_sem_duplicar(programa, rotina):
    preparacao.preparar_rotina("AMB", rotina, _config())
    r = preparacao.preparar_rotina("AMB", rotina, _config())
    assert r["ok"] is True
    pasta = Path(r["pasta"])
    assert len(list(pasta.glob("*TESTSUITE.py"))) == 1


def test_limpar_remove_so_o_ambiente_pedido(programa, rotina):
    preparacao.preparar_rotina("AMB1", rotina, _config())
    preparacao.preparar_rotina("AMB2", rotina, _config())
    preparacao.limpar_ambiente("AMB1")
    assert not (programa / "tests" / "AMB1").exists()
    assert (programa / "tests" / "AMB2").is_dir()
