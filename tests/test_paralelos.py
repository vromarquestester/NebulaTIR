"""Geração dos paralelos e as duas restaurações de RPO (`services.paralelos`)."""

from pathlib import Path

import pytest

from services import paralelos
from services.instancias import Instancias


class GerenciadorFalso:
    """Dublê que imita o comportamento real: `clonar` devolve assim que
    dispara a thread, e o ambiente só existe depois que a operação termina."""

    def __init__(self, falhar_em=None, nunca_termina=False,
                 nao_aparece=None):
        self.clonagens = []
        self.eventos = []          # ordem real das chamadas
        self.falhar_em = falhar_em
        self.nunca_termina = nunca_termina
        self.nao_aparece = nao_aparece
        self._criados = set()

    def clonar(self, **kw):
        self.clonagens.append(kw)
        self.eventos.append(("clonar", kw["novo_ambiente"]))
        if self.falhar_em and kw["novo_ambiente"] == self.falhar_em:
            return {"ok": False, "erro": "Já existe uma operação em andamento."}
        if kw["novo_ambiente"] != self.nao_aparece:
            self._criados.add(kw["novo_ambiente"])
        return {"ok": True}

    def esperar_ocioso(self, limite_seg=3600, parar=None):
        self.eventos.append(("esperar", limite_seg))
        if self.nunca_termina:
            return {"ok": False, "erro": "Tempo esgotado esperando o Gerenciador."}
        return {"ok": True}

    def atualizar(self):
        return {"online": True}

    def banco_por_nome(self, nome):
        return {"ambiente": nome} if nome in self._criados else None


def _plano(n):
    return {"instancias": [{"slot": i + 1, "portas": {"webapp": 4321 + i}}
                           for i in range(n)]}


@pytest.fixture
def reg(tmp_path):
    return Instancias(tmp_path / "instancias.json")


# ── Geração ─────────────────────────────────────────────────

def test_clona_a_quantidade_pedida(reg):
    ger = GerenciadorFalso()
    r = paralelos.gerar(origem="PAR_2510", banco_origem="P12", quantidade=3,
                        estado_gerenciador=ger, registro=reg,
                        plano_portas=_plano(3))
    assert r["ok"] is True
    assert [c["ambiente"] for c in r["criados"]] == \
        ["PAR_2510_TIR1", "PAR_2510_TIR2", "PAR_2510_TIR3"]
    assert len(ger.clonagens) == 3


def test_usa_as_credenciais_padrao_de_toda_base(reg):
    """`sa` / `123456`, como definido — o clone precisa delas para o DSN."""
    ger = GerenciadorFalso()
    paralelos.gerar(origem="A", banco_origem="B", quantidade=1,
                    estado_gerenciador=ger, registro=reg, plano_portas=_plano(1))
    assert ger.clonagens[0]["odbc_user"] == "sa"
    assert ger.clonagens[0]["odbc_pass"] == "123456"


def test_cada_paralelo_tem_banco_proprio(reg):
    """Um banco por slot: os testes fixam grupo e filial, não dá para dividir."""
    ger = GerenciadorFalso()
    paralelos.gerar(origem="A", banco_origem="BASE", quantidade=2,
                    estado_gerenciador=ger, registro=reg, plano_portas=_plano(2))
    bancos = [c["novo_banco"] for c in ger.clonagens]
    assert bancos == ["BASE_TIR1", "BASE_TIR2"]
    assert len(set(bancos)) == 2


def test_banco_da_instancia_e_o_do_pai_com_o_sufixo_do_slot(reg):
    """`<banco do pai>_TIR<slot>`, e nada além disso.

    O SQL Server tem bases de outras frentes; nome sem o sufixo do slot colide
    com elas e com o próprio pai. O sufixo também é o que identifica, olhando
    só a lista do MSSQL, de qual instância aquela base é.
    """
    ger = GerenciadorFalso()
    paralelos.gerar(origem="PAR2510_V1", banco_origem="P1212510MNTDBPAREXP_V1",
                    quantidade=2, estado_gerenciador=ger, registro=reg,
                    plano_portas=_plano(2))

    assert [c["novo_banco"] for c in ger.clonagens] == [
        "P1212510MNTDBPAREXP_V1_TIR1", "P1212510MNTDBPAREXP_V1_TIR2"]
    # O registro guarda o mesmo nome: é por ele que a limpeza vai achar o banco
    # a desanexar, inclusive quando o ambiente já saiu do Gerenciador.
    assert [i["banco"] for i in reg.listar("PAR2510_V1")] == [
        "P1212510MNTDBPAREXP_V1_TIR1", "P1212510MNTDBPAREXP_V1_TIR2"]


def test_clone_pede_a_pasta_das_instancias_e_o_temp_do_pai(reg):
    """As instâncias ficam agrupadas em `NebulaInstancia`, e o temp do pai não
    é duplicado: era um ambiente Protheus inteiro de artefato repetido."""
    ger = GerenciadorFalso()
    paralelos.gerar(origem="A", banco_origem="B", quantidade=1,
                    estado_gerenciador=ger, registro=reg, plano_portas=_plano(1))
    assert ger.clonagens[0]["subpasta"] == "NebulaInstancia"
    assert ger.clonagens[0]["reaproveitar_temp"] is True


def test_portas_vem_do_plano(reg):
    ger = GerenciadorFalso()
    paralelos.gerar(origem="A", banco_origem="B", quantidade=2,
                    estado_gerenciador=ger, registro=reg, plano_portas=_plano(2))
    assert [c["port"] for c in ger.clonagens] == ["4321", "4322"]


def test_reaproveita_os_que_ja_existem(reg):
    """Paralelo existente é sinal de execução interrompida — clonar de novo
    desperdiçaria os GB da base."""
    ger = GerenciadorFalso()
    r = paralelos.gerar(origem="A", banco_origem="B", quantidade=2,
                        estado_gerenciador=ger, registro=reg,
                        plano_portas=_plano(2),
                        existentes=["A_TIR1", "A_TIR2"])
    assert ger.clonagens == []
    assert r["reaproveitados"] == ["A_TIR1", "A_TIR2"]


def test_completa_o_que_falta(reg):
    ger = GerenciadorFalso()
    r = paralelos.gerar(origem="A", banco_origem="B", quantidade=3,
                        estado_gerenciador=ger, registro=reg,
                        plano_portas=_plano(3), existentes=["A_TIR1"])
    assert len(r["criados"]) == 2


def test_falha_numa_clonagem_interrompe(reg):
    """Clonagem é cara e sequencial no Gerenciador: falhou, para."""
    ger = GerenciadorFalso(falhar_em="A_TIR2")
    r = paralelos.gerar(origem="A", banco_origem="B", quantidade=3,
                        estado_gerenciador=ger, registro=reg,
                        plano_portas=_plano(3))
    assert len(r["criados"]) == 1
    assert r["erros"][0]["ambiente"] == "A_TIR2"
    assert len(ger.clonagens) == 2      # não tentou a terceira


# ── Sequenciamento: o defeito que travou a geração real ─────

def test_espera_cada_clonagem_terminar_antes_da_proxima(reg):
    """`clonar` devolve quando DISPARA a thread, não quando termina. Sem
    esperar, o segundo pedido bate no `_ocupar` do Gerenciador e volta
    "Já existe uma operação em andamento" — foi o que aconteceu de verdade."""
    ger = GerenciadorFalso()
    paralelos.gerar(origem="A", banco_origem="B", quantidade=3,
                    estado_gerenciador=ger, registro=reg, plano_portas=_plano(3))
    tipos = [e[0] for e in ger.eventos]
    assert tipos == ["clonar", "esperar", "clonar", "esperar", "clonar", "esperar"]


def test_clonagem_que_nao_termina_interrompe(reg):
    ger = GerenciadorFalso(nunca_termina=True)
    r = paralelos.gerar(origem="A", banco_origem="B", quantidade=3,
                        estado_gerenciador=ger, registro=reg,
                        plano_portas=_plano(3))
    assert r["criados"] == []
    assert "não terminou" in r["erros"][0]["erro"]
    assert len(ger.clonagens) == 1      # não insistiu


def test_ambiente_que_nao_aparece_e_erro(reg):
    """`ok` da thread não é garantia: a clonagem pode falhar no meio. O
    ambiente aparecer no Gerenciador é a prova."""
    ger = GerenciadorFalso(nao_aparece="A_TIR1")
    r = paralelos.gerar(origem="A", banco_origem="B", quantidade=2,
                        estado_gerenciador=ger, registro=reg,
                        plano_portas=_plano(2))
    assert r["criados"] == []
    assert "não apareceu" in r["erros"][0]["erro"]
    assert reg.nomes("A") == []         # não registrou o que não existe


# ── RPO ─────────────────────────────────────────────────────

@pytest.fixture
def rpo(tmp_path, monkeypatch):
    monkeypatch.setattr(paralelos, "pasta_do_programa", lambda: tmp_path / "prog")
    pasta = tmp_path / "ambiente" / "apo"
    pasta.mkdir(parents=True)
    (pasta / "custom.rpo").write_bytes(b"COM-PACOTE")
    return pasta


def test_guardar_e_restaurar_o_rpo_do_ambiente(rpo):
    assert paralelos.guardar_rpo("AMB", str(rpo))["ok"] is True
    assert paralelos.tem_rpo_guardado("AMB") is True

    (rpo / "custom.rpo").write_bytes(b"ZERADO")     # alguém repôs o zerado
    assert paralelos.restaurar_rpo_do_ambiente("AMB", str(rpo))["ok"] is True
    assert (rpo / "custom.rpo").read_bytes() == b"COM-PACOTE"


def test_restaurar_sem_ter_guardado_avisa(rpo):
    r = paralelos.restaurar_rpo_do_ambiente("AMB", str(rpo))
    assert r["ok"] is False
    assert "Guardar RPO" in r["erro"]


def test_guardar_rpo_de_pasta_inexistente_falha(tmp_path, monkeypatch):
    monkeypatch.setattr(paralelos, "pasta_do_programa", lambda: tmp_path)
    assert paralelos.guardar_rpo("AMB", str(tmp_path / "nao-existe"))["ok"] is False


def test_guardar_de_novo_sobrescreve(rpo):
    paralelos.guardar_rpo("AMB", str(rpo))
    (rpo / "novo.rpo").write_bytes(b"X")
    r = paralelos.guardar_rpo("AMB", str(rpo))
    assert r["arquivos"] == 2
