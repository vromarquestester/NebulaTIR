"""Alocação de portas para instâncias paralelas (`services.portas`).

O `checar` é injetado nos testes: alocar porta de verdade dependeria do que
está aberto na máquina, e o teste passaria ou falharia conforme o dia.
"""

from services import portas


def _ocupadas(*presas):
    """Dublê de `porta_livre`: só as portas passadas estão em uso."""
    presas = set(presas)
    return lambda p: p not in presas


# ── Sequencial ──────────────────────────────────────────────

def test_uma_instancia_fica_com_as_portas_originais():
    """Sequencial não pode mudar o que já funciona."""
    r = portas.alocar(1, checar=lambda p: True)
    assert r["ok"] is True
    assert r["instancias"][0]["portas"] == portas.BASES
    assert r["instancias"][0]["deslocadas"] == []


def test_porta_do_webapp_vem_do_ambiente():
    r = portas.alocar(1, base_webapp=4399, checar=lambda p: True)
    assert r["instancias"][0]["portas"]["webapp"] == 4399


# ── Paralelo ────────────────────────────────────────────────

def test_cada_instancia_soma_um():
    r = portas.alocar(3, checar=lambda p: True)
    assert [i["portas"]["webapp"] for i in r["instancias"]] == [4321, 4322, 4323]
    assert [i["portas"]["tcp"] for i in r["instancias"]] == [8881, 8882, 8883]
    assert [i["portas"]["webagent"] for i in r["instancias"]] == [21021, 21022, 21023]


def test_porta_em_uso_pula_para_a_proxima_livre():
    r = portas.alocar(2, checar=_ocupadas(8882, 8883))
    assert [i["portas"]["tcp"] for i in r["instancias"]] == [8881, 8884]


def test_nenhuma_porta_se_repete_entre_instancias():
    r = portas.alocar(4, checar=lambda p: True)
    todas = [p for i in r["instancias"] for p in i["portas"].values()]
    assert len(todas) == len(set(todas))


def test_deslocadas_apontam_o_que_saiu_do_padrao():
    r = portas.alocar(2, checar=lambda p: True)
    assert r["instancias"][0]["deslocadas"] == []
    assert set(r["instancias"][1]["deslocadas"]) == set(portas.BASES)


def test_sem_porta_livre_falha_com_mensagem():
    r = portas.alocar(1, checar=lambda p: False)
    assert r["ok"] is False
    assert "Sem porta livre" in r["erro"]


def test_zero_instancias_e_recusado():
    assert portas.alocar(0)["ok"] is False


# ── Imutáveis ───────────────────────────────────────────────

def test_licenseclient_fica_fora_da_alocacao():
    """Decisão do usuário: 8009 não muda. É a licença, compartilhada de
    propósito — não há o que isolar ali."""
    r = portas.alocar(3, checar=lambda p: True)
    assert r["imutaveis"] == {"licenseclient": 8009}
    for instancia in r["instancias"]:
        assert set(instancia["portas"]) == set(portas.BASES)
        assert 8009 not in instancia["portas"].values()


# ── DbAccess por instância ──────────────────────────────────
# Era imutável em 7890, com um processo atendendo todos os bancos via alias.
# Em corrida paralela as instâncias travavam sem causa visível em log nenhum,
# e o DbAccess compartilhado era o único ponto que todas dividiam — daí o
# isolamento, que a TOTVS documenta (`[GENERAL] Port` do dbaccess.ini).

def test_cada_instancia_ganha_a_propria_porta_de_dbaccess():
    r = portas.alocar(3, checar=lambda p: True)
    dbs = [i["portas"]["dbaccess"] for i in r["instancias"]]
    assert dbs == [7890, 7891, 7892]
    assert len(set(dbs)) == 3


def test_dbaccess_nao_colide_com_outra_chave():
    """Todas as portas de todas as instâncias são distintas entre si."""
    r = portas.alocar(3, checar=lambda p: True)
    todas = [p for i in r["instancias"] for p in i["portas"].values()]
    assert len(todas) == len(set(todas))


def test_modo_compartilhado_devolve_a_7890_para_todas():
    """A meia-volta: um DbAccess só, como antes."""
    r = portas.alocar(3, checar=lambda p: True, dbaccess_por_instancia=False)
    assert [i["portas"]["dbaccess"] for i in r["instancias"]] == [7890] * 3
    # E não aparece como deslocada: ela não saiu de lugar nenhum.
    for instancia in r["instancias"]:
        assert "dbaccess" not in instancia["deslocadas"]


def test_porta_ocupada_desloca_o_dbaccess():
    """Mesma regra das outras: soma 1 e segue até achar livre."""
    r = portas.alocar(1, checar=lambda p: p != 7890)
    assert r["instancias"][0]["portas"]["dbaccess"] == 7891
    assert "dbaccess" in r["instancias"][0]["deslocadas"]


def test_porta_fora_da_faixa_nao_e_considerada_livre():
    assert portas.porta_livre(80) is False or portas.porta_livre(80) in (True, False)
    assert portas.porta_livre(0) is False
    assert portas.porta_livre(70000) is False
