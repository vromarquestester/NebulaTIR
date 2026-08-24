"""Remoção do que o Gerenciador não alcança (`services.limpeza`).

Este é o módulo que apaga de verdade: processos, banco, DSN e pastas de GB.
Nada aqui pode rodar contra a máquina durante o teste, então cada efeito é
substituído — e o que se verifica é a **ordem** e o que acontece quando um
passo falha, que é onde mora o estrago.
"""

from pathlib import Path

import pytest

from services import limpeza as mod


@pytest.fixture
def instancia(tmp_path):
    """Uma instância com pasta e workspace, como o inventário devolve."""
    pasta = tmp_path / "TOTVS" / "A_TIR1"
    workspace = tmp_path / "Downloads" / "A_TIR1"
    for p in (pasta, workspace):
        p.mkdir(parents=True)
        (p / "arquivo.bin").write_bytes(b"x" * 10)
    return {"ambiente": "A_TIR1", "banco": "BASE_TIR1",
            "pasta": str(pasta), "workspace_banco": str(workspace),
            "caminhos": [str(pasta), str(workspace)]}


@pytest.fixture
def sem_efeito(monkeypatch):
    """Neutraliza o que toca a máquina; cada teste religa o que quer olhar."""
    monkeypatch.setattr(mod, "matar_processos_da_pasta",
                        lambda pasta: {"ok": True, "mortos": 0})
    monkeypatch.setattr(mod, "derrubar_banco",
                        lambda banco, servidor, driver: {"ok": True})
    monkeypatch.setattr(mod, "excluir_dsn", lambda nome: {"ok": True})
    return monkeypatch


# ── Pastas ──────────────────────────────────────────────────

def test_apaga_a_arvore(tmp_path):
    alvo = tmp_path / "raiz" / "A_TIR1"
    (alvo / "Protheus").mkdir(parents=True)
    (alvo / "Protheus" / "a.rpo").write_bytes(b"x")
    assert mod.apagar_pasta(alvo)["ok"] is True
    assert not alvo.exists()


def test_pasta_inexistente_nao_e_erro(tmp_path):
    r = mod.apagar_pasta(tmp_path / "nao-existe")
    assert r["ok"] is True and r["pulado"] == "não existe"


def test_recusa_caminho_raso():
    """O caminho vem de arquivo de registro: valor errado ali não pode virar
    `rmtree` na raiz de um disco."""
    r = mod.apagar_pasta("C:\\")
    assert r["ok"] is False
    assert "raso" in r["erro"]


def test_caminho_vazio_nao_faz_nada():
    """`Path("")` é `.`: campo em branco no registro chegaria na pasta do
    programa se a checagem viesse depois da conversão."""
    for vazio in ("", "   ", None):
        r = mod.apagar_pasta(vazio)
        assert r["ok"] is True and r["pulado"] == "caminho vazio"


# ── Banco ───────────────────────────────────────────────────

def test_sem_banco_no_registro_pula():
    r = mod.derrubar_banco("", "servidor", "driver")
    assert r["ok"] is True and "sem banco" in r["pulado"]


def test_sem_servidor_e_erro_explicito():
    """Sem o Gerenciador não há servidor nem driver — e o banco fica de pé."""
    r = mod.derrubar_banco("BASE_TIR1", "", "")
    assert r["ok"] is False
    assert "SQL" in r["erro"]


def test_banco_inexistente_no_servidor_nao_e_erro(monkeypatch):
    """A limpeza roda sobre restos: metade já não tem base anexada."""
    monkeypatch.setattr(mod, "conectar", lambda *a, **k: _ConexaoFalsa(db_id=None))
    r = mod.derrubar_banco("BASE_TIR1", "s", "d")
    assert r["ok"] is True and "não existe" in r["pulado"]


def test_drop_derruba_sessoes_antes(monkeypatch):
    """Sem `SINGLE_USER` o drop trava esperando a conexão que o AppServer
    deixou aberta."""
    conexao = _ConexaoFalsa(db_id=7)
    monkeypatch.setattr(mod, "conectar", lambda *a, **k: conexao)

    assert mod.derrubar_banco("BASE_TIR1", "s", "d")["ok"] is True
    assert conexao.comandos == [
        "ALTER DATABASE [BASE_TIR1] SET SINGLE_USER WITH ROLLBACK IMMEDIATE",
        "DROP DATABASE [BASE_TIR1]",
    ]
    assert conexao.fechada is True


def test_falha_no_sql_vira_erro_e_nao_excecao(monkeypatch):
    def _explode(*a, **k):
        raise RuntimeError("login failed")
    monkeypatch.setattr(mod, "conectar", _explode)
    r = mod.derrubar_banco("BASE_TIR1", "s", "d")
    assert r["ok"] is False
    assert "login failed" in r["erro"]


class _ConexaoFalsa:
    def __init__(self, db_id):
        self._db_id = db_id
        self.comandos = []
        self.fechada = False

    def cursor(self):
        return self

    def execute(self, sql, *params):
        if sql.startswith("SELECT DB_ID"):
            return _Resultado(self._db_id)
        self.comandos.append(sql)
        return _Resultado(None)

    def close(self):
        self.fechada = True


class _Resultado:
    def __init__(self, valor):
        self._valor = valor

    def fetchone(self):
        return (self._valor,)


# ── Orquestração ────────────────────────────────────────────

def test_ordem_dos_passos(instancia, sem_efeito):
    """Processos antes de tudo (seguram arquivo aberto), banco antes das
    pastas (o SQL bloqueia o MDF), DSN no meio."""
    ordem = []
    sem_efeito.setattr(mod, "matar_processos_da_pasta",
                       lambda p: ordem.append("processos") or {"ok": True})
    sem_efeito.setattr(mod, "derrubar_banco",
                       lambda b, servidor, driver: ordem.append("banco") or {"ok": True})
    sem_efeito.setattr(mod, "excluir_dsn",
                       lambda n: ordem.append("dsn") or {"ok": True})

    mod.remover(instancia, servidor="s", driver="d")
    assert ordem[:2] == ["processos", "processos"]   # pasta e workspace
    assert ordem[2:] == ["banco", "dsn"]
    assert not Path(instancia["pasta"]).exists()


def test_remove_tudo_e_reporta_ok(instancia, sem_efeito):
    r = mod.remover(instancia, servidor="s", driver="d")
    assert r["ok"] is True
    assert r["erros"] == []
    assert not Path(instancia["pasta"]).exists()
    assert not Path(instancia["workspace_banco"]).exists()


def test_falha_num_passo_nao_cancela_os_outros(instancia, sem_efeito):
    """Parar no meio deixa metade removida e nenhum registro do que sobrou."""
    sem_efeito.setattr(mod, "excluir_dsn",
                       lambda n: {"ok": False, "erro": "sem permissão"})

    r = mod.remover(instancia, servidor="s", driver="d")
    assert r["ok"] is False
    assert any("sem permissão" in e for e in r["erros"])
    # As pastas saíram assim mesmo: são elas que ocupam o disco.
    assert not Path(instancia["pasta"]).exists()


def test_o_banco_da_o_nome_do_dsn(instancia, sem_efeito):
    """O clone cria o DSN com o nome do banco — não há campo separado."""
    vistos = []
    sem_efeito.setattr(mod, "excluir_dsn",
                       lambda n: vistos.append(n) or {"ok": True})
    mod.remover(instancia, servidor="s", driver="d")
    assert vistos == ["BASE_TIR1"]


def test_cada_passo_aparece_no_relatorio(instancia, sem_efeito):
    r = mod.remover(instancia, servidor="s", driver="d")
    passos = [p["passo"] for p in r["passos"]]
    assert "banco" in passos and "dsn" in passos
    assert sum(1 for p in passos if p.startswith("pasta ")) == 2
