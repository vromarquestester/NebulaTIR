"""Catálogo de testes (`services.catalogo_testes`).

A estrutura de pastas é montada no `tmp_path`, espelhando o que existe em
`C:\\Dev\\Fontes\\Testes\\Automação Protheus` — inclusive as armadilhas reais:
país com acento na tabela e sem acento na pasta, módulo sem `Scripts Web`, e
TESTCASE que não declara os métodos.
"""

from pathlib import Path

import pytest

from services import catalogo_testes as cat

SUITE_COMA222 = '''\
from COMA222TESTCASE import COMA222
import unittest

suite = unittest.TestSuite()
suite.addTest(COMA222("test_COMA222_CT006"))
suite.addTest(COMA222("test_COMA222_CT007"))
suite.addTest(COMA222("test_COMA222_CT011"))
runner = unittest.TextTestRunner(verbosity=2)
runner.run(suite)
'''


def _rotina(web: Path, nome: str, suite_texto: str, com_case: bool = True):
    web.mkdir(parents=True, exist_ok=True)
    (web / f"{nome}TESTSUITE.py").write_text(suite_texto, encoding="utf-8")
    if com_case:
        (web / f"{nome}TESTCASE.py").write_text(
            f"class {nome}:\n    pass\n", encoding="utf-8")


@pytest.fixture
def raiz(tmp_path):
    r = tmp_path / "Automação Protheus"
    # Pasta sem acento, como no disco de verdade.
    _rotina(r / "Mexico" / "SIGACOM" / cat.PASTA_WEB, "COMA222", SUITE_COMA222)
    _rotina(r / "Mexico" / "SIGAFIN" / cat.PASTA_WEB, "FINA003",
            'suite.addTest(FINA003("test_FINA003_CT001"))\n')
    # Módulo sem nenhum TESTSUITE: fica fora, tenha a pasta que tiver.
    (r / "Mexico" / "SIGAATF" / "Scripts AdvPR").mkdir(parents=True)
    (r / "Mexico" / "SIGAATF" / "Scripts AdvPR" / "ATFA010.PRW").write_text(
        "// AdvPL, não é teste web\n", encoding="utf-8")
    # Outro país, para o filtro não vazar.
    _rotina(r / "Paraguai" / "SIGACOM" / cat.PASTA_WEB, "COMA010",
            'suite.addTest(COMA010("test_COMA010_CT001"))\n')
    # Não é país; fica fora do catálogo por decisão do usuário.
    _rotina(r / "PAD - Todos Paises" / "SIGACOM" / cat.PASTA_WEB, "PADX999",
            'suite.addTest(PADX999("test_PADX999_CT001"))\n')
    return r


# ── Descoberta ──────────────────────────────────────────────

def test_lista_rotinas_do_pais(raiz):
    r = cat.escanear_pais(raiz, "México")
    assert r["ok"] is True
    assert [x["rotina"] for x in r["rotinas"]] == ["COMA222", "FINA003"]
    assert r["rotinas"][0]["modulo"] == "SIGACOM"


def test_pais_com_acento_casa_com_pasta_sem_acento(raiz):
    """A tabela do Gerenciador tem “Colômbia”; a pasta no disco é “Colombia”."""
    (raiz / "Colombia" / "SIGACOM" / cat.PASTA_WEB).mkdir(parents=True)
    assert cat.pasta_do_pais(raiz, "Colômbia").name == "Colombia"
    assert cat.pasta_do_pais(raiz, "MEXICO").name == "Mexico"


def test_modulo_sem_nenhum_suite_fica_de_fora(raiz):
    modulos = {x["modulo"] for x in cat.escanear_pais(raiz, "Mexico")["rotinas"]}
    assert "SIGAATF" not in modulos


# ── Pastas fora do padrão “Scripts Web” ─────────────────────
# O disco real guarda teste em `Scripts TIR`, em `Scripts Web\Suite`, em
# `Suites`, `Suit`, `Prestadores`… Exigir uma pasta específica escondia a
# maior parte deles. O par de arquivos é que manda.

def test_scripts_tir_entra_no_catalogo(tmp_path):
    _rotina(tmp_path / "Brasil" / "SIGAPLS" / "Scripts TIR", "PLSA010",
            'suite.addTest(PLSA010("test_PLSA010_CT001"))\n')
    rotinas = cat.escanear_pais(tmp_path, "Brasil")["rotinas"]
    assert [(r["rotina"], r["modulo"]) for r in rotinas] == [("PLSA010", "SIGAPLS")]


def test_suite_em_subpasta_funda_entra(tmp_path):
    """`SIGAGFE\\Scripts Web\\Suite\\GFEA010TESTSUITE.py`: dois níveis."""
    _rotina(tmp_path / "Brasil" / "SIGAGFE" / cat.PASTA_WEB / "Suite", "GFEA010",
            'suite.addTest(GFEA010("test_GFEA010_CT001"))\n')
    rotina = cat.escanear_pais(tmp_path, "Brasil")["rotinas"][0]
    assert rotina["modulo"] == "SIGAGFE"
    assert rotina["subpasta"] == str(Path(cat.PASTA_WEB) / "Suite")
    assert rotina["casos"] == ["test_GFEA010_CT001"]


def test_caixa_do_nome_do_arquivo_nao_importa(tmp_path):
    """`TestSuite.py` e `TESTSUITE.PY` existem no disco tanto quanto o padrão."""
    pasta = tmp_path / "Brasil" / "SIGAFIN" / "Suite"
    pasta.mkdir(parents=True)
    (pasta / "FINA050TestSuite.py").write_text(
        'suite.addTest(FINA050("test_FINA050_CT001"))\n', encoding="utf-8")
    (pasta / "FINA050TestCase.py").write_text("class FINA050:\n    pass\n",
                                              encoding="utf-8")
    (pasta / "FINA060TESTSUITE.PY").write_text(
        'suite.addTest(FINA060("test_FINA060_CT001"))\n', encoding="utf-8")
    (pasta / "FINA060TESTCASE.PY").write_text("class FINA060:\n    pass\n",
                                              encoding="utf-8")

    rotinas = cat.escanear_pais(tmp_path, "Brasil")["rotinas"]
    assert [r["rotina"] for r in rotinas] == ["FINA050", "FINA060"]
    # O par tem que ser achado apesar da caixa, senão a UI marca “sem TESTCASE”.
    assert all(r["tem_case"] for r in rotinas)
    assert [Path(r["case"]).name for r in rotinas] == ["FINA050TestCase.py",
                                                       "FINA060TESTCASE.PY"]


def test_case_em_pasta_irma_do_suite(tmp_path):
    """`Scripts Web\\Suite\\...TESTSUITE.py` + `Scripts Web\\Cases\\...TESTCASE.py`."""
    web = tmp_path / "Brasil" / "SIGAACD" / cat.PASTA_WEB
    (web / "Suite").mkdir(parents=True)
    (web / "Suite" / "ACDA010TESTSUITE.py").write_text(
        'suite.addTest(ACDA010("test_ACDA010_CT001"))\n', encoding="utf-8")
    (web / "Cases").mkdir()
    (web / "Cases" / "ACDA010TESTCASE.py").write_text("class ACDA010:\n    pass\n",
                                                      encoding="utf-8")
    rotina = cat.escanear_pais(tmp_path, "Brasil")["rotinas"][0]
    assert rotina["tem_case"] is True
    assert Path(rotina["case"]) == web / "Cases" / "ACDA010TESTCASE.py"


def test_case_no_nivel_acima_do_suite(tmp_path):
    web = tmp_path / "Brasil" / "SIGAFIN" / cat.PASTA_WEB
    (web / "Suite").mkdir(parents=True)
    (web / "Suite" / "FINA050TESTSUITE.py").write_text(
        'suite.addTest(FINA050("test_FINA050_CT001"))\n', encoding="utf-8")
    (web / "FINA050TESTCASE.py").write_text("class FINA050:\n    pass\n",
                                            encoding="utf-8")
    rotina = cat.escanear_pais(tmp_path, "Brasil")["rotinas"][0]
    assert Path(rotina["case"]) == web / "FINA050TESTCASE.py"


def test_case_de_outra_rotina_nao_e_confundido(tmp_path):
    """A procura é por nome exato; rotina sem par continua sinalizada."""
    web = tmp_path / "Brasil" / "SIGAFIN" / cat.PASTA_WEB
    (web / "Suite").mkdir(parents=True)
    (web / "Suite" / "FINA060TESTSUITE.py").write_text(
        'suite.addTest(FINA060("test_FINA060_CT001"))\n', encoding="utf-8")
    (web / "Cases").mkdir()
    (web / "Cases" / "FINA070TESTCASE.py").write_text("class FINA070:\n    pass\n",
                                                      encoding="utf-8")
    rotina = cat.escanear_pais(tmp_path, "Brasil")["rotinas"][0]
    assert rotina["tem_case"] is False
    assert Path(rotina["case"]).parent == web / "Suite"


def test_obsoletos_fica_de_fora(tmp_path):
    """Teste aposentado não volta para a lista por causa da varredura funda."""
    _rotina(tmp_path / "Brasil" / "SIGATMS" / "Obsoletos", "TMSA100",
            'suite.addTest(TMSA100("test_TMSA100_CT001"))\n')
    _rotina(tmp_path / "Brasil" / "SIGATMS" / cat.PASTA_WEB, "TMSA200",
            'suite.addTest(TMSA200("test_TMSA200_CT001"))\n')
    rotinas = cat.escanear_pais(tmp_path, "Brasil")["rotinas"]
    assert [r["rotina"] for r in rotinas] == ["TMSA200"]


def test_mesmo_nome_em_modulos_diferentes_sao_duas_rotinas(tmp_path):
    """FATA080 existe em SIGACRM e em SIGAFAT no disco real."""
    for modulo in ("SIGACRM", "SIGAFAT"):
        _rotina(tmp_path / "Brasil" / modulo / cat.PASTA_WEB / "Suite", "FATA080",
                'suite.addTest(FATA080("test_FATA080_CT001"))\n')
    rotinas = cat.escanear_pais(tmp_path, "Brasil")["rotinas"]
    assert [r["modulo"] for r in rotinas] == ["SIGACRM", "SIGAFAT"]


def test_mesmo_nome_no_mesmo_modulo_entra_uma_vez(tmp_path):
    """Não existe no disco hoje; se aparecer, o primeiro vence e o log avisa."""
    base = tmp_path / "Brasil" / "SIGAFIN"
    _rotina(base / cat.PASTA_WEB / "Suite", "FINA010",
            'suite.addTest(FINA010("test_FINA010_CT001"))\n')
    _rotina(base / "Scripts TIR", "FINA010",
            'suite.addTest(FINA010("test_FINA010_CT002"))\n')
    rotinas = cat.escanear_pais(tmp_path, "Brasil")["rotinas"]
    assert [r["rotina"] for r in rotinas] == ["FINA010"]


def test_rotinas_saem_ordenadas_por_modulo_e_nome(tmp_path):
    _rotina(tmp_path / "Brasil" / "SIGAFIN" / "Scripts TIR", "FINA020",
            'suite.addTest(FINA020("test_a"))\n')
    _rotina(tmp_path / "Brasil" / "SIGAFIN" / cat.PASTA_WEB, "FINA010",
            'suite.addTest(FINA010("test_a"))\n')
    _rotina(tmp_path / "Brasil" / "SIGACOM" / cat.PASTA_WEB, "COMA222",
            'suite.addTest(COMA222("test_a"))\n')
    rotinas = cat.escanear_pais(tmp_path, "Brasil")["rotinas"]
    assert [r["rotina"] for r in rotinas] == ["COMA222", "FINA010", "FINA020"]


def test_pad_todos_paises_e_ignorada(raiz):
    assert cat.pasta_do_pais(raiz, "PAD - Todos Paises") is None


def test_pais_sem_pasta_nao_quebra(raiz):
    r = cat.escanear_pais(raiz, "Angola")
    assert r["ok"] is False
    assert r["rotinas"] == []


def test_raiz_inexistente_e_erro_claro(tmp_path):
    r = cat.escanear_pais(tmp_path / "nao-existe", "Mexico")
    assert r["ok"] is False
    assert "Raiz" in r["erro"]


# ── Casos de teste ──────────────────────────────────────────

def test_casos_saem_do_suite_na_ordem(raiz):
    rotina = cat.escanear_pais(raiz, "Mexico")["rotinas"][0]
    assert rotina["casos"] == ["test_COMA222_CT006", "test_COMA222_CT007",
                               "test_COMA222_CT011"]


def test_caso_repetido_no_suite_aparece_uma_vez(tmp_path):
    web = tmp_path / "Brasil" / "SIGACOM" / cat.PASTA_WEB
    _rotina(web, "X001", 'suite.addTest(X001("test_X001_CT001"))\n' * 3)
    assert cat.escanear_pais(tmp_path, "Brasil")["rotinas"][0]["casos"] == \
        ["test_X001_CT001"]


def test_suite_sem_addtest_cai_para_o_testcase(tmp_path):
    """Reserva: alguns suites não usam addTest legível."""
    web = tmp_path / "Brasil" / "SIGACOM" / cat.PASTA_WEB
    web.mkdir(parents=True)
    (web / "Y002TESTSUITE.py").write_text("# suite montado de outro jeito\n",
                                          encoding="utf-8")
    (web / "Y002TESTCASE.py").write_text(
        "class Y002:\n    def test_Y002_CT001(self):\n        pass\n",
        encoding="utf-8")
    assert cat.escanear_pais(tmp_path, "Brasil")["rotinas"][0]["casos"] == \
        ["test_Y002_CT001"]


def test_rotina_sem_testcase_e_sinalizada(tmp_path):
    """Sem o TESTCASE o suite quebra no import — a UI precisa avisar antes."""
    web = tmp_path / "Brasil" / "SIGACOM" / cat.PASTA_WEB
    _rotina(web, "Z003", 'suite.addTest(Z003("test_Z003_CT001"))\n', com_case=False)
    assert cat.escanear_pais(tmp_path, "Brasil")["rotinas"][0]["tem_case"] is False


# ── Descoberta da raiz ──────────────────────────────────────

def test_sugestao_valida_vence_a_busca(raiz, monkeypatch):
    """Apontar à mão tem que ser mais forte e mais rápido que adivinhar."""
    def _nao_deveria_buscar():
        raise AssertionError("buscou mesmo com sugestão válida")
    monkeypatch.setattr(cat, "_bases_provaveis", _nao_deveria_buscar)
    assert cat.descobrir_raiz(raiz) == raiz


def test_sugestao_invalida_cai_para_a_busca(raiz, tmp_path, monkeypatch):
    monkeypatch.setattr(cat, "_bases_provaveis", lambda: [tmp_path])
    # A raiz do fixture está em tmp_path/"Automação Protheus" — sem a pasta
    # "Testes" no meio, a busca não deve achar.
    assert cat.descobrir_raiz(tmp_path / "nao-existe") is None


def test_encontra_com_pasta_intermediaria(tmp_path, monkeypatch):
    """`C:\\Dev\\Fontes\\Testes\\Automação Protheus`: um nível no meio."""
    alvo = tmp_path / "Fontes" / cat.PASTA_PAI / cat.NOME_RAIZ
    _rotina(alvo / "Mexico" / "SIGACOM" / cat.PASTA_WEB, "X1",
            'suite.addTest(X1("test_X1_CT001"))\n')
    monkeypatch.setattr(cat, "_bases_provaveis", lambda: [tmp_path])
    assert cat.descobrir_raiz() == alvo


def test_encontra_direto_na_base(tmp_path, monkeypatch):
    """Download automático: `<perfil>\\Testes\\Automação Protheus`."""
    alvo = tmp_path / cat.PASTA_PAI / cat.NOME_RAIZ
    _rotina(alvo / "Brasil" / "SIGAFIN" / cat.PASTA_WEB, "X2",
            'suite.addTest(X2("test_X2_CT001"))\n')
    monkeypatch.setattr(cat, "_bases_provaveis", lambda: [tmp_path])
    assert cat.descobrir_raiz() == alvo


def test_raiz_valida_sem_scripts_web(tmp_path, monkeypatch):
    """Base só com `Scripts TIR` é raiz de testes do mesmo jeito."""
    alvo = tmp_path / cat.PASTA_PAI / cat.NOME_RAIZ
    _rotina(alvo / "Brasil" / "SIGAPLS" / "Scripts TIR" / "Suite", "PLSA010",
            'suite.addTest(PLSA010("test_PLSA010_CT001"))\n')
    monkeypatch.setattr(cat, "_bases_provaveis", lambda: [tmp_path])
    assert cat.descobrir_raiz() == alvo


def test_pasta_vazia_nao_conta_como_raiz(tmp_path, monkeypatch):
    """Pasta com o nome certo mas sem nenhum `Scripts Web` não serve."""
    (tmp_path / cat.PASTA_PAI / cat.NOME_RAIZ / "Brasil" / "SIGAFIN").mkdir(parents=True)
    monkeypatch.setattr(cat, "_bases_provaveis", lambda: [tmp_path])
    assert cat.descobrir_raiz() is None


# ── Busca ───────────────────────────────────────────────────

def test_busca_por_trecho_do_nome(raiz):
    rotinas = cat.escanear_pais(raiz, "Mexico")["rotinas"]
    assert [x["rotina"] for x in cat.filtrar(rotinas, "222")] == ["COMA222"]
    assert [x["rotina"] for x in cat.filtrar(rotinas, "coma")] == ["COMA222"]


def test_busca_pelo_modulo(raiz):
    rotinas = cat.escanear_pais(raiz, "Mexico")["rotinas"]
    assert [x["rotina"] for x in cat.filtrar(rotinas, "sigafin")] == ["FINA003"]


def test_busca_vazia_devolve_tudo(raiz):
    rotinas = cat.escanear_pais(raiz, "Mexico")["rotinas"]
    assert len(cat.filtrar(rotinas, "   ")) == len(rotinas)
