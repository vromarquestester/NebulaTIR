"""`webapp.dll` da release provisionado pelo NebulaTIR (`services.webapp`).

A 2610 exige o WebApp 26 (`26.0.0-ts9`); com o embutido o navegador dá
ERR0003. Alvo e URL vêm do canal (bloco `webapp` do detalhe); baixar e trocar
é daqui, em cada instância — sem parar ambiente que já está no ar.
"""

import zipfile
from pathlib import Path

import pytest

from services import appservers, webapp
from services.instancias import Instancias


@pytest.fixture
def pasta_appserver(tmp_path):
    pasta = tmp_path / "PAR_2610" / "Protheus" / "bin" / "appserver"
    pasta.mkdir(parents=True)
    dll = pasta / "webapp.dll"
    dll.write_bytes(b"velha")
    dll.chmod(0o444)
    (pasta / "webapp").mkdir()
    (pasta / "webapp" / "webapp-10.1.8.min.css").write_text("css")
    return pasta


@pytest.fixture
def pacote(tmp_path):
    zip_path = tmp_path / "smartclientwebapp.zip"
    with zipfile.ZipFile(zip_path, "w") as z:
        z.writestr("webapp.dll", b"nova-26")
    return zip_path.as_uri()       # file:// — o urllib abre sem rede


def _bloco(alvo="26.0.0-ts9", url=""):
    return {"ok": True, "versao_protheus": "2610", "alvo": alvo,
            "url_zip": url, "instalado": "", "sincronizado": False}


def test_provisionar_troca_dll_aposenta_assets_e_marca(pasta_appserver, pacote):
    r = webapp.provisionar(pasta_appserver, "26.0.0-ts9", pacote)
    assert r["ok"], r
    assert (pasta_appserver / "webapp.dll").read_bytes() == b"nova-26"
    assert (pasta_appserver / "webapp.versao").read_text() == "26.0.0-ts9"
    assert not (pasta_appserver / "webapp").exists()
    assert r["assets_renomeados"].startswith(str(pasta_appserver / "webapp.bak-"))


def test_provisionar_sem_rede_devolve_erro_sem_levantar(pasta_appserver, tmp_path):
    r = webapp.provisionar(pasta_appserver, "26.0.0-ts9",
                           (tmp_path / "nao_existe.zip").as_uri())
    assert r["ok"] is False and "26.0.0-ts9" in r["erro"]
    assert (pasta_appserver / "webapp.dll").read_bytes() == b"velha"


def test_provisionar_pacote_sem_dll(pasta_appserver, tmp_path):
    zip_path = tmp_path / "vazio.zip"
    with zipfile.ZipFile(zip_path, "w") as z:
        z.writestr("leiame.txt", "x")
    r = webapp.provisionar(pasta_appserver, "26.0.0-ts9", zip_path.as_uri())
    assert r["ok"] is False and "webapp.dll" in r["erro"]


def test_garantir_sem_bloco_nao_confere(pasta_appserver):
    """Gerenciador anterior à chave: a corrida segue como antes."""
    assert webapp.garantir(None, pasta_appserver) == {"ok": True, "conferido": False}
    assert webapp.garantir({}, pasta_appserver)["conferido"] is False


def test_garantir_sem_alvo_e_a_2510(pasta_appserver):
    r = webapp.garantir(_bloco(alvo=""), pasta_appserver)
    assert r["conferido"] is True and r["trocado"] is False


def test_garantir_troca_quando_a_marca_diverge(pasta_appserver, pacote):
    r = webapp.garantir(_bloco(url=pacote), pasta_appserver, nome="PAR_2610_TIR1")
    assert r["trocado"] is True and r["de"] == "" and r["alvo"] == "26.0.0-ts9"
    # Segunda vez: já está — nada a fazer, nem download.
    r2 = webapp.garantir(_bloco(url="file:///nao/vai/ser/lido"), pasta_appserver)
    assert r2["trocado"] is False and "aviso" not in r2


def test_garantir_le_a_marca_da_pasta_e_nao_do_bloco(pasta_appserver, pacote):
    """O bloco diz `instalado=''` (do pai); o clone no disco já tem a marca."""
    (pasta_appserver / "webapp.versao").write_text("26.0.0-ts9")
    r = webapp.garantir(_bloco(url=pacote), pasta_appserver)
    assert r["trocado"] is False


def test_garantir_no_ar_nao_troca_e_avisa(pasta_appserver, pacote):
    r = webapp.garantir(_bloco(url=pacote), pasta_appserver, no_ar=True, nome="PAR_2610")
    assert r["trocado"] is False
    assert "PAR_2610" in r["aviso"] and "ERR0003" in r["aviso"]
    assert (pasta_appserver / "webapp.dll").read_bytes() == b"velha"


def test_garantir_falha_de_download_vira_aviso(pasta_appserver, tmp_path):
    r = webapp.garantir(_bloco(url=(tmp_path / "x.zip").as_uri()), pasta_appserver)
    assert r["ok"] is False and r["trocado"] is False and "aviso" in r


def test_subir_para_instancias_provisiona_por_instancia(tmp_path, monkeypatch, pacote):
    """Cada clone tem a própria pasta: a troca é por instância, antes de subir."""
    reg = Instancias(tmp_path / "instancias.json")
    pastas = {}
    for slot in (1, 2):
        nome = f"A_TIR{slot}"
        pasta = tmp_path / nome / "Protheus" / "bin" / "appserver"
        pasta.mkdir(parents=True)
        pastas[nome] = pasta
        reg.registrar(ambiente=nome, origem="A", slot=slot, banco=f"B{slot}",
                      portas={"webapp": 4320 + slot})
    monkeypatch.setattr(appservers, "porta_responde", lambda *a, **k: True)
    monkeypatch.setattr(appservers.appserver_ini, "aplicar_portas",
                        lambda ini, portas: {"ok": True})
    ordem = []
    monkeypatch.setattr(appservers, "subir",
                        lambda exe, params="": ordem.append(("subir", exe)) or {"ok": True, "pid": 5})
    monkeypatch.setattr(appservers, "garantir_webagent",
                        lambda *a, **k: {"ok": True, "conferido": False})

    def detalhes(nome):
        return {"ok": True,
                "banco": {"appserver_exe": str(pastas[nome] / "appserver.exe")},
                "webapp": _bloco(url=pacote)}

    r = appservers.subir_para_instancias(reg.listar("A"), reg, detalhes,
                                         dbaccess_por_instancia=False)
    assert [s["ambiente"] for s in r["subidos"]] == ["A_TIR1", "A_TIR2"]
    for pasta in pastas.values():
        assert (pasta / "webapp.dll").read_bytes() == b"nova-26"
        assert (pasta / "webapp.versao").read_text() == "26.0.0-ts9"
    assert r["avisos"] == []


def test_subir_para_instancias_falha_do_webapp_vira_aviso_e_sobe(tmp_path, monkeypatch):
    reg = Instancias(tmp_path / "instancias.json")
    pasta = tmp_path / "A_TIR1" / "Protheus" / "bin" / "appserver"
    pasta.mkdir(parents=True)
    reg.registrar(ambiente="A_TIR1", origem="A", slot=1, banco="B1",
                  portas={"webapp": 4321})
    monkeypatch.setattr(appservers, "porta_responde", lambda *a, **k: True)
    monkeypatch.setattr(appservers.appserver_ini, "aplicar_portas",
                        lambda ini, portas: {"ok": True})
    monkeypatch.setattr(appservers, "subir", lambda exe, params="": {"ok": True, "pid": 5})
    monkeypatch.setattr(appservers, "garantir_webagent",
                        lambda *a, **k: {"ok": True, "conferido": False})
    r = appservers.subir_para_instancias(
        reg.listar("A"), reg,
        lambda n: {"ok": True, "banco": {"appserver_exe": str(pasta / "appserver.exe")},
                   "webapp": _bloco(url=(tmp_path / "x.zip").as_uri())},
        dbaccess_por_instancia=False)
    assert [s["ambiente"] for s in r["subidos"]] == ["A_TIR1"]
    assert len(r["avisos"]) == 1 and "WebApp" in r["avisos"][0]


def test_subir_principal_provisiona_antes_de_subir(tmp_path, monkeypatch, pacote):
    from webui import api as api_mod
    from webui.api import Api
    api = Api(arquivo_importados=tmp_path / "importados.json",
              iniciar_monitores=False, instalar_log=False,
              arquivo_preferencias=tmp_path / "preferencias.json",
              arquivo_instancias=tmp_path / "instancias.json")
    pasta = tmp_path / "PAR_2610" / "Protheus" / "bin" / "appserver"
    pasta.mkdir(parents=True)
    monkeypatch.setattr(api, "_garantir_dbaccess", lambda nome, *a: {"ok": True})
    monkeypatch.setattr(api._estado, "detalhes_por_nome", lambda n: {
        "ok": True, "banco": {"appserver_exe": str(pasta / "appserver.exe"), "port": "4321"},
        "webapp": _bloco(url=pacote)})
    monkeypatch.setattr(api_mod.appservers, "porta_responde", lambda *a, **k: False)
    monkeypatch.setattr(api_mod.appservers, "subir", lambda exe, p="": {"ok": True, "pid": 9})
    monkeypatch.setattr(api_mod.appservers, "esperar_porta", lambda porta: {"ok": True})

    r = api._subir_principal("PAR_2610")
    assert r["ok"], r
    assert (pasta / "webapp.versao").read_text() == "26.0.0-ts9"
    textos = [e["text"] for e in api.poll_logs()]
    assert any("WebApp embutido → 26.0.0-ts9" in t for t in textos)


def test_subir_principal_no_ar_so_avisa(tmp_path, monkeypatch, pacote):
    from webui import api as api_mod
    from webui.api import Api
    api = Api(arquivo_importados=tmp_path / "importados.json",
              iniciar_monitores=False, instalar_log=False,
              arquivo_preferencias=tmp_path / "preferencias.json",
              arquivo_instancias=tmp_path / "instancias.json")
    pasta = tmp_path / "PAR_2610" / "Protheus" / "bin" / "appserver"
    pasta.mkdir(parents=True)
    monkeypatch.setattr(api, "_garantir_dbaccess", lambda nome, *a: {"ok": True})
    monkeypatch.setattr(api._estado, "detalhes_por_nome", lambda n: {
        "ok": True, "banco": {"appserver_exe": str(pasta / "appserver.exe"), "port": "4321"},
        "webapp": _bloco(url=pacote)})
    monkeypatch.setattr(api_mod.appservers, "porta_responde", lambda *a, **k: True)

    r = api._subir_principal("PAR_2610")
    assert r == {"ok": True, "reaproveitado": True}
    assert not (pasta / "webapp.versao").exists()
    avisos = [e["text"] for e in api.poll_logs() if e["level"] == "WARNING"]
    assert any("ERR0003" in a for a in avisos)
