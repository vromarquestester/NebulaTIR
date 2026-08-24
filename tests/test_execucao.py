"""Corrida do TIR (`services.execucao`).

Nada aqui sobe Protheus nem navegador: o lançador é trocado por um script
Python de mentira que grava log e png, e o Gerenciador por um dublê. O que
está sob teste é o **ciclo** — preparar, executar, restaurar, seguir — e o
aborto.
"""

import queue
from pathlib import Path

import pytest

from services import config_tir, execucao, preparacao


class GerenciadorFalso:
    """Dublê do EstadoGerenciador: registra os pedidos de restauração."""

    def __init__(self, falhar=False):
        self.restauracoes = []
        self.esperas = 0
        self.falhar = falhar

    def restaurar_banco(self, ambiente):
        self.restauracoes.append(ambiente)
        if self.falhar:
            return {"ok": False, "erro": "Gerenciador offline."}
        return {"ok": True}

    def esperar_ocioso(self, limite_seg=3600, parar=None):
        self.esperas += 1
        return {"ok": True}


@pytest.fixture
def cenario(tmp_path, monkeypatch):
    """Pasta do programa + fontes, com um lançador que não abre navegador."""
    prog = tmp_path / "programa"
    prog.mkdir()
    anexos = tmp_path / "anexos"
    (anexos / "services" / "tir").mkdir(parents=True)
    (anexos / "resources").mkdir()

    # "Lançador": grava um .log e um .png na pasta indicada pelo config e sai
    # com o código pedido pelo nome do suite. Honra `--config` como o real —
    # em paralelo a pasta é compartilhada e cada instância tem o seu arquivo.
    (anexos / "services" / "tir" / "nebula_run.py").write_text(
        "import json, sys\n"
        "from pathlib import Path\n"
        "alvo = (sys.argv[sys.argv.index('--config') + 1]\n"
        "        if '--config' in sys.argv else 'config.json')\n"
        "cfg = json.loads(Path(alvo).read_text(encoding='utf-8'))\n"
        "pasta = Path(cfg['LogFolder'])\n"
        "pasta.mkdir(parents=True, exist_ok=True)\n"
        "(pasta / 'execucao.log').write_text('log', encoding='utf-8')\n"
        "(pasta / 'relatorio.png').write_bytes(b'PNG')\n"
        "print('[falso] rodou', sys.argv[1])\n"
        "sys.exit(1 if 'FALHA' in sys.argv[1] else 0)\n",
        encoding="utf-8")
    for nome in preparacao.ANEXOS:
        alvo = anexos / "services" / "tir" / nome
        if not alvo.exists():
            alvo.write_text("", encoding="utf-8")
    for nome in preparacao.ANEXOS_ASSETS:
        alvo = anexos / "services" / "tir" / nome
        alvo.parent.mkdir(parents=True, exist_ok=True)
        alvo.write_bytes(b"fonte")

    monkeypatch.setattr(preparacao, "pasta_do_programa", lambda: prog)
    monkeypatch.setattr(
        preparacao, "recurso",
        lambda *p: anexos.joinpath(*(p[1:] if p and p[0] == "src" else p)))
    # O venv do TIR é o próprio interpretador do teste.
    import sys
    monkeypatch.setattr(execucao.venv_tir, "python_do_venv",
                        lambda: Path(sys.executable))
    return prog


def _rotina(tmp_path, nome="COMA222"):
    fontes = tmp_path / "fontes" / nome
    fontes.mkdir(parents=True, exist_ok=True)
    (fontes / f"{nome}TESTSUITE.py").write_text("", encoding="utf-8")
    (fontes / f"{nome}TESTCASE.py").write_text("", encoding="utf-8")
    return {"rotina": nome, "modulo": "SIGACOM", "casos": ["test_1"],
            "suite": str(fontes / f"{nome}TESTSUITE.py"),
            "case": str(fontes / f"{nome}TESTCASE.py")}


def _config():
    return config_tir.padrao_para(url="http://127.0.0.1:4321/",
                                  ambiente_ini="environment", navegador="Chrome")


def _rodar(rotinas, gerenciador=None, **kw):
    eventos = queue.Queue()
    corrida = execucao.Execucao(
        ambiente="AMB", rotinas=rotinas, config=_config(),
        estado_gerenciador=gerenciador or GerenciadorFalso(),
        fila_eventos=eventos, **kw)
    corrida.iniciar()
    for t in corrida._threads:
        t.join(timeout=60)
    return corrida, eventos


# ── Ciclo ───────────────────────────────────────────────────

def test_roda_a_rotina_e_registra_log_e_png(cenario, tmp_path):
    corrida, _ = _rodar([_rotina(tmp_path)])
    item = corrida.instantaneo()["rotinas"][0]
    assert item["estado"] == execucao.OK
    assert item["log"].endswith("execucao.log")
    assert item["png"].endswith("relatorio.png")


def test_restaura_o_banco_depois_de_cada_rotina(cenario, tmp_path):
    """O ciclo pedido: executa, gera log e png, restaura, próxima."""
    ger = GerenciadorFalso()
    _rodar([_rotina(tmp_path, "R1"), _rotina(tmp_path, "R2")], ger)
    assert ger.restauracoes == ["AMB", "AMB"]
    assert ger.esperas == 2      # esperou o Gerenciador terminar, das duas vezes


def test_sequencial_roda_uma_de_cada_vez(cenario, tmp_path):
    corrida, _ = _rodar([_rotina(tmp_path, "R1"), _rotina(tmp_path, "R2")],
                        instancias=1)
    assert corrida.instancias == 1
    assert {i["estado"] for i in corrida.instantaneo()["rotinas"]} == {execucao.OK}


def test_falha_do_teste_nao_para_a_fila(cenario, tmp_path):
    """Teste que falha é resultado, não acidente: a fila continua."""
    corrida, _ = _rodar([_rotina(tmp_path, "FALHA1"), _rotina(tmp_path, "R2")])
    por_nome = {i["rotina"]: i for i in corrida.instantaneo()["rotinas"]}
    assert por_nome["FALHA1"]["estado"] == execucao.FALHOU
    assert por_nome["R2"]["estado"] == execucao.OK


def test_rotina_sem_fonte_e_marcada_sem_derrubar_o_resto(cenario, tmp_path):
    quebrada = dict(_rotina(tmp_path, "SUMIU"), suite=str(tmp_path / "nao-existe.py"))
    corrida, _ = _rodar([quebrada, _rotina(tmp_path, "R2")])
    por_nome = {i["rotina"]: i for i in corrida.instantaneo()["rotinas"]}
    assert por_nome["SUMIU"]["estado"] == execucao.FALHOU
    assert por_nome["R2"]["estado"] == execucao.OK


def test_falha_ao_restaurar_nao_derruba_a_corrida(cenario, tmp_path):
    corrida, eventos = _rodar([_rotina(tmp_path)], GerenciadorFalso(falhar=True))
    assert corrida.instantaneo()["rotinas"][0]["estado"] == execucao.OK
    textos = []
    while not eventos.empty():
        textos.append(eventos.get()["text"])
    assert any("restauração" in t.lower() for t in textos)


# ── Aborto ──────────────────────────────────────────────────

def test_abortar_esvazia_a_fila_e_marca_pendentes(cenario, tmp_path):
    eventos = queue.Queue()
    rotinas = [_rotina(tmp_path, f"R{i}") for i in range(1, 6)]
    corrida = execucao.Execucao(
        ambiente="AMB", rotinas=rotinas, config=_config(),
        estado_gerenciador=GerenciadorFalso(), fila_eventos=eventos)
    corrida.abortar()          # aborta antes de iniciar: fila inteira pendente
    corrida.iniciar()
    for t in corrida._threads:
        t.join(timeout=30)

    estados = {i["estado"] for i in corrida.instantaneo()["rotinas"]}
    assert estados == {execucao.ABORTADO}
    assert corrida.instantaneo()["abortada"] is True


# ── Paralelo ────────────────────────────────────────────────

def test_paralelo_restaura_o_banco_de_cada_instancia(cenario, tmp_path):
    """Restaurar entre rotinas é obrigatório também em paralelo: sem isso a
    instância que liberar primeiro pega a base no estado que o teste anterior
    deixou, e o resultado deixa de valer."""
    ger = GerenciadorFalso()
    eventos = queue.Queue()
    corrida = execucao.Execucao(
        ambiente="AMB", rotinas=[_rotina(tmp_path, f"R{i}") for i in range(1, 5)],
        config=_config(), estado_gerenciador=ger, fila_eventos=eventos,
        instancias=2, ambientes_por_slot=["AMB_TIR1", "AMB_TIR2"])
    corrida.iniciar()
    for t in corrida._threads:
        t.join(timeout=60)

    assert len(ger.restauracoes) == 4
    assert set(ger.restauracoes) <= {"AMB_TIR1", "AMB_TIR2"}


def test_religa_o_ambiente_depois_de_restaurar(cenario, tmp_path):
    """A restauração encerra o AppServer daquele ambiente (o pipeline precisa
    liberar os arquivos da base). Sem religar, a próxima rotina do mesmo slot
    encontraria a porta fechada."""
    religados = []
    eventos = queue.Queue()
    corrida = execucao.Execucao(
        ambiente="AMB", rotinas=[_rotina(tmp_path, "R1"), _rotina(tmp_path, "R2")],
        config=_config(), estado_gerenciador=GerenciadorFalso(),
        fila_eventos=eventos, instancias=1,
        religar_ambiente=lambda a: religados.append(a) or {"ok": True})
    corrida.iniciar()
    for t in corrida._threads:
        t.join(timeout=60)

    assert religados == ["AMB", "AMB"]


def test_nao_religa_quando_a_restauracao_falha(cenario, tmp_path):
    """Religar sobre um banco que não voltou esconderia o problema."""
    religados = []
    eventos = queue.Queue()
    corrida = execucao.Execucao(
        ambiente="AMB", rotinas=[_rotina(tmp_path)], config=_config(),
        estado_gerenciador=GerenciadorFalso(falhar=True), fila_eventos=eventos,
        religar_ambiente=lambda a: religados.append(a) or {"ok": True})
    corrida.iniciar()
    for t in corrida._threads:
        t.join(timeout=60)

    assert religados == []



def test_cada_slot_usa_o_proprio_ambiente(cenario, tmp_path):
    """Em paralelo o banco é por instância; restaurar o do principal não
    adiantaria nada para o slot que rodou."""
    ger = GerenciadorFalso()
    rotinas = [_rotina(tmp_path, f"R{i}") for i in range(1, 5)]
    eventos = queue.Queue()
    corrida = execucao.Execucao(
        ambiente="AMB", rotinas=rotinas, config=_config(),
        estado_gerenciador=ger, fila_eventos=eventos, instancias=2,
        ambientes_por_slot=["AMB_TIR1", "AMB_TIR2"])
    corrida.iniciar()
    for t in corrida._threads:
        t.join(timeout=60)

    assert set(ger.restauracoes) <= {"AMB_TIR1", "AMB_TIR2"}
    assert len(ger.restauracoes) == 4
    assert {i["estado"] for i in corrida.instantaneo()["rotinas"]} == {execucao.OK}


def test_ambiente_do_slot_cai_no_principal_em_sequencial(cenario, tmp_path):
    corrida, _ = _rodar([_rotina(tmp_path)])
    assert corrida.ambiente_do_slot(1) == "AMB"
    assert corrida.ambiente_do_slot(9) == "AMB"     # slot fora da lista


def test_tudo_na_pasta_do_ambiente_base(cenario, tmp_path):
    """Uma árvore só, por rotina. Separar por instância espalharia o mesmo
    trabalho em pastas quase idênticas, e o resultado do teste não pertence à
    instância que calhou de rodá-lo."""
    eventos = queue.Queue()
    corrida = execucao.Execucao(
        ambiente="AMB", rotinas=[_rotina(tmp_path, "R1"), _rotina(tmp_path, "R2")],
        config=_config(), estado_gerenciador=GerenciadorFalso(),
        fila_eventos=eventos, instancias=2,
        ambientes_por_slot=["AMB_TIR1", "AMB_TIR2"])
    corrida.iniciar()
    for t in corrida._threads:
        t.join(timeout=60)

    criadas = {p.name for p in (cenario / "tests").iterdir()}
    assert criadas == {"AMB"}
    rotinas = {p.name for p in (cenario / "tests" / "AMB").iterdir()}
    assert rotinas == {"R1", "R2"}


def test_config_de_cada_pasta_e_o_da_instancia_que_rodou(cenario, tmp_path):
    """A pasta é do ambiente base, mas a URL é a da instância que executou."""
    import json
    eventos = queue.Queue()
    corrida = execucao.Execucao(
        ambiente="AMB", rotinas=[_rotina(tmp_path, "R1")], config=_config(),
        estado_gerenciador=GerenciadorFalso(), fila_eventos=eventos,
        instancias=1, ambientes_por_slot=["AMB_TIR1"],
        config_por_ambiente={"AMB_TIR1": {**_config(),
                                          "Url": "http://127.0.0.1:4322/"}})
    corrida.iniciar()
    for t in corrida._threads:
        t.join(timeout=60)

    # Pasta do ambiente base, arquivo nomeado pela instância que rodou.
    config = json.loads(
        (cenario / "tests" / "AMB" / "R1" / "config.AMB_TIR1.json")
        .read_text(encoding="utf-8"))
    assert config["Url"] == "http://127.0.0.1:4322/"


def test_abortar_encerra_a_corrida_de_imediato(cenario, tmp_path):
    """Abortar tem que liberar o Executar TIR na hora.

    O lançador deixa geckodriver e Firefox para trás, e a leitura da saída
    pode ficar pendurada. Amarrar `ativa` só à vida das threads deixava o
    botão desabilitado para sempre depois de um aborto.
    """
    eventos = queue.Queue()
    corrida = execucao.Execucao(
        ambiente="AMB", rotinas=[_rotina(tmp_path, f"R{i}") for i in range(1, 4)],
        config=_config(), estado_gerenciador=GerenciadorFalso(),
        fila_eventos=eventos)

    class ThreadPresa:
        def is_alive(self):
            return True
    corrida._threads = [ThreadPresa()]
    assert corrida.ativa is True

    corrida.abortar()
    assert corrida.ativa is False
    assert corrida.instantaneo()["ativa"] is False


def test_abortar_mata_a_arvore_de_processos(cenario, tmp_path, monkeypatch):
    """`proc.kill()` mata só o lançador; o navegador que ele abriu sobrevive e
    segura o stdout, pendurando a leitura."""
    comandos = []

    def _run(cmd, **kwargs):
        comandos.append(cmd)
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()
    monkeypatch.setattr(execucao.subprocess, "run", _run)

    class ProcFalso:
        pid = 4242

        def poll(self):
            return None

        def kill(self):
            pass

    eventos = queue.Queue()
    corrida = execucao.Execucao(
        ambiente="AMB", rotinas=[_rotina(tmp_path)], config=_config(),
        estado_gerenciador=GerenciadorFalso(), fila_eventos=eventos)
    corrida._processos = {"R1": ProcFalso()}

    r = corrida.abortar()
    assert r["mortos"] == 1
    assert ["taskkill", "/F", "/T", "/PID", "4242"] in comandos


# ── Divisão de casos entre instâncias ───────────────────────

def test_rotina_dividida_vira_varias_unidades(cenario, tmp_path):
    """Cada unidade é um item da fila; o banco só é restaurado quando a
    última terminar — restaurar entre casos da mesma rotina não faz sentido."""
    ger = GerenciadorFalso()
    rotina = _rotina(tmp_path, "R1")
    rotina["casos"] = ["test_A", "test_B", "test_C"]
    rotina["unidades"] = [["test_A"], ["test_B"], ["test_C"]]

    eventos = queue.Queue()
    corrida = execucao.Execucao(
        ambiente="AMB", rotinas=[rotina], config=_config(),
        estado_gerenciador=ger, fila_eventos=eventos, instancias=2)
    corrida.iniciar()
    for t in corrida._threads:
        t.join(timeout=60)

    # Três execuções do lançador, uma restauração só.
    assert ger.restauracoes == ["AMB"]
    item = corrida.instantaneo()["rotinas"][0]
    assert item["dividida"] is True
    assert item["partes"] == 3


def test_rotina_inteira_continua_com_uma_unidade(cenario, tmp_path):
    ger = GerenciadorFalso()
    rotina = _rotina(tmp_path, "R1")
    rotina["unidades"] = [["test_A", "test_B"]]
    eventos = queue.Queue()
    corrida = execucao.Execucao(
        ambiente="AMB", rotinas=[rotina], config=_config(),
        estado_gerenciador=ger, fila_eventos=eventos)
    corrida.iniciar()
    for t in corrida._threads:
        t.join(timeout=60)

    assert ger.restauracoes == ["AMB"]
    assert corrida.instantaneo()["rotinas"][0]["dividida"] is False


def test_instantaneo_conta_concluidas(cenario, tmp_path):
    corrida, _ = _rodar([_rotina(tmp_path, "R1"), _rotina(tmp_path, "R2")])
    foto = corrida.instantaneo()
    assert foto["total"] == 2
    assert foto["concluidas"] == 2
    assert foto["ativa"] is False


# ── Árvore de acompanhamento ao vivo ────────────────────────
# O resultado da suite só existe quando ela termina, e cada caso do TIR leva
# minutos. Sem isto o painel fica parado por meia hora sem dizer o que roda.

def test_progresso_do_lancador_alimenta_a_arvore(cenario, tmp_path):
    """`[nebula_caso] INICIO/FIM` vira estado por caso, por instância."""
    eventos = queue.Queue()
    corrida = execucao.Execucao(
        ambiente="AMB", rotinas=[_rotina(tmp_path, "R1")], config=_config(),
        estado_gerenciador=GerenciadorFalso(), fila_eventos=eventos)
    corrida._slot_assume(1, "R1", ["test_A", "test_B"])

    corrida._progresso(1, "[nebula_caso] INICIO test_A")
    inst = corrida.instantaneo()["arvore"][0]
    assert inst["ambiente"] == "AMB"
    assert inst["rotina"] == "R1"
    assert inst["caso"] == "test_A"
    assert [c["estado"] for c in inst["casos"]] == ["rodando", "fila"]

    corrida._progresso(1, "[nebula_caso] FIM test_A ok")
    corrida._progresso(1, "[nebula_caso] INICIO test_B")
    corrida._progresso(1, "[nebula_caso] FIM test_B erro")
    inst = corrida.instantaneo()["arvore"][0]
    assert [c["estado"] for c in inst["casos"]] == ["ok", "erro"]
    assert inst["caso"] == ""


def test_caso_pendurado_nao_fica_girando_para_sempre(cenario, tmp_path):
    """Lançador morto no meio deixa um caso em 'rodando'. Liberar o slot
    fecha esse estado — senão o círculo azul pulsa até o programa fechar."""
    eventos = queue.Queue()
    corrida = execucao.Execucao(
        ambiente="AMB", rotinas=[_rotina(tmp_path, "R1")], config=_config(),
        estado_gerenciador=GerenciadorFalso(), fila_eventos=eventos)
    corrida._slot_assume(1, "R1", ["test_A"])
    corrida._progresso(1, "[nebula_caso] INICIO test_A")
    corrida._slot_libera(1)

    inst = corrida.instantaneo()["arvore"][0]
    assert inst["casos"][0]["estado"] == execucao.FALHOU
    assert inst["estado"] == "ocioso"


def test_arvore_separa_as_instancias(cenario, tmp_path):
    """Duas instâncias, dois ambientes: cada uma com a própria subárvore."""
    eventos = queue.Queue()
    corrida = execucao.Execucao(
        ambiente="AMB", rotinas=[_rotina(tmp_path, "R1")], config=_config(),
        estado_gerenciador=GerenciadorFalso(), fila_eventos=eventos,
        instancias=2, ambientes_por_slot=["AMB_TIR1", "AMB_TIR2"])
    corrida._slot_assume(1, "R1", ["test_A"])
    corrida._slot_assume(2, "R2", ["test_B"])

    por_ambiente = {i["ambiente"]: i for i in corrida.instantaneo()["arvore"]}
    assert por_ambiente["AMB_TIR1"]["rotina"] == "R1"
    assert por_ambiente["AMB_TIR2"]["rotina"] == "R2"


def test_progresso_nao_polui_o_console(cenario, tmp_path):
    """A árvore já mostra caso a caso; repetir no log afogaria o resto."""
    corrida, eventos = _rodar([_rotina(tmp_path)])
    textos = []
    while not eventos.empty():
        textos.append(eventos.get()["text"])
    assert not any(execucao.MARCA_PROGRESSO in t for t in textos)


# ── Config por instância na rotina dividida ─────────────────
# Defeito real: MATA143 tem 2 casos, foi dividida em 2 instâncias, e as duas
# rodaram na mesma pasta escrevendo `config.json` uma por cima da outra. As
# duas acabaram com a URL da última a gravar — os dois navegadores no MESMO
# AppServer, com "RPOs divergentes" e "REST could not be initialized!" no
# console, enquanto o outro AppServer ficava ocioso. Como o vencedor da
# corrida de escrita muda a cada corrida, o sintoma era intermitente.

def test_fatias_da_mesma_rotina_nao_dividem_o_config(cenario, tmp_path):
    import json

    rotina = _rotina(tmp_path, "R1")
    rotina["casos"] = ["test_A", "test_B"]
    rotina["unidades"] = [["test_A"], ["test_B"]]

    eventos = queue.Queue()
    corrida = execucao.Execucao(
        ambiente="AMB", rotinas=[rotina], config=_config(),
        estado_gerenciador=GerenciadorFalso(), fila_eventos=eventos,
        instancias=2, ambientes_por_slot=["AMB_TIR1", "AMB_TIR2"],
        config_por_ambiente={
            "AMB_TIR1": {**_config(), "Url": "http://127.0.0.1:4321/"},
            "AMB_TIR2": {**_config(), "Url": "http://127.0.0.1:4322/"},
        })
    corrida.iniciar()
    for t in corrida._threads:
        t.join(timeout=60)

    pasta = cenario / "tests" / "AMB" / "R1"
    urls = {json.loads(p.read_text(encoding="utf-8"))["Url"]
            for p in pasta.glob("config.*.json")}
    # Uma URL por instância, e nenhuma perdida por sobrescrita.
    assert urls == {"http://127.0.0.1:4321/", "http://127.0.0.1:4322/"}


def test_lancador_recebe_o_config_explicito(cenario, tmp_path, monkeypatch):
    """A pasta é compartilhada: sem `--config`, a busca automática do TIR
    acharia o arquivo de outra instância."""
    comandos = []
    original = execucao.subprocess.Popen

    def espiao(cmd, **kwargs):
        comandos.append((cmd, kwargs))
        return original(cmd, **kwargs)

    monkeypatch.setattr(execucao.subprocess, "Popen", espiao)

    rotina = _rotina(tmp_path, "R1")
    rotina["unidades"] = [["test_A"]]
    eventos = queue.Queue()
    corrida = execucao.Execucao(
        ambiente="AMB", rotinas=[rotina], config=_config(),
        estado_gerenciador=GerenciadorFalso(), fila_eventos=eventos,
        instancias=1, ambientes_por_slot=["AMB_TIR1"],
        config_por_ambiente={"AMB_TIR1": _config()})
    corrida.iniciar()
    for t in corrida._threads:
        t.join(timeout=60)

    cmd, kwargs = comandos[0]
    assert "--config" in cmd
    caminho = cmd[cmd.index("--config") + 1]
    assert caminho.endswith("config.AMB_TIR1.json")
    # E pela variável de ambiente também: o `tir_report` relê o config para
    # descobrir o LogFolder, e a busca dele parte da pasta compartilhada.
    assert kwargs["env"]["TIR_CONFIG"] == caminho


def test_sequencial_mantem_config_json(cenario, tmp_path):
    """Sem paralelo não há disputa; mudar o nome só atrapalharia quem abre a
    pasta para conferir."""
    _rodar([_rotina(tmp_path, "R1")])
    pasta = cenario / "tests" / "AMB" / "R1"
    assert (pasta / "config.json").is_file()
    assert list(pasta.glob("config.*.json")) == []


# ── Teto de tempo por unidade ───────────────────────────────
# Sem isto o lançador fica preso para sempre. Na corrida de 14/08 o AppServer
# de uma instância parou de atender, o TIR entrou no ciclo de `restart_browser`
# — que não fecha o Firefox anterior — e o lançador ficou mais de uma hora
# tentando. O slot nunca liberou e cada tentativa deixava outro navegador.

def test_lancador_travado_e_encerrado_e_marcado(cenario, tmp_path, monkeypatch):
    monkeypatch.setattr(execucao, "LIMITE_UNIDADE_SEG", 0.5)
    monkeypatch.setattr(execucao, "INTERVALO_VIGIA_SEG", 0.05)

    # Lançador que nunca termina, como o que ficou preso no retry do TIR.
    (tmp_path / "anexos" / "services" / "tir" / "nebula_run.py").write_text(
        "import time\nwhile True: time.sleep(1)\n", encoding="utf-8")

    corrida, eventos = _rodar([_rotina(tmp_path, "TRAVA")])

    item = corrida.instantaneo()["rotinas"][0]
    assert item["estado"] == execucao.FALHOU
    assert "sem terminar" in item["mensagem"]

    textos = []
    while not eventos.empty():
        textos.append(eventos.get()["text"])
    assert any("Encerrando para liberar a instância" in t for t in textos)


def test_a_fila_segue_depois_do_estouro(cenario, tmp_path, monkeypatch):
    """O teto existe para liberar o slot, não para derrubar a corrida."""
    monkeypatch.setattr(execucao, "LIMITE_UNIDADE_SEG", 0.5)
    monkeypatch.setattr(execucao, "INTERVALO_VIGIA_SEG", 0.05)

    lancador = tmp_path / "anexos" / "services" / "tir" / "nebula_run.py"
    lancador.write_text(
        "import json, sys, time\n"
        "from pathlib import Path\n"
        "if 'TRAVA' in sys.argv[1]:\n"
        "    while True: time.sleep(1)\n"
        "alvo = (sys.argv[sys.argv.index('--config') + 1]\n"
        "        if '--config' in sys.argv else 'config.json')\n"
        "cfg = json.loads(Path(alvo).read_text(encoding='utf-8'))\n"
        "pasta = Path(cfg['LogFolder'])\n"
        "pasta.mkdir(parents=True, exist_ok=True)\n"
        "(pasta / 'execucao.log').write_text('log', encoding='utf-8')\n",
        encoding="utf-8")

    corrida, _ = _rodar([_rotina(tmp_path, "TRAVA"), _rotina(tmp_path, "BOA")],
                        instancias=1)
    por_nome = {i["rotina"]: i for i in corrida.instantaneo()["rotinas"]}
    assert por_nome["TRAVA"]["estado"] == execucao.FALHOU
    assert por_nome["BOA"]["estado"] == execucao.OK


def test_execucao_normal_nao_e_tocada_pelo_vigia(cenario, tmp_path):
    """O vigia só age no estouro; nada muda no caminho feliz."""
    corrida, _ = _rodar([_rotina(tmp_path, "R1")])
    assert corrida.instantaneo()["rotinas"][0]["estado"] == execucao.OK
    assert corrida._estourados == set()


# ── Visibilidade do paralelismo ─────────────────────────────
# Uma corrida rodou com uma instância a menos e ninguém percebeu: o painel só
# mostrava a instância que trabalhou, e o motivo da outra ter ficado de fora
# passou como uma linha solta no log da tela, que some ao fechar o programa.

def test_instancia_ociosa_aparece_na_arvore(cenario, tmp_path):
    """Esconder a ociosa faz o paralelo parecer sequencial."""
    eventos = queue.Queue()
    corrida = execucao.Execucao(
        ambiente="AMB", rotinas=[_rotina(tmp_path, "R1")], config=_config(),
        estado_gerenciador=GerenciadorFalso(), fila_eventos=eventos,
        instancias=2, ambientes_por_slot=["AMB_TIR1", "AMB_TIR2"])

    arvore = corrida.instantaneo()["arvore"]
    assert [i["ambiente"] for i in arvore] == ["AMB_TIR1", "AMB_TIR2"]
    assert all(i["rotina"] == "" for i in arvore)


def test_avisa_quando_ha_menos_trabalho_que_instancia(cenario, tmp_path):
    eventos = queue.Queue()
    corrida = execucao.Execucao(
        ambiente="AMB", rotinas=[_rotina(tmp_path, "R1")], config=_config(),
        estado_gerenciador=GerenciadorFalso(), fila_eventos=eventos,
        instancias=3, ambientes_por_slot=["A1", "A2", "A3"])
    corrida.iniciar()
    for t in corrida._threads:
        t.join(timeout=60)

    textos = []
    while not eventos.empty():
        textos.append(eventos.get()["text"])
    assert any("vão ficar" in t and "ociosas" in t for t in textos)


def test_duas_unidades_vao_para_instancias_diferentes(cenario, tmp_path):
    """O ponto do paralelo. Se as duas caírem no mesmo slot, não há ganho."""
    rotina = _rotina(tmp_path, "R1")
    rotina["casos"] = ["test_A", "test_B"]
    rotina["unidades"] = [["test_A"], ["test_B"]]

    eventos = queue.Queue()
    corrida = execucao.Execucao(
        ambiente="AMB", rotinas=[rotina], config=_config(),
        estado_gerenciador=GerenciadorFalso(), fila_eventos=eventos,
        instancias=2, ambientes_por_slot=["AMB_TIR1", "AMB_TIR2"],
        config_por_ambiente={"AMB_TIR1": _config(), "AMB_TIR2": _config()})
    corrida.iniciar()
    for t in corrida._threads:
        t.join(timeout=60)

    # Cada instância gravou o próprio config: prova de que as duas rodaram.
    pasta = cenario / "tests" / "AMB" / "R1"
    assert sorted(p.name for p in pasta.glob("config.*.json")) == [
        "config.AMB_TIR1.json", "config.AMB_TIR2.json"]
