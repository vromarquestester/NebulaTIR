"""A família e o custo da verificação (`services.atualizacao`, 2026-09-14).

Três coisas entraram juntas e são testadas aqui:

- **pendente por executável** — a pasta `update/` é partilhada pelas
  ferramentas, e um `pendente.json` único fazia uma descartar a atualização em
  espera da outra;
- **GET condicional com backoff** — `304` reaproveita o manifesto guardado, e
  falha de rede dobra o intervalo até o teto;
- **a família** — quem verifica, verifica para as irmãs instaladas ao lado, e
  deixa em espera com o nome delas.

Nada aqui toca a rede nem lê a versão de um exe de verdade: `consultar_se_mudou`
e `versao_do_exe` são substituídos por dublês.
"""

import hashlib
import json
import os
import zipfile
from datetime import timedelta
from pathlib import Path

import pytest

from services import atualizacao as upd
from services import familia as fam
from services import canal


NOME_EXE = "NebulaTIR.exe"
IRMA = fam.Ferramenta("Gerenciador de Ambientes", "GerenciadorAmbientes.exe",
                      "vromarquestester/Gerenciador-de-Ambientes-Releases")


# =============================================================
# APOIO
# =============================================================

def _manifesto(versao, exe=NOME_EXE, sha="0" * 64):
    return upd.Manifesto.de_dados({
        "esquema": 1, "ferramenta": exe, "versao": versao, "canal": "estavel",
        "publicado_em": "", "obrigatoria": False, "minima_suportada": "0.0.0",
        "arquivo": {"nome": f"{exe}_v{versao}.zip", "exe": exe,
                    "url": f"https://exemplo/{exe}/{versao}", "sha256": sha,
                    "tamanho": 1},
        "changelog": [f"{exe} {versao}"],
    })


def _zip_com(destino: Path, nome: str, conteudo: bytes) -> str:
    with zipfile.ZipFile(destino, "w") as z:
        z.writestr(nome, conteudo)
    return hashlib.sha256(destino.read_bytes()).hexdigest()


def _baixador_de(zips: dict):
    """`url -> zip de origem`; copia e nada mais."""
    def baixar(url, destino, on_progress=None):
        destino.write_bytes(Path(zips[url]).read_bytes())
        return destino
    return baixar


def _servidor(monkeypatch, manifestos: dict):
    """Dublê do `consultar_se_mudou`: `url -> Manifesto`. Conta as chamadas e
    responde `304` quando o etag enviado é o vigente."""
    chamadas = []

    def consultar(url, etag=None, timeout=15):
        chamadas.append((url, etag))
        m = manifestos[url]
        if isinstance(m, Exception):
            raise m
        vigente = f'"{m.versao}"'
        if etag == vigente:
            return None, etag
        return m, vigente

    monkeypatch.setattr(upd, "consultar_se_mudou", consultar)
    return chamadas


@pytest.fixture
def instalado(tmp_path):
    (tmp_path / NOME_EXE).write_bytes(b"nebula velho")
    (tmp_path / IRMA.exe).write_bytes(b"gerenciador velho")
    return tmp_path


@pytest.fixture
def versoes(monkeypatch):
    """`versao_do_exe` dublado pelo nome do arquivo."""
    tabela = {IRMA.exe: "2.7.2"}
    monkeypatch.setattr(upd, "versao_do_exe",
                        lambda caminho: tabela.get(Path(caminho).name))
    return tabela


# =============================================================
# PENDENTE POR EXECUTÁVEL
# =============================================================

def test_descartar_o_proprio_nao_toca_no_pendente_da_irma(instalado):
    """O defeito que motivou tudo: NebulaTIR abria, lia o `pendente.json` do
    Gerenciador, não achava `NebulaTIR.exe.new` e apagava tudo."""
    pasta = instalado / "update"
    pasta.mkdir()
    (pasta / f"{IRMA.exe}.new").write_bytes(b"gerenciador novo")
    (pasta / f"{IRMA.exe}.pendente.json").write_text(json.dumps({
        "versao": "2.8.0", "exe": IRMA.exe}), encoding="utf-8")

    assert upd.ler_pendente(instalado, NOME_EXE) is None
    assert upd.aplicar_pendente(instalado, NOME_EXE) is None
    upd.descartar_pendente(instalado, NOME_EXE)

    assert (pasta / f"{IRMA.exe}.new").exists()
    assert upd.ler_pendente(instalado, IRMA.exe)["versao"] == "2.8.0"


def test_pendente_legado_e_lido_so_se_for_nosso(instalado):
    """Uma versão anterior deixou o `pendente.json` único. Ele vale para quem
    está gravado nele — e só para esse."""
    pasta = instalado / "update"
    pasta.mkdir()
    (pasta / "pendente.json").write_text(json.dumps({
        "versao": "2.8.0", "exe": IRMA.exe}), encoding="utf-8")

    assert upd.ler_pendente(instalado, NOME_EXE) is None
    assert upd.ler_pendente(instalado, IRMA.exe)["versao"] == "2.8.0"

    upd.descartar_pendente(instalado, NOME_EXE)
    assert (pasta / "pendente.json").exists()      # não era nosso
    upd.descartar_pendente(instalado, IRMA.exe)
    assert not (pasta / "pendente.json").exists()


def test_pendente_por_exe_vence_o_legado(instalado):
    pasta = instalado / "update"
    pasta.mkdir()
    (pasta / "pendente.json").write_text(json.dumps({
        "versao": "0.3.0", "exe": NOME_EXE}), encoding="utf-8")
    (pasta / f"{NOME_EXE}.pendente.json").write_text(json.dumps({
        "versao": "0.4.0", "exe": NOME_EXE}), encoding="utf-8")
    assert upd.ler_pendente(instalado, NOME_EXE)["versao"] == "0.4.0"


def test_pendente_com_exe_de_outro_nome_e_ignorado(instalado):
    pasta = instalado / "update"
    pasta.mkdir()
    (pasta / f"{NOME_EXE}.pendente.json").write_text(json.dumps({
        "versao": "0.4.0", "exe": IRMA.exe}), encoding="utf-8")
    assert upd.ler_pendente(instalado, NOME_EXE) is None


def test_download_proprio_grava_com_o_nome_do_exe(instalado, tmp_path, monkeypatch):
    zipado = tmp_path / "n.zip"
    sha = _zip_com(zipado, NOME_EXE, b"nebula novo")
    a = upd.Atualizador(instalado, NOME_EXE, "u", "0.2.0", upd.ConfigAtualizacao(),
                        baixador=_baixador_de({"https://exemplo/NebulaTIR.exe/0.3.0": zipado}))
    a._manifesto = _manifesto("0.3.0", sha=sha)

    assert a.baixar()["estado"] == upd.PRONTO
    assert (instalado / "update" / f"{NOME_EXE}.pendente.json").exists()
    assert not (instalado / "update" / "pendente.json").exists()
    assert not (instalado / "update" / f"{NOME_EXE}.baixando").exists()


# =============================================================
# TRAVA DE DOWNLOAD
# =============================================================

def test_trava_recusa_enquanto_o_dono_estiver_vivo(instalado, monkeypatch):
    monkeypatch.setattr(upd, "_pid_vivo", lambda pid: pid == 4242)
    pasta = instalado / "update"
    pasta.mkdir()
    (pasta / f"{NOME_EXE}.baixando").write_text(json.dumps({"pid": 4242}))

    with upd.TravaDownload(instalado, NOME_EXE) as obtida:
        assert obtida is False
    assert (pasta / f"{NOME_EXE}.baixando").exists()     # não é nossa


def test_trava_de_processo_morto_e_tomada(instalado, monkeypatch):
    monkeypatch.setattr(upd, "_pid_vivo", lambda pid: False)
    pasta = instalado / "update"
    pasta.mkdir()
    (pasta / f"{NOME_EXE}.baixando").write_text(json.dumps({"pid": 4242}))

    with upd.TravaDownload(instalado, NOME_EXE) as obtida:
        assert obtida is True
        assert json.loads((pasta / f"{NOME_EXE}.baixando").read_text())["pid"] \
            == os.getpid()
    assert not (pasta / f"{NOME_EXE}.baixando").exists()


def test_trava_e_por_executavel(instalado):
    with upd.TravaDownload(instalado, NOME_EXE) as a, \
            upd.TravaDownload(instalado, IRMA.exe) as b:
        assert a and b


def test_download_proprio_cede_quando_a_irma_esta_baixando(instalado, monkeypatch):
    monkeypatch.setattr(upd, "_pid_vivo", lambda pid: True)
    pasta = instalado / "update"
    pasta.mkdir()
    (pasta / f"{NOME_EXE}.baixando").write_text(json.dumps({"pid": 1}))

    a = upd.Atualizador(instalado, NOME_EXE, "u", "0.2.0", upd.ConfigAtualizacao(),
                        baixador=lambda *a, **k: pytest.fail("não pode baixar"))
    a._manifesto = _manifesto("0.3.0")
    estado = a.baixar()
    assert estado["estado"] == upd.DISPONIVEL
    assert "outro programa" in estado["mensagem"]


# =============================================================
# GET CONDICIONAL E BACKOFF
# =============================================================

def test_segunda_consulta_manda_o_etag_e_reaproveita_o_manifesto(instalado, monkeypatch):
    chamadas = _servidor(monkeypatch, {"u": _manifesto("0.3.0")})
    a = upd.Atualizador(instalado, NOME_EXE, "u", "0.2.0", upd.ConfigAtualizacao())

    assert a.verificar(forcado=True)["estado"] == upd.DISPONIVEL
    assert a.verificar(forcado=True)["estado"] == upd.DISPONIVEL

    assert chamadas == [("u", None), ("u", '"0.3.0"')]


def test_falha_dobra_o_intervalo_ate_o_teto_e_sucesso_zera(instalado, monkeypatch):
    manifestos = {"u": upd.ErroAtualizacao("proxy")}
    _servidor(monkeypatch, manifestos)
    a = upd.Atualizador(instalado, NOME_EXE, "u", "0.2.0", upd.ConfigAtualizacao())
    assert a.intervalo_atual() == timedelta(minutes=5)

    esperados = [10, 20, 40, 60, 60]
    for minutos in esperados:
        a.verificar(forcado=True)
        assert a.intervalo_atual() == timedelta(minutes=minutos)

    manifestos["u"] = _manifesto("0.2.0")
    assert a.verificar(forcado=True)["estado"] == upd.EM_DIA
    assert a.intervalo_atual() == timedelta(minutes=5)
    assert a.estado["intervalo_seg"] == 300


def test_marca_de_verificacao_e_gravada_no_maximo_uma_vez_por_hora(instalado, monkeypatch):
    """A cada 5 min o Gerenciador reescreveria o INI inteiro por nada."""
    _servidor(monkeypatch, {"u": _manifesto("0.2.0")})
    gravadas = []
    a = upd.Atualizador(instalado, NOME_EXE, "u", "0.2.0",
                        upd.ConfigAtualizacao(ao_registrar=gravadas.append))
    for _ in range(5):
        a.verificar(forcado=True)
    assert len(gravadas) == 1


def test_rodada_periodica_nao_depende_da_marca_gravada(instalado, monkeypatch):
    """O intervalo do monitor é o freio; a marca na configuração só serve à
    partida. Com ela recente, a rodada continua consultando."""
    from datetime import datetime, timezone
    chamadas = _servidor(monkeypatch, {"u": _manifesto("0.2.0")})
    agora = datetime.now(timezone.utc).isoformat(timespec="seconds")
    a = upd.Atualizador(instalado, NOME_EXE, "u", "0.2.0",
                        upd.ConfigAtualizacao(ultima_verificacao=agora))
    a._rodada_periodica()
    assert chamadas


def test_304_de_verdade_devolve_none_e_o_etag(monkeypatch):
    import urllib.error

    def urlopen(req, timeout=0):
        assert req.get_header("If-none-match") == '"abc"'
        raise urllib.error.HTTPError(req.full_url, 304, "Not Modified", {}, None)

    monkeypatch.setattr(upd.urllib.request, "urlopen", urlopen)
    assert upd.consultar_se_mudou("https://x/latest.json", '"abc"') == (None, '"abc"')


# =============================================================
# A FAMÍLIA
# =============================================================

def test_catalogo_e_o_canal_concordam():
    """`canal.py` diz quem eu sou; `familia.py` diz quem somos. Divergência
    entre os dois faria a irmã procurar a minha vitrine no lugar errado."""
    eu = fam.por_exe(canal.NOME_EXE)
    assert eu is not None
    assert eu.vitrine == canal.VITRINE
    assert eu.url_manifesto == canal.URL_MANIFESTO
    assert canal.NOME_EXE not in [f.exe for f in fam.irmas(canal.NOME_EXE)]
    assert len(fam.irmas(canal.NOME_EXE)) == len(fam.FERRAMENTAS) - 1


def test_irma_desatualizada_e_baixada_com_o_nome_dela(instalado, tmp_path, monkeypatch, versoes):
    zipado = tmp_path / "g.zip"
    sha = _zip_com(zipado, IRMA.exe, b"gerenciador novo")
    _servidor(monkeypatch, {"u": _manifesto("0.2.0"),
                            IRMA.url_manifesto: _manifesto("2.8.0", IRMA.exe, sha)})
    a = upd.Atualizador(instalado, NOME_EXE, "u", "0.2.0", upd.ConfigAtualizacao(),
                        baixador=_baixador_de({f"https://exemplo/{IRMA.exe}/2.8.0": zipado}),
                        irmas=(IRMA,))

    estado = a.verificar(forcado=True)

    assert estado["estado"] == upd.EM_DIA                 # o próprio não mudou
    [situacao] = estado["familia"]
    assert situacao["exe"] == IRMA.exe
    assert situacao["estado"] == upd.PRONTO
    assert (situacao["versao_atual"], situacao["versao_nova"]) == ("2.7.2", "2.8.0")

    pendente = upd.ler_pendente(instalado, IRMA.exe)
    assert pendente["versao"] == "2.8.0"
    assert pendente["baixado_por"] == NOME_EXE
    novo = instalado / "update" / f"{IRMA.exe}.new"
    assert novo.read_bytes() == b"gerenciador novo"
    assert pendente["sha256_exe"] == hashlib.sha256(b"gerenciador novo").hexdigest()
    # E a irmã aplica sozinha na partida dela, pelo caminho de sempre.
    assert upd.aplicar_pendente(instalado, IRMA.exe) == instalado / IRMA.exe
    assert (instalado / IRMA.exe).read_bytes() == b"gerenciador novo"
    # Sem tocar no nosso.
    assert (instalado / NOME_EXE).read_bytes() == b"nebula velho"


def test_irma_ausente_nao_e_baixada(instalado, monkeypatch, versoes):
    (instalado / IRMA.exe).unlink()
    chamadas = _servidor(monkeypatch, {"u": _manifesto("0.2.0"),
                                       IRMA.url_manifesto: _manifesto("2.8.0", IRMA.exe)})
    a = upd.Atualizador(instalado, NOME_EXE, "u", "0.2.0", upd.ConfigAtualizacao(),
                        irmas=(IRMA,))
    [situacao] = a.verificar(forcado=True)["familia"]
    assert situacao["estado"] == upd.AUSENTE
    # Nem o manifesto dela é consultado: não há o que atualizar.
    assert [u for u, _ in chamadas] == ["u"]


def test_irma_em_dia_nao_baixa(instalado, monkeypatch, versoes):
    _servidor(monkeypatch, {"u": _manifesto("0.2.0"),
                            IRMA.url_manifesto: _manifesto("2.7.2", IRMA.exe)})
    a = upd.Atualizador(instalado, NOME_EXE, "u", "0.2.0", upd.ConfigAtualizacao(),
                        baixador=lambda *a, **k: pytest.fail("não pode baixar"),
                        irmas=(IRMA,))
    [situacao] = a.verificar(forcado=True)["familia"]
    assert situacao["estado"] == upd.EM_DIA


def test_automatico_desligado_so_avisa_sobre_a_irma(instalado, monkeypatch, versoes):
    _servidor(monkeypatch, {"u": _manifesto("0.2.0"),
                            IRMA.url_manifesto: _manifesto("2.8.0", IRMA.exe)})
    a = upd.Atualizador(instalado, NOME_EXE, "u", "0.2.0",
                        upd.ConfigAtualizacao(automatica=False),
                        baixador=lambda *a, **k: pytest.fail("não pode baixar"),
                        irmas=(IRMA,))
    [situacao] = a.verificar(forcado=True)["familia"]
    assert situacao["estado"] == upd.DISPONIVEL
    assert upd.ler_pendente(instalado, IRMA.exe) is None


def test_irma_ja_em_espera_nao_baixa_de_novo(instalado, monkeypatch, versoes):
    pasta = instalado / "update"
    pasta.mkdir()
    (pasta / f"{IRMA.exe}.pendente.json").write_text(json.dumps({
        "versao": "2.8.0", "exe": IRMA.exe}), encoding="utf-8")
    _servidor(monkeypatch, {"u": _manifesto("0.2.0"),
                            IRMA.url_manifesto: _manifesto("2.8.0", IRMA.exe)})
    a = upd.Atualizador(instalado, NOME_EXE, "u", "0.2.0", upd.ConfigAtualizacao(),
                        baixador=lambda *a, **k: pytest.fail("não pode baixar"),
                        irmas=(IRMA,))
    [situacao] = a.verificar(forcado=True)["familia"]
    assert situacao["estado"] == upd.PRONTO


def test_falha_na_irma_nao_muda_o_estado_proprio(instalado, monkeypatch, versoes):
    _servidor(monkeypatch, {"u": _manifesto("0.2.0"),
                            IRMA.url_manifesto: upd.ErroAtualizacao("vitrine fora")})
    a = upd.Atualizador(instalado, NOME_EXE, "u", "0.2.0", upd.ConfigAtualizacao(),
                        irmas=(IRMA,))
    estado = a.verificar(forcado=True)
    assert estado["estado"] == upd.EM_DIA
    [situacao] = estado["familia"]
    assert situacao["estado"] == upd.ERRO
    assert "vitrine fora" in situacao["mensagem"]
    assert a.intervalo_atual() == timedelta(minutes=5)   # sem backoff por ela


def test_hash_errado_da_irma_nao_deixa_nada_em_espera(instalado, tmp_path, monkeypatch, versoes):
    zipado = tmp_path / "g.zip"
    _zip_com(zipado, IRMA.exe, b"gerenciador novo")
    _servidor(monkeypatch, {"u": _manifesto("0.2.0"),
                            IRMA.url_manifesto: _manifesto("2.8.0", IRMA.exe, "f" * 64)})
    a = upd.Atualizador(instalado, NOME_EXE, "u", "0.2.0", upd.ConfigAtualizacao(),
                        baixador=_baixador_de({f"https://exemplo/{IRMA.exe}/2.8.0": zipado}),
                        irmas=(IRMA,))
    [situacao] = a.verificar(forcado=True)["familia"]
    assert situacao["estado"] == upd.ERRO
    assert "hash" in situacao["mensagem"]
    assert upd.ler_pendente(instalado, IRMA.exe) is None
    assert not (instalado / "update" / f"{IRMA.exe}.new").exists()


def test_versao_ilegivel_da_irma_nao_baixa(instalado, monkeypatch):
    monkeypatch.setattr(upd, "versao_do_exe", lambda caminho: None)
    _servidor(monkeypatch, {"u": _manifesto("0.2.0"),
                            IRMA.url_manifesto: _manifesto("2.8.0", IRMA.exe)})
    a = upd.Atualizador(instalado, NOME_EXE, "u", "0.2.0", upd.ConfigAtualizacao(),
                        baixador=lambda *a, **k: pytest.fail("não pode baixar"),
                        irmas=(IRMA,))
    [situacao] = a.verificar(forcado=True)["familia"]
    assert situacao["estado"] == upd.ERRO
    assert "versão" in situacao["mensagem"]


@pytest.mark.skipif(os.name != "nt", reason="VS_VERSIONINFO é coisa do Windows")
def test_versao_do_exe_le_o_versioninfo_de_um_binario_real():
    import sys
    versao = upd.versao_do_exe(Path(sys.base_prefix) / "python.exe")
    assert versao and upd.versao_tupla(versao)
    assert upd.versao_do_exe(Path("nao_existe.exe")) is None
