"""Pacote de diagnóstico (`services.diagnostico` + `Api.gerar_diagnostico`)."""

import json
import zipfile
from pathlib import Path

from services import diagnostico


def _programa(tmp_path):
    prog = tmp_path / "prog"
    (prog / "logs").mkdir(parents=True)
    (prog / "logs" / "nebula-20260918.log").write_text("n", encoding="utf-8")
    (prog / "logs" / "debug-20260918.log").write_text("d", encoding="utf-8")
    import os, time
    for i in range(8):
        velho = prog / "logs" / f"debug-2026090{i}.log"
        velho.write_text("velho", encoding="utf-8")
        os.utime(velho, (time.time() - 86400 * (10 - i),) * 2)
    corrida = prog / "tests" / "PAR_2510" / "MATA465N"
    (corrida / "log").mkdir(parents=True)
    (corrida / "config.json").write_text(json.dumps(
        {"Url": "http://x", "User": "ADMIN", "Password": "1234"}), encoding="utf-8")
    (corrida / "log" / "TIR_x.log").write_text("log", encoding="utf-8")
    (corrida / "log" / "tela.png").write_bytes(b"png")
    (corrida / "MATA465NTESTSUITE.py").write_text("# fonte", encoding="utf-8")
    (corrida / "nebula_run.py").write_text("# lançador", encoding="utf-8")
    return prog


def test_pacote_reune_logs_config_corrida_e_mascara_senha(tmp_path):
    prog = _programa(tmp_path)
    prefs = prog / "config" / "preferencias.json"
    prefs.parent.mkdir()
    prefs.write_text("{}", encoding="utf-8")
    tela = tmp_path / "tela.bmp"
    tela.write_bytes(b"BM")

    zip_path = diagnostico.gerar_pacote(
        prog / "diag.zip", pasta_programa=prog, pasta_logs=prog / "logs",
        arquivos_config=[prefs, prog / "config" / "nao_existe.json"],
        texto_ambiente="NebulaTIR x", print_tela=tela)

    with zipfile.ZipFile(zip_path) as z:
        nomes = set(z.namelist())
        assert "ambiente.txt" in nomes and "tela.bmp" in nomes
        assert "config/preferencias.json" in nomes
        assert "logs/nebula-20260918.log" in nomes
        # Só os 6 debug mais recentes.
        assert len([n for n in nomes if n.startswith("logs/debug-")]) == diagnostico.MAX_LOGS
        assert "ultima_corrida/PAR_2510/MATA465N/config.json" in nomes
        assert "ultima_corrida/PAR_2510/MATA465N/log/TIR_x.log" in nomes
        assert "ultima_corrida/PAR_2510/MATA465N/log/tela.png" in nomes
        # Fonte do teste e lançador não entram.
        assert not any(n.endswith(".py") for n in nomes)
        config = json.loads(z.read("ultima_corrida/PAR_2510/MATA465N/config.json"))
        assert config["Password"] == "***" and config["User"] == "ADMIN"


def test_arquivo_gigante_da_corrida_e_pulado(tmp_path, monkeypatch):
    prog = _programa(tmp_path)
    monkeypatch.setattr(diagnostico, "MAX_BYTES_ARQUIVO", 2)
    zip_path = diagnostico.gerar_pacote(
        prog / "diag.zip", pasta_programa=prog, pasta_logs=prog / "logs",
        arquivos_config=[], texto_ambiente="x")
    with zipfile.ZipFile(zip_path) as z:
        nomes = z.namelist()
        assert "ultima_corrida/PAR_2510/MATA465N/log/TIR_x.log" not in nomes
        assert "TIR_x.log" in z.read("ultima_corrida/PULADOS.txt").decode()


def test_ultima_corrida_e_a_mais_recente(tmp_path):
    prog = _programa(tmp_path)
    outra = prog / "tests" / "PAR_2610" / "MATA010"
    outra.mkdir(parents=True)
    import os, time
    antigo = time.time() - 3600
    os.utime(prog / "tests" / "PAR_2510" / "MATA465N", (antigo, antigo))
    assert diagnostico.ultima_corrida(prog / "tests") == outra
    assert diagnostico.ultima_corrida(tmp_path / "nada") is None


def test_coletar_ambiente_le_o_status_da_tela(tmp_path, monkeypatch):
    from services import appservers, venv_tir
    monkeypatch.setattr(appservers, "dono_da_porta",
                        lambda p: {"pid": 7, "exe": "C:/T/.dyncall.exe"} if p == 4321
                        else {"pid": 0, "exe": ""})
    monkeypatch.setattr(venv_tir, "provisionador", lambda: "python")
    monkeypatch.setattr(venv_tir, "versao_instalada", lambda: "2.14.8")
    monkeypatch.setattr(diagnostico, "_saida", lambda cmd, timeout=20: "(tasklist)")
    estado = {"link": True, "gerenciador_versao": "2.9.0", "vpn": True,
              "ambientes": {"PAR_2510": {"estado": "running", "port": "4321",
                                         "porta_compartilhada": True}},
              "instancias": ["PAR_2510_1: pai=PAR_2510"]}
    texto = diagnostico.coletar_ambiente("0.4.2", tmp_path, estado)
    assert "provisionador: python" in texto
    assert "tir_framework: 2.14.8" in texto
    assert "versão: 2.9.0" in texto
    assert "PAR_2510: estado=running porta=4321" in texto
    assert "4321: PID 7 C:/T/.dyncall.exe" in texto and "7890: livre" in texto
    assert "PAR_2510_1: pai=PAR_2510" in texto


def test_api_gerar_diagnostico(tmp_path, monkeypatch):
    from services import captura, recursos
    from webui import log_bridge
    from webui.api import Api
    prog = _programa(tmp_path)
    monkeypatch.setattr(recursos, "pasta_do_programa", lambda: prog)
    api = Api(arquivo_importados=tmp_path / "importados.json",
              iniciar_monitores=False, instalar_log=False,
              arquivo_preferencias=tmp_path / "preferencias.json",
              arquivo_instancias=tmp_path / "instancias.json")
    monkeypatch.setattr(log_bridge, "pasta_de_log", lambda: prog / "logs")
    monkeypatch.setattr(captura, "capturar_janela", lambda titulo, destino: None)
    revelados = []
    monkeypatch.setattr(diagnostico, "revelar_no_explorer", revelados.append)
    monkeypatch.setattr(diagnostico, "_saida", lambda cmd, timeout=20: "")

    r = api.gerar_diagnostico()

    assert r["ok"], r
    assert Path(r["arquivo"]).parent == prog and revelados == [Path(r["arquivo"])]
    with zipfile.ZipFile(r["arquivo"]) as z:
        assert "ambiente.txt" in z.namelist()
        assert "logs/debug-20260918.log" in z.namelist()


def test_config_importada_sai_mascarada(tmp_path):
    prog = _programa(tmp_path)
    imp = prog / "config" / "ambientes_importados.json"
    imp.parent.mkdir(exist_ok=True)
    imp.write_text(json.dumps({"ambientes": [{"nome": "PAR", "config": {"Password": "1234"}}]}),
                   encoding="utf-8")
    zip_path = diagnostico.gerar_pacote(
        prog / "diag.zip", pasta_programa=prog, pasta_logs=prog / "logs",
        arquivos_config=[imp], texto_ambiente="x")
    with zipfile.ZipFile(zip_path) as z:
        dados = json.loads(z.read("config/ambientes_importados.json"))
    assert dados["ambientes"][0]["config"]["Password"] == "***"
