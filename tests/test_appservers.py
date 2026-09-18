"""Subida dos AppServer das instâncias paralelas (`services.appservers`).

Quem sobe é o NebulaTIR e não o Gerenciador — só assim o PID fica registrado
aqui, e só com o PID dá para parar uma instância sem derrubar as outras.
"""

import subprocess
import sys
from pathlib import Path

import pytest

from services import appservers
from services.instancias import Instancias


@pytest.fixture(autouse=True)
def sem_espera(monkeypatch):
    """Nenhum teste pode depender de porta de verdade nem esperar de verdade."""
    monkeypatch.setattr(appservers, "ESPERA_SUBIDA_SEG", 0.2)
    monkeypatch.setattr(appservers, "INTERVALO_SONDA_SEG", 0)
    monkeypatch.setattr(appservers, "ESPERA_PORTA_SEG", 0.5)
    monkeypatch.setattr(appservers, "porta_responde", lambda *a, **k: True)


def _exe_que_fica_vivo(tmp_path, nome="appserver.exe"):
    """Dublê do AppServer: um script que dorme, no lugar do executável."""
    pasta = tmp_path / "bin"
    pasta.mkdir(parents=True, exist_ok=True)
    alvo = pasta / nome
    alvo.write_text("", encoding="utf-8")
    return alvo


def test_exe_inexistente_avisa(tmp_path):
    r = appservers.subir(str(tmp_path / "nao-existe.exe"))
    assert r["ok"] is False
    assert "não encontrado" in r["erro"]


def test_processo_que_morre_na_partida_e_falha(tmp_path, monkeypatch):
    """Porta ocupada ou .ini inválido matam o AppServer em segundos; devolver
    'ok' faria o erro aparecer lá na frente, longe da causa.

    A janela aqui é generosa de propósito, e não custa os 10 s: `subir` espera
    **até** o limite, então volta assim que o script morre. Com os 0,2 s da
    fixture o teste media o tempo de partida do interpretador, não o
    comportamento — no runner do GitHub o processo ainda estava nascendo
    quando a janela fechava, e o natimorto passava por vivo.
    """
    monkeypatch.setattr(appservers, "ESPERA_SUBIDA_SEG", 10.0)
    script = tmp_path / "morre.py"
    script.write_text("import sys; sys.exit(3)\n", encoding="utf-8")
    r = appservers.subir(sys.executable, str(script))
    assert r["ok"] is False
    assert "encerrou" in r["erro"]


def test_processo_vivo_devolve_pid(tmp_path):
    script = tmp_path / "vive.py"
    script.write_text("import time; time.sleep(30)\n", encoding="utf-8")
    r = appservers.subir(sys.executable, str(script))
    assert r["ok"] is True
    assert r["pid"] > 0
    import subprocess
    subprocess.run(["taskkill", "/F", "/PID", str(r["pid"])],
                   capture_output=True)


# ── Orquestração ────────────────────────────────────────────

# ── Prontidão: o defeito da segunda corrida real ────────────

def test_espera_a_porta_responder_antes_de_liberar(tmp_path, monkeypatch):
    """Processo vivo não basta: o AppServer nasce em instantes e leva dezenas
    de segundos para publicar o WebApp. Liberar o teste antes disso dá
    `connectionFailure` no navegador — foi o que aconteceu."""
    reg = Instancias(tmp_path / "instancias.json")
    reg.registrar(ambiente="A_TIR1", origem="A", slot=1, banco="B1",
                  portas={"webapp": 4321})

    tentativas = {"n": 0}

    def _responde(porta, host="127.0.0.1", timeout=1.0):
        tentativas["n"] += 1
        return tentativas["n"] >= 3      # responde só na terceira sonda
    monkeypatch.setattr(appservers, "porta_responde", _responde)
    monkeypatch.setattr(appservers, "INTERVALO_SONDA_SEG", 0)
    monkeypatch.setattr(appservers.appserver_ini, "aplicar_portas",
                        lambda ini, portas: {"ok": True})
    monkeypatch.setattr(appservers, "subir",
                        lambda exe, params="": {"ok": True, "pid": 555})

    r = appservers.subir_para_instancias(
        reg.listar("A"), reg,
        lambda n: {"ok": True, "banco": {"appserver_exe": "x"}},
        dbaccess_por_instancia=False)
    assert [s["ambiente"] for s in r["subidos"]] == ["A_TIR1"]
    assert tentativas["n"] >= 3


def test_porta_que_nunca_responde_vira_erro(tmp_path, monkeypatch):
    reg = Instancias(tmp_path / "instancias.json")
    reg.registrar(ambiente="A_TIR1", origem="A", slot=1, banco="B1",
                  portas={"webapp": 4321})
    monkeypatch.setattr(appservers, "porta_responde", lambda *a, **k: False)
    monkeypatch.setattr(appservers, "INTERVALO_SONDA_SEG", 0)
    monkeypatch.setattr(appservers, "ESPERA_PORTA_SEG", 0.2)
    monkeypatch.setattr(appservers.appserver_ini, "aplicar_portas",
                        lambda ini, portas: {"ok": True})
    monkeypatch.setattr(appservers, "subir",
                        lambda exe, params="": {"ok": True, "pid": 555})

    r = appservers.subir_para_instancias(
        reg.listar("A"), reg,
        lambda n: {"ok": True, "banco": {"appserver_exe": "x"}},
        dbaccess_por_instancia=False)
    assert r["subidos"] == []
    assert "não respondeu" in r["erros"][0]["erro"]


def test_esperar_porta_respeita_o_pedido_de_parada(monkeypatch):
    monkeypatch.setattr(appservers, "porta_responde", lambda *a, **k: False)
    monkeypatch.setattr(appservers, "INTERVALO_SONDA_SEG", 0)
    r = appservers.esperar_porta(4321, limite_seg=5, parar=lambda: True)
    assert r["ok"] is False
    assert "Interrompido" in r["erro"]


def test_escreve_as_portas_antes_de_subir(tmp_path, monkeypatch):
    """Sem isso, todo clone fica com [TCP] 8881 e só o primeiro sobe."""
    reg = Instancias(tmp_path / "instancias.json")
    reg.registrar(ambiente="A_TIR2", origem="A", slot=2, banco="B2",
                  portas={"webapp": 4322, "tcp": 8882, "httprest": 8081})

    escritas = {}
    monkeypatch.setattr(appservers.appserver_ini, "aplicar_portas",
                        lambda ini, portas: escritas.update(portas) or {"ok": True})
    monkeypatch.setattr(appservers, "subir",
                        lambda exe, params="": {"ok": True, "pid": 777})

    appservers.subir_para_instancias(
        reg.listar("A"), reg,
        lambda n: {"ok": True, "banco": {"appserver_exe": "x"}},
        dbaccess_por_instancia=False)
    assert escritas["tcp"] == 8882
    assert escritas["httprest"] == 8081


def test_falha_ao_escrever_o_ini_impede_a_subida(tmp_path, monkeypatch):
    reg = Instancias(tmp_path / "instancias.json")
    reg.registrar(ambiente="A_TIR1", origem="A", slot=1, banco="B1",
                  portas={"webapp": 4321})
    monkeypatch.setattr(appservers.appserver_ini, "aplicar_portas",
                        lambda ini, portas: {"ok": False, "erro": "ini sumiu"})
    monkeypatch.setattr(appservers, "subir",
                        lambda *a, **k: pytest.fail("subiu com ini errado"))
    r = appservers.subir_para_instancias(
        reg.listar("A"), reg,
        lambda n: {"ok": True, "banco": {"appserver_exe": "x"}},
        dbaccess_por_instancia=False)
    assert r["erros"][0]["erro"] == "ini sumiu"


# ── DbAccess ────────────────────────────────────────────────

def test_dbaccess_ja_no_ar_nao_e_tocado(monkeypatch):
    """Um DbAccess atende todos os bancos; subir outro mataria as instâncias
    que já estão rodando."""
    monkeypatch.setattr(appservers, "dbaccess_no_ar", lambda porta=0: True)
    monkeypatch.setattr(appservers.subprocess, "Popen",
                        lambda *a, **k: pytest.fail("subiu um segundo DbAccess"))
    r = appservers.garantir_dbaccess("qualquer.exe")
    assert r["ok"] is True
    assert r["subiu"] is False
    assert r["porta"] == 7890


def test_dbaccess_e_decidido_pela_porta_nao_pelo_nome(tmp_path, monkeypatch):
    """Corrida das 14:12 de 2026-09-18: o DbAccess isolado do clone (7891)
    estava vivo, o `tasklist` achava `dbaccess64.exe` e o pai ficava sem o
    seu na 7890 — `Falha ao conectar no DbAccess`. Agora é a porta que diz."""
    exe = tmp_path / "dbaccess64.exe"
    exe.write_bytes(b"")
    (tmp_path / "dbaccess.ini").write_text("[General]" + chr(10) + "Port=7890" + chr(10), encoding="latin-1")
    respondem = {7891}
    monkeypatch.setattr(appservers, "porta_responde",
                        lambda porta, host="127.0.0.1", timeout=1.0: porta in respondem)
    monkeypatch.setattr(appservers, "INTERVALO_SONDA_SEG", 0)

    class ProcFalso:
        pid = 77
        def poll(self):
            return None
        def wait(self, timeout=None):
            raise subprocess.TimeoutExpired("duble", timeout)

    def _popen(cmd, **k):
        respondem.add(7890)       # subiu: a porta passa a responder
        return ProcFalso()
    monkeypatch.setattr(appservers.subprocess, "Popen", _popen)
    monkeypatch.setattr(appservers.time, "sleep", lambda s: None)
    # Nunca consulta o tasklist: a decisão é pela porta.
    monkeypatch.setattr(appservers.subprocess, "run",
                        lambda *a, **k: pytest.fail("consultou o tasklist"))

    r = appservers.garantir_dbaccess(str(exe))
    assert r["ok"] is True and r["subiu"] is True and r["porta"] == 7890


def test_dbaccess_inexistente_avisa(monkeypatch, tmp_path):
    monkeypatch.setattr(appservers, "dbaccess_no_ar", lambda porta=0: False)
    r = appservers.garantir_dbaccess(str(tmp_path / "nao-existe.exe"))
    assert r["ok"] is False
    assert "não encontrado" in r["erro"]


def test_anota_o_pid_de_cada_instancia(tmp_path, monkeypatch):
    reg = Instancias(tmp_path / "instancias.json")
    reg.registrar(ambiente="A_TIR1", origem="A", slot=1, banco="B1",
                  portas={"webapp": 4322})
    reg.registrar(ambiente="A_TIR2", origem="A", slot=2, banco="B2",
                  portas={"webapp": 4323})

    monkeypatch.setattr(appservers.appserver_ini, "aplicar_portas",
                        lambda ini, portas: {"ok": True})
    monkeypatch.setattr(appservers, "subir",
                        lambda exe, params="": {"ok": True, "pid": 900 + len(exe)})
    detalhes = lambda nome: {"ok": True, "banco": {"appserver_exe": nome}}

    r = appservers.subir_para_instancias(reg.listar("A"), reg, detalhes,
                                        dbaccess_por_instancia=False)
    assert [s["ambiente"] for s in r["subidos"]] == ["A_TIR1", "A_TIR2"]
    assert reg.por_nome("A_TIR1")["pids"]["appserver"] > 0
    assert reg.por_nome("A_TIR2")["pids"]["appserver"] > 0


def test_instancia_ja_no_ar_e_reaproveitada(tmp_path, monkeypatch):
    reg = Instancias(tmp_path / "instancias.json")
    reg.registrar(ambiente="A_TIR1", origem="A", slot=1, banco="B1", portas={})
    reg.anotar_pid("A_TIR1", "appserver", 4321)

    monkeypatch.setattr(appservers, "subir",
                        lambda *a, **k: pytest.fail("subiu de novo"))
    itens = reg.listar("A")
    itens[0]["vivos"] = {"appserver": True}
    r = appservers.subir_para_instancias(itens, reg, lambda n: {"ok": True},
                                        dbaccess_por_instancia=False)
    assert r["subidos"][0]["reaproveitado"] is True


def test_falha_numa_nao_impede_as_outras(tmp_path, monkeypatch):
    reg = Instancias(tmp_path / "instancias.json")
    for i in (1, 2):
        reg.registrar(ambiente=f"A_TIR{i}", origem="A", slot=i,
                      banco=f"B{i}", portas={"webapp": 4320 + i})

    def _subir(exe, params=""):
        return {"ok": False, "erro": "porta ocupada"} if "TIR1" in exe \
            else {"ok": True, "pid": 777}
    monkeypatch.setattr(appservers.appserver_ini, "aplicar_portas",
                        lambda ini, portas: {"ok": True})
    monkeypatch.setattr(appservers, "subir", _subir)
    detalhes = lambda nome: {"ok": True, "banco": {"appserver_exe": nome}}

    r = appservers.subir_para_instancias(reg.listar("A"), reg, detalhes,
                                        dbaccess_por_instancia=False)
    assert [e["ambiente"] for e in r["erros"]] == ["A_TIR1"]
    assert [s["ambiente"] for s in r["subidos"]] == ["A_TIR2"]


# ── Janela de console por processo ──────────────────────────
# Processo calado vira fantasma: sobra DbAccess de pé com o ambiente marcado
# como parado, segurando arquivo e derrubando a exclusão do ambiente sem que
# dê para ver quem é. Com a janela na tela, cada instância é visível.

def test_appserver_sobe_com_console(tmp_path, monkeypatch):
    exe = tmp_path / "appserver.exe"
    exe.write_bytes(b"")
    chamadas = []

    class ProcFalso:
        pid = 4242

        def poll(self):
            return None

        def wait(self, timeout=None):
            # Vivo: `subir` espera ate o limite e desiste. Mesmo contrato do
            # Popen real, que levanta TimeoutExpired em vez de devolver.
            raise subprocess.TimeoutExpired("duble", timeout)

    monkeypatch.setattr(appservers.subprocess, "Popen",
                        lambda cmd, **k: chamadas.append((cmd, k)) or ProcFalso())
    monkeypatch.setattr(appservers.time, "sleep", lambda s: None)

    assert appservers.subir(str(exe))["ok"] is True
    cmd, kwargs = chamadas[0]
    assert cmd[-1] == "-console"
    # Console novo, não janela escondida: é a diferença entre ver e não ver.
    assert kwargs["creationflags"] == appservers._COM_CONSOLE
    assert kwargs["creationflags"] != appservers._SEM_JANELA


def test_dbaccess_sobe_com_console(tmp_path, monkeypatch):
    exe = tmp_path / "dbaccess64.exe"
    exe.write_bytes(b"")
    chamadas = []

    class ProcFalso:
        pid = 99

        def poll(self):
            return None

        def wait(self, timeout=None):
            # Vivo: `subir` espera ate o limite e desiste. Mesmo contrato do
            # Popen real, que levanta TimeoutExpired em vez de devolver.
            raise subprocess.TimeoutExpired("duble", timeout)

    monkeypatch.setattr(appservers, "dbaccess_no_ar", lambda porta=0: False)
    monkeypatch.setattr(appservers.subprocess, "Popen",
                        lambda cmd, **k: chamadas.append((cmd, k)) or ProcFalso())
    monkeypatch.setattr(appservers.time, "sleep", lambda s: None)
    monkeypatch.setattr(appservers, "esperar_porta", lambda porta, **k: {"ok": True})

    assert appservers.garantir_dbaccess(str(exe))["ok"] is True
    cmd, kwargs = chamadas[0]
    assert cmd[-1] == "-console"
    assert kwargs["creationflags"] == appservers._COM_CONSOLE


def test_nao_duplica_console_ja_configurado():
    """O parâmetro pode já vir do ambiente, com hífen ou com barra."""
    assert appservers._params_com_console("-console") == ["-console"]
    assert appservers._params_com_console("/console") == ["/console"]
    assert appservers._params_com_console("-CONSOLE") == ["-CONSOLE"]


def test_preserva_os_parametros_do_ambiente():
    assert appservers._params_com_console("-env=prod -q") == \
        ["-env=prod", "-q", "-console"]
    assert appservers._params_com_console("") == ["-console"]


# ── DbAccess por instância ──────────────────────────────────
# Hipótese em teste: em corrida paralela as instâncias travavam sem causa
# visível em nenhum log, e o DbAccess compartilhado era o único ponto que
# todas dividiam. A TOTVS documenta várias instâncias na mesma máquina, cada
# uma em outra porta.

class _ProcVivo:
    pid = 4242

    def poll(self):
        return None

    def wait(self, timeout=None):
        # Vivo: `subir` espera ate o limite e desiste. Mesmo contrato do
        # Popen real, que levanta TimeoutExpired em vez de devolver.
        raise subprocess.TimeoutExpired("duble", timeout)


def test_sobe_um_dbaccess_na_porta_da_instancia(tmp_path, monkeypatch):
    exe = tmp_path / "dbaccess64.exe"
    exe.write_bytes(b"")
    chamadas = []
    monkeypatch.setattr(appservers, "porta_responde",
                        lambda p, **k: p == 7891)   # livre antes, no ar depois
    monkeypatch.setattr(appservers.subprocess, "Popen",
                        lambda cmd, **k: chamadas.append((cmd, k)) or _ProcVivo())

    # A porta ainda não pode estar respondendo quando ele decide subir.
    respostas = iter([False, True, True])
    monkeypatch.setattr(appservers, "porta_responde",
                        lambda p, **k: next(respostas, True))

    r = appservers.subir_dbaccess_da_instancia(str(exe), 7891)
    assert r["ok"] is True
    assert r["porta"] == 7891
    assert r["pid"] == 4242

    cmd, kwargs = chamadas[0]
    assert "-p7891" in cmd
    assert "-console" in cmd
    assert kwargs["creationflags"] == appservers._COM_CONSOLE


def test_nao_sobe_se_a_porta_ja_responde(tmp_path, monkeypatch):
    """Subir por cima criaria dois processos brigando pela mesma porta."""
    exe = tmp_path / "dbaccess64.exe"
    exe.write_bytes(b"")
    monkeypatch.setattr(appservers, "porta_responde", lambda p, **k: True)
    monkeypatch.setattr(appservers.subprocess, "Popen",
                        lambda *a, **k: pytest.fail("subiu por cima"))

    r = appservers.subir_dbaccess_da_instancia(str(exe), 7891)
    assert r["ok"] is True
    assert r["subiu"] is False


def test_instancia_sem_porta_de_dbaccess_e_recusada(tmp_path):
    exe = tmp_path / "dbaccess64.exe"
    exe.write_bytes(b"")
    r = appservers.subir_dbaccess_da_instancia(str(exe), 0)
    assert r["ok"] is False
    assert "sem porta" in r["erro"].lower()


def test_dbaccess_vem_antes_do_appserver(tmp_path, monkeypatch):
    """Sem DbAccess o AppServer sobe, publica a porta e trava ao abrir o
    ambiente — o login fica pendurado até o timeout, sem erro claro."""
    reg = Instancias(tmp_path / "instancias.json")
    reg.registrar(ambiente="A_TIR1", origem="A", slot=1, banco="B1",
                  portas={"webapp": 4321, "dbaccess": 7890})

    ordem = []
    monkeypatch.setattr(appservers.appserver_ini, "aplicar_portas",
                        lambda ini, portas: {"ok": True})
    monkeypatch.setattr(appservers.appserver_ini, "desativar_webmonitor",
                        lambda ini: {"ok": True})
    monkeypatch.setattr(appservers.dbaccess_ini, "aplicar_porta",
                        lambda ini, porta: ordem.append(("ini_db", porta))
                        or {"ok": True})
    monkeypatch.setattr(appservers, "subir_dbaccess_da_instancia",
                        lambda exe, porta, params="":
                        ordem.append(("dbaccess", porta))
                        or {"ok": True, "pid": 111, "porta": porta})
    monkeypatch.setattr(appservers, "subir",
                        lambda exe, params="": ordem.append(("appserver", 0))
                        or {"ok": True, "pid": 222})
    monkeypatch.setattr(appservers, "porta_responde", lambda *a, **k: True)

    r = appservers.subir_para_instancias(
        reg.listar("A"), reg,
        lambda n: {"ok": True, "banco": {"appserver_exe": "x",
                                         "dbaccess_exe": "y"}})
    assert r["subidos"][0]["ambiente"] == "A_TIR1"
    assert [passo for passo, _ in ordem] == ["ini_db", "dbaccess", "appserver"]
    # O PID do DbAccess é registrado: é por ele que "Parar selecionados" mata
    # só o desta instância, sem tocar nas vizinhas.
    assert reg.por_nome("A_TIR1")["pids"]["dbaccess"] == 111


def test_dbaccess_que_nao_sobe_impede_o_appserver(tmp_path, monkeypatch):
    reg = Instancias(tmp_path / "instancias.json")
    reg.registrar(ambiente="A_TIR1", origem="A", slot=1, banco="B1",
                  portas={"webapp": 4321, "dbaccess": 7890})

    monkeypatch.setattr(appservers.appserver_ini, "aplicar_portas",
                        lambda ini, portas: {"ok": True})
    monkeypatch.setattr(appservers.appserver_ini, "desativar_webmonitor",
                        lambda ini: {"ok": True})
    monkeypatch.setattr(appservers.dbaccess_ini, "aplicar_porta",
                        lambda ini, porta: {"ok": True})
    monkeypatch.setattr(appservers, "subir_dbaccess_da_instancia",
                        lambda *a, **k: {"ok": False, "erro": "porta ocupada"})
    monkeypatch.setattr(appservers, "subir",
                        lambda *a, **k: pytest.fail("subiu sem DbAccess"))

    r = appservers.subir_para_instancias(
        reg.listar("A"), reg,
        lambda n: {"ok": True, "banco": {"appserver_exe": "x",
                                         "dbaccess_exe": "y"}})
    assert r["subidos"] == []
    assert "porta ocupada" in r["erros"][0]["erro"]


# ── Quem está na porta ─────────────────────────────────────
# Dois ambientes cadastrados na mesma porta: "responde" não diz qual está de
# pé. O dono é o processo LISTENING do netstat.

NETSTAT = """
Conexões ativas

  Proto  Endereço local         Endereço externo       Estado           PID
  TCP    0.0.0.0:135            0.0.0.0:0              LISTENING       1234
  TCP    0.0.0.0:4321           0.0.0.0:0              LISTENING       5566
  TCP    127.0.0.1:4321         127.0.0.1:50000        ESTABLISHED     5566
  TCP    127.0.0.1:14321        0.0.0.0:0              LISTENING       9999
  TCP    [::]:4322              [::]:0                 LISTENING       7788
"""


def test_pid_no_netstat_acha_a_linha_listening():
    assert appservers.pid_no_netstat(NETSTAT, 4321) == 5566


def test_pid_no_netstat_nao_confunde_sufixo():
    """`:4321` não pode casar com `:14321`."""
    assert appservers.pid_no_netstat(NETSTAT, 321) == 0


def test_pid_no_netstat_ipv6():
    assert appservers.pid_no_netstat(NETSTAT, 4322) == 7788


def test_pid_no_netstat_porta_sem_ninguem():
    assert appservers.pid_no_netstat(NETSTAT, 8080) == 0
    assert appservers.pid_no_netstat("", 4321) == 0


def test_mesmo_executavel_ignora_caixa_e_barra():
    assert appservers.mesmo_executavel(r"C:\TOTVS\PAR\appserver.exe",
                                       "c:/totvs/par/APPSERVER.EXE")
    assert not appservers.mesmo_executavel(r"C:\A\appserver.exe",
                                           r"C:\B\appserver.exe")
    assert not appservers.mesmo_executavel("", r"C:\B\appserver.exe")


def test_dono_da_porta_junta_pid_e_exe(monkeypatch):
    monkeypatch.setattr(appservers, "pid_escutando", lambda porta: 5566)
    monkeypatch.setattr(appservers, "exe_do_pid",
                        lambda pid: r"C:\T\appserver.exe" if pid == 5566 else "")
    assert appservers.dono_da_porta(4321) == {"pid": 5566,
                                              "exe": r"C:\T\appserver.exe"}
    monkeypatch.setattr(appservers, "pid_escutando", lambda porta: 0)
    assert appservers.dono_da_porta(4321) == {"pid": 0, "exe": ""}


@pytest.mark.skipif(sys.platform != "win32", reason="netstat/ctypes do Windows")
def test_exe_do_pid_do_proprio_processo():
    import os
    exe = appservers.exe_do_pid(os.getpid())
    assert exe.lower().endswith(("python.exe", "pythonw.exe", "python3.exe"))


def test_mesma_pasta_do_appserver_aceita_o_dyncall_filho():
    """Quem escuta a porta do WebApp é o `.dyncall.exe`, não o appserver.exe."""
    assert appservers.mesma_pasta_do_appserver(
        "C:/TOTVS/PAR_2510/Protheus/bin/appserver/.dyncall.exe",
        "c:/totvs/par_2510/protheus/bin/appserver/APPSERVER.EXE")
    assert not appservers.mesma_pasta_do_appserver(
        "C:/TOTVS/PAR_2610/Protheus/bin/appserver/.dyncall.exe",
        "C:/TOTVS/PAR_2510/Protheus/bin/appserver/appserver.exe")
    assert not appservers.mesma_pasta_do_appserver("", "C:/T/appserver.exe")
    assert not appservers.mesma_pasta_do_appserver("C:/T/.dyncall.exe", "")


def test_instancia_sobe_com_o_webagent_do_pai(tmp_path, monkeypatch):
    """Registro antigo guardou webagent=21022 (deslocado); o agente da estação
    escuta na 21021 do pai. O clone tem que subir apontando para a do pai —
    senão o WebApp fica em "Tentando se conectar ao WebAgent" (2026-09-18)."""
    reg = Instancias(tmp_path / "instancias.json")
    reg.registrar(ambiente="A_TIR1", origem="A", slot=1, banco="B1",
                  portas={"webapp": 4322, "webagent": 21022})
    pai = tmp_path / "A" / "Protheus" / "bin" / "appserver"
    pai.mkdir(parents=True)
    (pai / "appserver.ini").write_text("[WEBAGENT]\nPort=21021\n", encoding="latin-1")
    clone = tmp_path / "A_TIR1" / "Protheus" / "bin" / "appserver"
    clone.mkdir(parents=True)
    (clone / "appserver.ini").write_text("[WEBAPP]\nport=4321\n[WEBAGENT]\nPort=21021\n",
                                         encoding="latin-1")
    monkeypatch.setattr(appservers, "porta_responde", lambda *a, **k: True)
    monkeypatch.setattr(appservers, "subir", lambda exe, params="": {"ok": True, "pid": 5})
    monkeypatch.setattr(appservers, "garantir_webagent", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(appservers.webapp, "garantir", lambda *a, **k: {"ok": True})

    def detalhes(nome):
        pasta = pai if nome == "A" else clone
        return {"ok": True, "banco": {"appserver_exe": str(pasta / "appserver.exe")}}

    r = appservers.subir_para_instancias(reg.listar("A"), reg, detalhes,
                                         dbaccess_por_instancia=False)
    assert [s["ambiente"] for s in r["subidos"]] == ["A_TIR1"]
    gravadas = appservers.appserver_ini.ler_portas(clone / "appserver.ini")
    assert int(gravadas["webagent"]) == 21021
    assert int(gravadas["webapp"]) == 4322
