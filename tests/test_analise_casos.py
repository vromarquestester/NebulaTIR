"""Divisão dos casos entre instâncias (`services.analise_casos`).

A regra: casos independentes se espalham; casos ligados por dependência ficam
**juntos, na mesma instância e na ordem do suite** — assim o banco já chega
com o dado que o caso anterior criou.

Os trechos abaixo imitam scripts reais do repositório de testes.
"""

import pytest

from services import analise_casos

INDEPENDENTES = '''
from tir import Webapp
import unittest

class COMA222(unittest.TestCase):
    @classmethod
    def setUpClass(inst):
        inst.oHelper = Webapp()
        inst.oHelper.Setup('SIGACOM', '', 'T1', 'D MG 01 ', '02')

    def test_COMA222_CT001(self):
        self.oHelper.Program('COMA222')
        self.oHelper.SetValue("F1_FORNECE", "P23276", name_attr=True)
        self.oHelper.SetButton("Visualizar")

    def test_COMA222_CT002(self):
        self.oHelper.Program('COMA222')
        self.oHelper.SetValue("F1_FORNECE", "P99887", name_attr=True)
        self.oHelper.SetButton("Visualizar")

    def test_COMA222_CT003(self):
        self.oHelper.Program('COMA222')
        self.oHelper.SetButton("Visualizar")
'''

ESTADO_COMPARTILHADO = '''
import unittest

class MATA030(unittest.TestCase):
    def test_MATA030_CT001(self):
        self.codigo_cliente = "CLI9001"
        self.oHelper.SetButton("Incluir")

    def test_MATA030_CT002(self):
        self.oHelper.SetValue("A1_COD", self.codigo_cliente)

    def test_MATA030_CT003(self):
        self.oHelper.Program('MATA030')
'''

DADO_CRIADO = '''
import unittest

class MATA030(unittest.TestCase):
    def test_MATA030_CT001(self):
        self.oHelper.SetButton("Incluir")
        self.oHelper.SetValue("A1_COD", "CLI9001")
        self.oHelper.SetButton("Salvar")

    def test_MATA030_CT002(self):
        self.oHelper.SetValue("A1_COD", "CLI9001")
        self.oHelper.SetButton("Visualizar")

    def test_MATA030_CT003(self):
        self.oHelper.SetValue("A1_COD", "CLI0002")
'''


def _arquivo(tmp_path, fonte, nome="X"):
    caminho = tmp_path / f"{nome}TESTCASE.py"
    caminho.write_text(fonte, encoding="utf-8")
    return caminho


# ── Independentes ───────────────────────────────────────────

def test_casos_independentes_viram_unidades_separadas(tmp_path):
    caminho = _arquivo(tmp_path, INDEPENDENTES)
    r = analise_casos.analisar(caminho, ["test_COMA222_CT001",
                                         "test_COMA222_CT002",
                                         "test_COMA222_CT003"])
    assert r.divisivel is True
    assert r.grupos == [["test_COMA222_CT001"], ["test_COMA222_CT002"],
                        ["test_COMA222_CT003"]]


def test_dado_preexistente_repetido_nao_cria_dependencia(tmp_path):
    """`P23276` aparece só num caso e nenhum deles inclui registro — dado da
    base congelada não é dependência."""
    caminho = _arquivo(tmp_path, INDEPENDENTES)
    r = analise_casos.analisar(caminho, ["test_COMA222_CT001",
                                         "test_COMA222_CT002"])
    assert r.divisivel is True


# ── Estado compartilhado: prova ─────────────────────────────

def test_estado_na_classe_mantem_os_casos_juntos(tmp_path):
    caminho = _arquivo(tmp_path, ESTADO_COMPARTILHADO)
    r = analise_casos.analisar(caminho, ["test_MATA030_CT001",
                                         "test_MATA030_CT002",
                                         "test_MATA030_CT003"])
    # Os dois primeiros formam cadeia; o terceiro é independente.
    assert r.grupos == [["test_MATA030_CT001", "test_MATA030_CT002"],
                        ["test_MATA030_CT003"]]
    assert r.divisivel is True
    assert "self.codigo_cliente" in r.motivo


# ── Dado criado e reaproveitado: indício ────────────────────

def test_dado_incluido_e_reusado_mantem_os_casos_juntos(tmp_path):
    """CT001 inclui `CLI9001` e CT002 usa o mesmo valor: rodar separado faria
    o segundo procurar um cliente que ninguém criou naquele banco."""
    caminho = _arquivo(tmp_path, DADO_CRIADO)
    r = analise_casos.analisar(caminho, ["test_MATA030_CT001",
                                         "test_MATA030_CT002",
                                         "test_MATA030_CT003"])
    assert r.grupos[0] == ["test_MATA030_CT001", "test_MATA030_CT002"]
    assert ["test_MATA030_CT003"] in r.grupos
    assert "CLI9001" in r.motivo


def test_cadeia_inteira_nao_divide(tmp_path):
    caminho = _arquivo(tmp_path, DADO_CRIADO)
    r = analise_casos.analisar(caminho, ["test_MATA030_CT001",
                                         "test_MATA030_CT002"])
    assert r.divisivel is False
    assert r.grupos == [["test_MATA030_CT001", "test_MATA030_CT002"]]


# ── Segurança: na dúvida, junta ─────────────────────────────

def test_arquivo_ilegivel_nao_divide(tmp_path):
    caminho = _arquivo(tmp_path, "isto ( nao é python")
    r = analise_casos.analisar(caminho, ["test_A", "test_B"])
    assert r.divisivel is False
    assert "não consegui ler" in r.motivo.lower()


def test_caso_do_suite_ausente_no_arquivo_nao_divide(tmp_path):
    """Suite e arquivo fora de sincronia: analisar seria adivinhação."""
    caminho = _arquivo(tmp_path, INDEPENDENTES)
    r = analise_casos.analisar(caminho, ["test_COMA222_CT001", "test_SUMIU"])
    assert r.divisivel is False
    assert "test_SUMIU" in r.motivo


def test_um_caso_so_nao_divide(tmp_path):
    caminho = _arquivo(tmp_path, INDEPENDENTES)
    assert analise_casos.analisar(caminho, ["test_COMA222_CT001"]).divisivel is False


# ── Calibragem do que conta como dado criado ────────────────
# Rodado contra os scripts reais: comparar qualquer literal acusava
# dependência em quase toda rotina, porque "Normal", "Crédito" e "0001" são
# vocabulário de domínio. Estes casos travam o ajuste.

@pytest.mark.parametrize("valor", ["P23276", "CLI9001", "REM001", "CAS642",
                                   "1705202500003"])
def test_chave_de_negocio_conta(valor):
    assert analise_casos._parece_chave(valor) is True


@pytest.mark.parametrize("valor", ["Normal", "Crédito", "0001", "000001",
                                   "Fact", "02", "T1"])
def test_vocabulario_de_dominio_nao_conta(valor):
    assert analise_casos._parece_chave(valor) is False


def test_nome_de_campo_nao_conta_como_dado(tmp_path):
    """`SetValue("A1_COD", "CLI9001")`: o campo é a posição 0, o dado é a 1.
    Comparar a posição 0 acusaria dependência entre quaisquer dois casos que
    mexem no mesmo campo."""
    fonte = '''
import unittest
class X(unittest.TestCase):
    def test_A(self):
        self.oHelper.SetButton("Incluir")
        self.oHelper.SetValue("A1_COD", "CLI0001")
    def test_B(self):
        self.oHelper.SetValue("A1_COD", "CLI0002")
'''
    r = analise_casos.analisar(_arquivo(tmp_path, fonte), ["test_A", "test_B"])
    assert r.divisivel is True


# ── Integração com a fila ───────────────────────────────────

def test_unidades_respeita_a_chave_desligada(tmp_path):
    rotina = {"rotina": "X", "case": str(_arquivo(tmp_path, INDEPENDENTES)),
              "casos": ["test_COMA222_CT001", "test_COMA222_CT002"]}
    grupos, analise = analise_casos.unidades(rotina, ativo=False)
    assert grupos == [rotina["casos"]]
    assert analise is None


def test_unidades_divide_quando_ligada(tmp_path):
    rotina = {"rotina": "X", "case": str(_arquivo(tmp_path, INDEPENDENTES)),
              "casos": ["test_COMA222_CT001", "test_COMA222_CT002"]}
    grupos, analise = analise_casos.unidades(rotina, ativo=True)
    assert grupos == [["test_COMA222_CT001"], ["test_COMA222_CT002"]]
    assert analise.divisivel is True
