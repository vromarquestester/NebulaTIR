"""Registro dos ambientes paralelos (`services.instancias`).

O que este registro protege: parar e excluir **só** o que o NebulaTIR criou.
O ambiente principal é do Gerenciador e não pode ser tocado.
"""

import json

import pytest

from services import instancias as mod
from services.instancias import Instancias


@pytest.fixture
def reg(tmp_path):
    return Instancias(tmp_path / "instancias.json")


def _registrar(reg, nome="PAR_2510_TIR1", origem="PAR_2510", slot=1):
    return reg.registrar(ambiente=nome, origem=origem, slot=slot,
                         banco=f"BANCO{slot}", portas={"webapp": 4320 + slot})


# ── Registro ────────────────────────────────────────────────

def test_registra_e_persiste(reg, tmp_path):
    assert _registrar(reg)["ok"] is True
    dados = json.loads((tmp_path / "instancias.json").read_text(encoding="utf-8"))
    assert dados["instancias"][0]["ambiente"] == "PAR_2510_TIR1"
    assert Instancias(tmp_path / "instancias.json").contem("PAR_2510_TIR1")


def test_nao_registra_duas_vezes(reg):
    _registrar(reg)
    assert _registrar(reg)["ok"] is False


def test_slots_nao_se_repetem_por_origem(reg):
    _registrar(reg, "A_TIR1", "A", 1)
    _registrar(reg, "A_TIR2", "A", 2)
    assert reg.proximo_slot("A") == 3
    assert reg.proximo_slot("B") == 1      # outra origem começa do 1


def test_lista_filtra_por_origem(reg):
    _registrar(reg, "A_TIR1", "A", 1)
    _registrar(reg, "B_TIR1", "B", 1)
    assert reg.nomes("A") == ["A_TIR1"]
    assert len(reg.nomes()) == 2


# ── Estado dos processos ────────────────────────────────────

def test_estado_confere_o_pid_no_ato(reg, monkeypatch):
    """PID guardado pode ter morrido entre uma abertura e outra."""
    _registrar(reg)
    reg.anotar_pid("PAR_2510_TIR1", "appserver", 4321)

    monkeypatch.setattr(mod, "_pid_vivo", lambda pid: True)
    assert reg.listar()[0]["estado"] == mod.RODANDO

    monkeypatch.setattr(mod, "_pid_vivo", lambda pid: False)
    assert reg.listar()[0]["estado"] == mod.PARADA


def test_instancia_nova_nasce_como_criada(reg, monkeypatch):
    monkeypatch.setattr(mod, "_pid_vivo", lambda pid: False)
    _registrar(reg)
    assert reg.listar()[0]["estado"] == mod.CRIADA


# ── Parar ───────────────────────────────────────────────────

def test_para_so_as_pedidas(reg, monkeypatch):
    """O ponto do registro: parar uma sem derrubar as outras."""
    mortos = []
    monkeypatch.setattr(mod, "_matar", lambda pid: mortos.append(pid) or True)
    for i in (1, 2, 3):
        _registrar(reg, f"A_TIR{i}", "A", i)
        reg.anotar_pid(f"A_TIR{i}", "appserver", 1000 + i)

    r = reg.parar(["A_TIR2"])
    assert [p["ambiente"] for p in r["parados"]] == ["A_TIR2"]
    assert mortos == [1002]


def test_parar_ignora_quem_nao_e_do_registro(reg, monkeypatch):
    """O ambiente principal não está aqui — e não pode ser parado por engano."""
    monkeypatch.setattr(mod, "_matar", lambda pid: pytest.fail("matou o que não devia"))
    r = reg.parar(["PAR_2510"])
    assert r["ignorados"] == ["PAR_2510"]
    assert r["parados"] == []


def test_parar_zera_os_pids(reg, monkeypatch):
    monkeypatch.setattr(mod, "_matar", lambda pid: True)
    monkeypatch.setattr(mod, "_pid_vivo", lambda pid: False)
    _registrar(reg)
    reg.anotar_pid("PAR_2510_TIR1", "appserver", 4321)
    reg.parar(["PAR_2510_TIR1"])
    assert reg.por_nome("PAR_2510_TIR1")["pids"]["appserver"] == 0
    assert reg.listar()[0]["estado"] == mod.PARADA


# ── Remover ─────────────────────────────────────────────────

def test_remove_do_registro(reg):
    _registrar(reg, "A_TIR1", "A", 1)
    _registrar(reg, "A_TIR2", "A", 2)
    assert reg.remover(["A_TIR1"])["removidos"] == 1
    assert reg.nomes("A") == ["A_TIR2"]


def test_arquivo_corrompido_nao_derruba(tmp_path):
    (tmp_path / "instancias.json").write_text("{ nao json", encoding="utf-8")
    assert Instancias(tmp_path / "instancias.json").nomes() == []


# ── Onde o registro mora ────────────────────────────────────
# Ficava ao lado do executável: trocar o `.exe` de pasta perdia o registro e
# deixava ambiente de GB no disco sem ninguém sabendo de onde veio.

def test_registro_vive_junto_dos_ambientes(tmp_path, monkeypatch):
    base = tmp_path / "TOTVS"
    monkeypatch.setattr(mod, "arquivo_legado",
                        lambda: tmp_path / "config" / mod.ARQUIVO_LEGADO)
    reg = Instancias(base_path=str(base))
    _registrar(reg)
    assert reg.arquivo == base / mod.PASTA_INSTANCIAS / mod.ARQUIVO
    assert reg.arquivo.exists()


def test_registro_antigo_e_migrado_uma_vez(tmp_path, monkeypatch):
    legado = tmp_path / "config" / mod.ARQUIVO_LEGADO
    monkeypatch.setattr(mod, "arquivo_legado", lambda: legado)
    base = tmp_path / "TOTVS"

    _registrar(Instancias(), "A_TIR1", "A", 1)
    assert legado.exists()

    reg = Instancias(base_path=str(base))
    assert reg.nomes() == ["A_TIR1"]
    assert (base / mod.PASTA_INSTANCIAS / mod.ARQUIVO).exists()
    # O antigo sai de circulação: outra instalação sem `base_path` reviveria
    # um registro já superado.
    assert not legado.exists()
    assert legado.with_suffix(legado.suffix + ".migrado").exists()


def test_registro_do_base_path_vence_o_antigo(tmp_path, monkeypatch):
    """Quem vive junto dos ambientes é mais recente que o que ficou para trás."""
    legado = tmp_path / "config" / mod.ARQUIVO_LEGADO
    monkeypatch.setattr(mod, "arquivo_legado", lambda: legado)
    base = tmp_path / "TOTVS"

    _registrar(Instancias(base_path=str(base)), "NOVO_TIR1", "NOVO", 1)
    _registrar(Instancias(), "VELHO_TIR1", "VELHO", 1)

    assert Instancias(base_path=str(base)).nomes() == ["NOVO_TIR1"]


def test_sem_base_path_o_registro_continua_local(tmp_path, monkeypatch):
    """Gerenciador fechado não pode apagar a memória do que já foi criado."""
    legado = tmp_path / "config" / mod.ARQUIVO_LEGADO
    monkeypatch.setattr(mod, "arquivo_legado", lambda: legado)
    reg = Instancias(base_path=lambda: "")
    _registrar(reg)
    assert reg.arquivo == legado
    assert reg.nomes() == ["PAR_2510_TIR1"]


def test_base_path_que_chega_depois_migra_sozinho(tmp_path, monkeypatch):
    """O `base_path` vem do Gerenciador, que só responde depois do boot."""
    legado = tmp_path / "config" / mod.ARQUIVO_LEGADO
    monkeypatch.setattr(mod, "arquivo_legado", lambda: legado)
    base = tmp_path / "TOTVS"
    atual = {"valor": ""}

    reg = Instancias(base_path=lambda: atual["valor"])
    _registrar(reg)
    assert reg.arquivo == legado

    atual["valor"] = str(base)
    assert reg.nomes() == ["PAR_2510_TIR1"]
    assert reg.arquivo == base / mod.PASTA_INSTANCIAS / mod.ARQUIVO


# ── Caminhos no disco ───────────────────────────────────────
# Sem eles, limpar uma instância cujo ambiente já saiu do Gerenciador vira
# adivinhação: ninguém mais sabe onde estão as pastas nem o banco anexado.

def test_registrar_guarda_os_caminhos(reg):
    reg.registrar(ambiente="A_TIR1", origem="A", slot=1, banco="B", portas={},
                  caminhos={"pasta": r"C:\TOTVS\NebulaInstancia\A_TIR1",
                            "temp_pai": r"C:\TOTVS\Downloads\A\temp",
                            "base_path": r"C:\TOTVS"})
    item = reg.por_nome("A_TIR1")
    assert item["pasta"] == r"C:\TOTVS\NebulaInstancia\A_TIR1"
    assert item["temp_pai"] == r"C:\TOTVS\Downloads\A\temp"


def test_campo_desconhecido_nao_entra_no_registro(reg):
    """Lista fechada: campo solto vindo do Gerenciador não vira registro."""
    reg.registrar(ambiente="A_TIR1", origem="A", slot=1, banco="B", portas={},
                  caminhos={"pasta": r"C:\X", "senha": "1234", "vazio": ""})
    item = reg.por_nome("A_TIR1")
    assert "senha" not in item
    assert "vazio" not in item


def test_anotar_caminhos_completa_registro_antigo(reg):
    _registrar(reg)
    assert reg.por_nome("PAR_2510_TIR1").get("pasta") is None

    r = reg.anotar_caminhos("PAR_2510_TIR1", {"pasta": r"C:\TOTVS\PAR_2510_TIR1"})
    assert r["alterado"] is True
    assert reg.por_nome("PAR_2510_TIR1")["pasta"] == r"C:\TOTVS\PAR_2510_TIR1"

    # Sem mudança não regrava: a listagem chama isso a cada abertura de tela.
    assert reg.anotar_caminhos(
        "PAR_2510_TIR1", {"pasta": r"C:\TOTVS\PAR_2510_TIR1"})["alterado"] is False
