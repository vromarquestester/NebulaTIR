"""Inventário das instâncias (`services.inventario`).

O que este módulo protege: um ambiente Protheus por instância é dezenas de GB,
e o caso que abriu a frente foi pasta de pé sem ninguém saber mais de onde
veio. O contrário também: declarar órfã o que não é, e apagar o que estava em
uso — foi quase o que aconteceu aqui, porque a máquina tem duas instalações do
Gerenciador, cada uma com o próprio cadastro.
"""

from pathlib import Path

from services import inventario as inv


def _registro(ambiente, origem, pasta=None, workspace=None, banco="B"):
    item = {"ambiente": ambiente, "origem": origem, "banco": banco,
            "estado": "parada"}
    if pasta:
        item["pasta"] = str(pasta)
    if workspace:
        item["workspace_banco"] = str(workspace)
    return item


def _situacoes(resultado):
    return {i["ambiente"]: i["situacao"] for i in resultado["instancias"]}


# ── Nome da instância ───────────────────────────────────────

def test_decompoe_o_nome_da_instancia():
    assert inv.decompor("PAR2510_V1_TIR2") == ("PAR2510_V1", 2)
    assert inv.decompor("PAR2510_V1") is None
    assert inv.decompor("") is None


# ── Situação ────────────────────────────────────────────────

def test_instancia_com_pai_no_gerenciador_esta_ok(tmp_path):
    pasta = tmp_path / "A_TIR1"
    pasta.mkdir()
    r = inv.levantar(registradas=[_registro("A_TIR1", "A", pasta)],
                     ambientes={"A", "A_TIR1"}, online=True)
    assert _situacoes(r) == {"A_TIR1": inv.OK}
    assert r["instancias"][0]["removivel"] is False


def test_pai_fora_do_gerenciador_e_orfa(tmp_path):
    """A regra do usuário: sem o pai, a instância não faz sentido."""
    pasta = tmp_path / "A_TIR1"
    pasta.mkdir()
    r = inv.levantar(registradas=[_registro("A_TIR1", "A", pasta)],
                     ambientes={"OUTRO"}, online=True)
    assert _situacoes(r) == {"A_TIR1": inv.ORFA}
    assert r["instancias"][0]["removivel"] is True


def test_instancia_sem_cadastro_mas_com_pai_vivo_nao_e_removivel(tmp_path):
    """Pai vivo significa que aquela instância pode estar numa corrida."""
    pasta = tmp_path / "A_TIR1"
    pasta.mkdir()
    r = inv.levantar(registradas=[_registro("A_TIR1", "A", pasta)],
                     ambientes={"A"}, online=True)
    assert _situacoes(r) == {"A_TIR1": inv.SEM_CADASTRO}
    assert r["instancias"][0]["removivel"] is False


def test_registro_sem_pasta_no_disco_e_fantasma(tmp_path):
    r = inv.levantar(registradas=[_registro("A_TIR1", "A", tmp_path / "sumiu")],
                     ambientes={"A", "A_TIR1"}, online=True)
    assert _situacoes(r) == {"A_TIR1": inv.FANTASMA}


def test_registro_sem_caminho_acha_a_pasta_pelo_nome(tmp_path):
    """As duas instâncias que já existiam foram registradas sem caminho: a
    pasta de mesmo nome no disco é o vínculo que sobrou."""
    pasta = tmp_path / "A_TIR1"
    pasta.mkdir()
    r = inv.levantar(registradas=[_registro("A_TIR1", "A")],
                     ambientes={"A", "A_TIR1"}, online=True, base_path=tmp_path)
    assert r["instancias"][0]["caminhos"] == [str(pasta)]
    assert r["instancias"][0]["situacao"] == inv.OK


def test_registro_antigo_sem_caminho_nao_vira_fantasma():
    """Registro de antes da mudança não tem caminho nenhum — isso não quer
    dizer que a instância sumiu do disco."""
    r = inv.levantar(registradas=[_registro("A_TIR1", "A")],
                     ambientes={"A", "A_TIR1"}, online=True)
    assert _situacoes(r) == {"A_TIR1": inv.OK}


# ── Gerenciador fechado ─────────────────────────────────────

def test_offline_nao_classifica_nada(tmp_path):
    """Cada instalação do Gerenciador tem o próprio cadastro; sem o canal no ar
    não dá para dizer o que sobra, e apagar GB por engano não se desfaz."""
    pasta = tmp_path / "A_TIR1"
    pasta.mkdir()
    r = inv.levantar(registradas=[_registro("A_TIR1", "A", pasta)],
                     ambientes=set(), online=False, base_path=tmp_path)
    assert _situacoes(r) == {"A_TIR1": inv.INDEFINIDA}
    assert all(not i["removivel"] for i in r["instancias"])


# ── Pastas que o registro não conhece ───────────────────────

def test_pasta_no_disco_sem_registro_aparece(tmp_path):
    (tmp_path / inv.pasta_raiz(tmp_path).name / "B_TIR1").mkdir(parents=True)
    r = inv.levantar(registradas=[], ambientes={"B"}, online=True,
                     base_path=tmp_path)
    assert _situacoes(r) == {"B_TIR1": inv.NAO_REGISTRADA}
    assert r["instancias"][0]["registrada"] is False


def test_instancia_legada_fora_da_pasta_nova_tambem_aparece(tmp_path):
    """As criadas antes desta mudança moram direto na raiz dos ambientes."""
    (tmp_path / "C_TIR3").mkdir()
    r = inv.levantar(registradas=[], ambientes={"C"}, online=True,
                     base_path=tmp_path)
    assert _situacoes(r) == {"C_TIR3": inv.NAO_REGISTRADA}


def test_pasta_que_nao_e_instancia_fica_de_fora(tmp_path):
    (tmp_path / "PAR2510_V1").mkdir()
    (tmp_path / "Downloads").mkdir()
    r = inv.levantar(registradas=[], ambientes={"PAR2510_V1"}, online=True,
                     base_path=tmp_path)
    assert r["instancias"] == []


def test_pasta_ja_registrada_nao_entra_duas_vezes(tmp_path):
    pasta = tmp_path / "A_TIR1"
    pasta.mkdir()
    r = inv.levantar(registradas=[_registro("A_TIR1", "A", pasta)],
                     ambientes={"A", "A_TIR1"}, online=True, base_path=tmp_path)
    assert [i["ambiente"] for i in r["instancias"]] == ["A_TIR1"]


# ── Espaço ──────────────────────────────────────────────────

def test_mede_pasta_do_ambiente_e_workspace_do_banco(tmp_path):
    """O MDF/LDF fica fora da pasta do ambiente: contar só uma delas
    esconderia a maior parte dos GB."""
    ambiente = tmp_path / "A_TIR1"
    (ambiente / "Protheus").mkdir(parents=True)
    (ambiente / "Protheus" / "a.rpo").write_bytes(b"x" * 100)
    workspace = tmp_path / "Downloads" / "A_TIR1"
    workspace.mkdir(parents=True)
    (workspace / "base.mdf").write_bytes(b"y" * 500)

    item = _registro("A_TIR1", "A", ambiente, workspace)
    r = inv.medir(inv.caminhos_ocupados(item))
    assert r["bytes"] == 600
    assert set(r["detalhe"]) == {str(ambiente), str(workspace)}


def test_temp_do_pai_nao_conta_como_espaco_da_instancia(tmp_path):
    """É do ambiente-pai, e é justamente o que deixa de ser duplicado."""
    ambiente = tmp_path / "A_TIR1"
    ambiente.mkdir()
    item = _registro("A_TIR1", "A", ambiente)
    item["temp_pai"] = str(tmp_path / "Downloads" / "A" / "temp")
    assert inv.caminhos_ocupados(item) == [str(ambiente)]


def test_medir_caminho_inexistente_nao_quebra(tmp_path):
    assert inv.medir([str(tmp_path / "nao-existe")])["bytes"] == 0


def test_resumo_conta_por_situacao(tmp_path):
    pasta = tmp_path / "A_TIR1"
    pasta.mkdir()
    r = inv.levantar(
        registradas=[_registro("A_TIR1", "A", pasta),
                     _registro("B_TIR1", "B", tmp_path / "sumiu")],
        ambientes={"A", "A_TIR1", "B", "B_TIR1"}, online=True)
    assert r["resumo"] == {inv.OK: 1, inv.FANTASMA: 1}


def test_caminho_da_pasta_vai_para_a_tela(tmp_path):
    """A tela mostra os caminhos no `title`: é o que o usuário confere antes
    de mandar apagar."""
    pasta = Path(tmp_path) / "A_TIR1"
    pasta.mkdir()
    r = inv.levantar(registradas=[_registro("A_TIR1", "A", pasta)],
                     ambientes={"A"}, online=True)
    assert r["instancias"][0]["caminhos"] == [str(pasta)]
