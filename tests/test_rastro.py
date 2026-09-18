"""Rastro do `debug-*.log` (`services.rastro` + `log_bridge.instalar_debug`).

O arquivo de debug é o que o diagnóstico anexa: tem que registrar ação da
API com argumentos e tempo, evento da tela, subprocesso com PID, e nunca
deixar senha passar. E nada dele pode vazar para a tela nem para o
`nebula-*.log`, que continuam iguais.
"""

import logging
import subprocess
import sys

import pytest

from services import rastro
from webui import log_bridge


@pytest.fixture
def capturado(caplog):
    caplog.set_level(logging.DEBUG, logger="rastro")
    return caplog


def test_acao_e_resultado_mascaram_segredo(capturado):
    rastro.acao("salvar_configuracao", nome="PAR", config={"User": "ADMIN", "Password": "1234"})
    rastro.resultado("salvar_configuracao", {"ok": True, "extra": 1}, 12.3)
    texto = capturado.text
    assert "[AÇÃO] salvar_configuracao" in texto
    assert "'Password': '***'" in texto and "1234" not in texto
    assert "→ {'ok': True} (12 ms)" in texto


def test_etapa_registra_falha_e_repropaga(capturado):
    with pytest.raises(ValueError):
        with rastro.etapa("clonar", ambiente="PAR"):
            raise ValueError("boom")
    assert "[ETAPA] ▶ clonar ambiente='PAR'" in capturado.text
    assert "✗ clonar falhou" in capturado.text and "ValueError: boom" in capturado.text


def test_rastrear_classe_envolve_publicos_e_poupa_polling(capturado):
    class Falsa:
        def executar(self, nome, rotinas=None):
            return {"ok": True, "rotinas": 3}

        def get_status(self):
            return {"ok": True}

        def _interno(self):
            return 1

    rastro.rastrear_classe(Falsa)
    f = Falsa()
    assert f.executar("PAR", rotinas=["A"]) == {"ok": True, "rotinas": 3}
    f.get_status()
    f._interno()
    linhas = [r.getMessage() for r in capturado.records]
    assert any("[AÇÃO] executar args=('PAR',) rotinas=['A']" in l for l in linhas)
    assert any("[AÇÃO] executar → {'ok': True}" in l for l in linhas)
    assert not any("get_status" in l or "_interno" in l for l in linhas)


def test_rastreado_registra_excecao(capturado):
    class Falsa:
        def quebra(self):
            raise RuntimeError("x")
    rastro.rastrear_classe(Falsa)
    with pytest.raises(RuntimeError):
        Falsa().quebra()
    assert "[AÇÃO] quebra ✗ RuntimeError: x" in capturado.text


def test_gancho_subprocesso_registra_pid_e_saida(capturado, monkeypatch):
    monkeypatch.setattr(rastro, "_gancho_instalado", False)
    init, wait = subprocess.Popen.__init__, subprocess.Popen.wait
    try:
        rastro.instalar_gancho_subprocesso()
        proc = subprocess.run([sys.executable, "-c", "print(1)"], capture_output=True)
    finally:
        subprocess.Popen.__init__, subprocess.Popen.wait = init, wait
    assert proc.returncode == 0
    assert f"[PROC] ▶ PID {proc.args and ''}" in capturado.text or "[PROC] ▶ PID" in capturado.text
    assert "saiu com 0" in capturado.text


def test_logger_rastro_nao_passa_pelo_filtro_da_aplicacao():
    """Nada do rastro chega à tela nem ao `nebula-*.log`."""
    filtro = log_bridge.FiltroAplicacao()
    registro = logging.LogRecord("rastro", logging.DEBUG, "", 0, "x", None, None)
    assert filtro.filter(registro) is False


def test_instalar_debug_cria_arquivo_e_limpeza_alcanca_os_dois(tmp_path, monkeypatch):
    import os, time
    monkeypatch.setattr(log_bridge, "pasta_de_log", lambda: tmp_path)
    monkeypatch.setattr(rastro, "instalar_gancho_subprocesso", lambda: None)
    handler = log_bridge.instalar_debug()
    try:
        rastro.ui("clique", {"id": "btn-executar"})
        handler.flush()
        arquivos = list(tmp_path.glob("debug-*.log"))
        assert len(arquivos) == 1
        conteudo = arquivos[0].read_text(encoding="utf-8")
        assert "[NOTA] programa aberto" in conteudo
        assert "[UI] clique id='btn-executar'" in conteudo
    finally:
        logging.getLogger().removeHandler(handler)
        handler.close()

    velho = tmp_path / "debug-20200101.log"
    velho.write_text("x")
    antigo = time.time() - (log_bridge.DIAS_GUARDADOS + 1) * 86400
    os.utime(velho, (antigo, antigo))
    assert log_bridge._limpar_antigos() == 1
    assert not velho.exists() and arquivos[0].exists()
