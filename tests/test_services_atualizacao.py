"""Atualização automática do executável (`services.atualizacao`).

Nada aqui toca a rede: o manifesto é servido por um dublê e o download é um
callable injetado. A troca do binário é exercitada com arquivos de mentira em
`tmp_path` — o que se testa é a coreografia dos renames, não o PyInstaller.

O caso que mais importa: **falha na troca não pode impedir o programa de
abrir**. Vários testes existem só para fixar isso.
"""

import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from services import atualizacao as upd


NOME_EXE = "NebulaTIR.exe"


# =============================================================
# APOIO
# =============================================================

def _manifesto_dict(versao="2.7.0", sha="0" * 64, **extra):
    dados = {
        "esquema": 1,
        "ferramenta": "NebulaTIR",
        "versao": versao,
        "canal": "estavel",
        "publicado_em": "2026-09-02T18:00:00Z",
        "obrigatoria": False,
        "minima_suportada": "0.0.0",
        "arquivo": {
            "nome": f"NebulaTIR_v{versao}.zip",
            "exe": NOME_EXE,
            "url": f"https://exemplo/NebulaTIR_v{versao}.zip",
            "sha256": sha,
            "tamanho": 123,
        },
        "changelog": ["Mudou alguma coisa."],
    }
    dados.update(extra)
    return dados


def _zip_com_exe(destino: Path, conteudo=b"binario novo", nome=NOME_EXE) -> str:
    with zipfile.ZipFile(destino, "w") as z:
        z.writestr(nome, conteudo)
    return hashlib.sha256(destino.read_bytes()).hexdigest()


def _baixador_falso(origem: Path):
    """Devolve um callable com a assinatura do baixador, que só copia."""
    def baixar(url, destino, on_progress=None):
        destino.write_bytes(Path(origem).read_bytes())
        if on_progress:
            on_progress(len(destino.read_bytes()), len(destino.read_bytes()))
        return destino
    return baixar


@pytest.fixture
def instalado(tmp_path):
    """Uma instalação de mentira: o exe na raiz e a pasta update vazia."""
    (tmp_path / NOME_EXE).write_bytes(b"binario velho")
    return tmp_path


# =============================================================
# VERSÃO
# =============================================================

def test_prerelease_ordena_antes_do_lancamento():
    # Regra do SemVer 2.0.0, e ela importa: sem isso quem estivesse num rc
    # nunca receberia a versão final.
    assert upd.versao_tupla("2.7.0-rc1") < upd.versao_tupla("2.7.0")
    assert upd.versao_tupla("2.7.0-rc1") < upd.versao_tupla("2.7.0-rc2")
    assert upd.versao_tupla("2.6.9") < upd.versao_tupla("2.7.0-rc1")


def test_versao_invalida_e_recusada():
    with pytest.raises(upd.ErroAtualizacao):
        upd.versao_tupla("dois ponto sete")


# =============================================================
# MANIFESTO
# =============================================================

def test_esquema_desconhecido_e_recusado():
    # Interpretar pela metade é pior que não interpretar: um campo novo pode
    # ser exatamente o que impediria a instalação.
    with pytest.raises(upd.ErroAtualizacao, match="esquema"):
        upd.Manifesto.de_dados(_manifesto_dict(esquema=2))


def test_manifesto_sem_hash_e_recusado():
    dados = _manifesto_dict()
    del dados["arquivo"]["sha256"]
    with pytest.raises(upd.ErroAtualizacao, match="incompleto"):
        upd.Manifesto.de_dados(dados)


# =============================================================
# DECISÃO
# =============================================================

def test_versao_igual_ou_menor_nao_atualiza():
    m = upd.Manifesto.de_dados(_manifesto_dict("2.6.1"))
    assert upd.comparar("2.6.1", m)["atualizar"] is False
    assert upd.comparar("2.7.0", m)["atualizar"] is False


def test_prerelease_so_com_o_canal_ligado():
    m = upd.Manifesto.de_dados(_manifesto_dict("2.7.0-beta1"))
    assert upd.comparar("2.6.1", m)["atualizar"] is False
    assert upd.comparar("2.6.1", m, incluir_prerelease=True)["atualizar"] is True


def test_minima_suportada_bloqueia_e_explica():
    m = upd.Manifesto.de_dados(
        _manifesto_dict("2.7.0", minima_suportada="2.6.5"))
    decisao = upd.comparar("2.6.1", m)
    assert decisao["atualizar"] is False
    assert decisao["bloqueada"] is True
    assert "pacote" in decisao["motivo"]


# =============================================================
# DOWNLOAD E PREPARO
# =============================================================

def test_hash_divergente_descarta_o_pacote(instalado, tmp_path):
    zipado = tmp_path / "pacote.zip"
    _zip_com_exe(zipado)
    m = _manifesto_dict("2.7.0", sha="f" * 64)   # hash que não é o do arquivo

    a = upd.Atualizador(instalado, NOME_EXE, "http://x/latest.json", "2.6.1",
                        upd.ConfigAtualizacao(), baixador=_baixador_falso(zipado))
    a._manifesto = upd.Manifesto.de_dados(m)

    estado = a.baixar()
    assert estado["estado"] == upd.ERRO
    assert "hash" in estado["mensagem"]
    # Sem prova de origem, nada fica em espera.
    assert upd.ler_pendente(instalado, NOME_EXE) is None


def test_download_bom_deixa_em_espera(instalado, tmp_path):
    zipado = tmp_path / "pacote.zip"
    sha = _zip_com_exe(zipado)

    a = upd.Atualizador(instalado, NOME_EXE, "http://x/latest.json", "2.6.1",
                        upd.ConfigAtualizacao(), baixador=_baixador_falso(zipado))
    a._manifesto = upd.Manifesto.de_dados(_manifesto_dict("2.7.0", sha))

    estado = a.baixar()
    assert estado["estado"] == upd.PRONTO
    assert (instalado / "update" / f"{NOME_EXE}.new").exists()

    pendente = upd.ler_pendente(instalado, NOME_EXE)
    assert pendente["versao"] == "2.7.0"
    assert pendente["sha256_exe"]
    # O zip some depois de extraído: guardar 40 MB que já cumpriram o papel
    # ocuparia disco à toa.
    assert not (instalado / "update" / f"NebulaTIR_v2.7.0.zip").exists()


def test_zip_sem_o_executavel_falha(instalado, tmp_path):
    zipado = tmp_path / "pacote.zip"
    sha = _zip_com_exe(zipado, nome="OutraCoisa.exe")

    a = upd.Atualizador(instalado, NOME_EXE, "http://x/latest.json", "2.6.1",
                        upd.ConfigAtualizacao(), baixador=_baixador_falso(zipado))
    a._manifesto = upd.Manifesto.de_dados(_manifesto_dict("2.7.0", sha))

    assert a.baixar()["estado"] == upd.ERRO


def test_exe_em_subpasta_do_zip_nao_e_extraido(instalado, tmp_path):
    # Nome de membro vem de arquivo externo. Aceitar caminho deixaria um zip
    # montado escrever fora da pasta de staging.
    zipado = tmp_path / "pacote.zip"
    sha = _zip_com_exe(zipado, nome=f"subpasta/{NOME_EXE}")

    a = upd.Atualizador(instalado, NOME_EXE, "http://x/latest.json", "2.6.1",
                        upd.ConfigAtualizacao(), baixador=_baixador_falso(zipado))
    a._manifesto = upd.Manifesto.de_dados(_manifesto_dict("2.7.0", sha))

    assert a.baixar()["estado"] == upd.ERRO
    assert not (instalado / "update" / f"{NOME_EXE}.new").exists()


# =============================================================
# A TROCA
# =============================================================

def _deixar_em_espera(base: Path, conteudo=b"binario novo", versao="2.7.0"):
    pasta = base / "update"
    pasta.mkdir(exist_ok=True)
    novo = pasta / f"{NOME_EXE}.new"
    novo.write_bytes(conteudo)
    (pasta / f"{NOME_EXE}.pendente.json").write_text(json.dumps({
        "versao": versao,
        "exe": NOME_EXE,
        "sha256_exe": hashlib.sha256(conteudo).hexdigest(),
        "changelog": [],
    }), encoding="utf-8")
    return novo


def test_aplicar_troca_e_guarda_o_anterior(instalado):
    _deixar_em_espera(instalado)

    devolvido = upd.aplicar_pendente(instalado, NOME_EXE)

    assert devolvido == instalado / NOME_EXE
    assert (instalado / NOME_EXE).read_bytes() == b"binario novo"
    # O anterior fica para o "Reverter atualização" — não dá para detectar de
    # dentro da versão nova que ela mesma quebrou. Fica em `update/`, fora da
    # vista: na pasta do programa parecia um arquivo estranho.
    assert (instalado / "update" / f"{NOME_EXE}.old").read_bytes() == b"binario velho"
    assert not (instalado / f"{NOME_EXE}.old").exists()
    assert upd.ler_pendente(instalado, NOME_EXE) is None


def test_aplicar_sem_pendente_nao_faz_nada(instalado):
    assert upd.aplicar_pendente(instalado, NOME_EXE) is None
    assert (instalado / NOME_EXE).read_bytes() == b"binario velho"


def test_arquivo_em_espera_adulterado_e_descartado(instalado):
    _deixar_em_espera(instalado)
    # Entre o download e a próxima abertura o arquivo fica no disco; é essa
    # janela que a conferência na partida cobre.
    (instalado / "update" / f"{NOME_EXE}.new").write_bytes(b"outra coisa")

    assert upd.aplicar_pendente(instalado, NOME_EXE) is None
    assert (instalado / NOME_EXE).read_bytes() == b"binario velho"
    assert upd.ler_pendente(instalado, NOME_EXE) is None


def test_pendente_sem_arquivo_e_descartado(instalado):
    _deixar_em_espera(instalado)
    (instalado / "update" / f"{NOME_EXE}.new").unlink()

    assert upd.aplicar_pendente(instalado, NOME_EXE) is None
    assert upd.ler_pendente(instalado, NOME_EXE) is None


def test_reverter_troca_os_dois_de_lugar(instalado):
    _deixar_em_espera(instalado)
    upd.aplicar_pendente(instalado, NOME_EXE)

    assert upd.pode_reverter(instalado, NOME_EXE) is True
    assert upd.reverter(instalado, NOME_EXE) is True
    assert (instalado / NOME_EXE).read_bytes() == b"binario velho"
    # E dá para desfazer a reversão: o novo virou o `.old`.
    assert (instalado / "update" / f"{NOME_EXE}.old").read_bytes() == b"binario novo"
    assert not (instalado / "update" / f"{NOME_EXE}.revertendo").exists()


def test_reverter_sem_anterior_devolve_falso(instalado):
    assert upd.reverter(instalado, NOME_EXE) is False


def test_limpar_antigo_preserva_a_versao_anterior(instalado):
    # O `.old` de `update/` é o "Voltar à versão anterior": a partida não o
    # apaga. Só a atualização seguinte o substitui.
    assert upd.limpar_antigo(instalado, NOME_EXE) is False
    _deixar_em_espera(instalado)
    upd.aplicar_pendente(instalado, NOME_EXE)
    assert upd.limpar_antigo(instalado, NOME_EXE) is False
    assert (instalado / "update" / f"{NOME_EXE}.old").read_bytes() == b"binario velho"
    assert upd.pode_reverter(instalado, NOME_EXE) is True


def test_atualizacao_seguinte_substitui_a_versao_anterior(instalado):
    _deixar_em_espera(instalado, b"binario novo", "2.7.3")
    upd.aplicar_pendente(instalado, NOME_EXE)
    _deixar_em_espera(instalado, b"binario mais novo", "2.7.4")
    upd.aplicar_pendente(instalado, NOME_EXE)
    assert (instalado / NOME_EXE).read_bytes() == b"binario mais novo"
    assert (instalado / "update" / f"{NOME_EXE}.old").read_bytes() == b"binario novo"


def test_limpar_antigo_recolhe_o_old_das_versoes_anteriores(instalado):
    # Até a 2.7.2 o `.old` ficava ao lado do `.exe`. Quem sai de uma delas
    # ainda o encontra lá na primeira abertura.
    legado = instalado / f"{NOME_EXE}.old"
    legado.write_bytes(b"binario de antes")
    assert upd.limpar_antigo(instalado, NOME_EXE) is True
    assert not legado.exists()


def test_preparar_partida_nao_troca_em_desenvolvimento(instalado, monkeypatch):
    # `sys.frozen` ausente = rodando do repositório. Não há binário para
    # trocar, e mexer aqui atrapalharia quem desenvolve.
    _deixar_em_espera(instalado)
    monkeypatch.delattr("sys.frozen", raising=False)

    assert upd.preparar_partida(instalado, NOME_EXE) is None
    assert upd.ler_pendente(instalado, NOME_EXE) is not None


def test_preparar_partida_engole_qualquer_falha(instalado, monkeypatch):
    # A regra que vale acima de todas: atualização não pode impedir o programa
    # de abrir.
    monkeypatch.setattr("sys.frozen", True, raising=False)
    monkeypatch.setattr(upd, "aplicar_pendente",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))

    assert upd.preparar_partida(instalado, NOME_EXE) is None


# =============================================================
# RELANÇAR
# =============================================================

def test_ambiente_do_relancamento_sai_sem_as_variaveis_do_pyinstaller():
    # O bootloader deixa `_PYI_*` no ambiente; herdado pelo filho, ele se trata
    # como subprocesso deste e exige o mesmo executável no pai — que é o
    # `.old`. Era o "Security validation failure" depois de atualizar.
    ambiente = {
        "PATH": "C:\\x",
        "_PYI_ARCHIVE_FILE": "a",
        "_PYI_PARENT_PROCESS_LEVEL": "1",
        "_PYI_APPLICATION_HOME_DIR": "b",
    }
    limpo = upd.ambiente_para_relancar(ambiente)
    assert limpo == {"PATH": "C:\\x", "PYINSTALLER_RESET_ENVIRONMENT": "1"}
    assert "_PYI_ARCHIVE_FILE" in ambiente            # o original fica intacto


def test_relancar_passa_o_ambiente_limpo(monkeypatch):
    chamadas = []
    monkeypatch.setattr(upd.subprocess, "Popen",
                        lambda args, **kw: chamadas.append((args, kw)))
    monkeypatch.setenv("_PYI_PARENT_PROCESS_LEVEL", "1")

    assert upd.relancar(Path("C:/x/app.exe")) is True
    (args, kw), = chamadas
    assert args == ["C:\\x\\app.exe"]
    assert "_PYI_PARENT_PROCESS_LEVEL" not in kw["env"]
    assert kw["env"]["PYINSTALLER_RESET_ENVIRONMENT"] == "1"


def test_reiniciar_aplica_o_pendente_e_relanca_o_novo(instalado, monkeypatch):
    monkeypatch.setattr("sys.frozen", True, raising=False)
    relancados = []
    monkeypatch.setattr(upd, "relancar", lambda exe: relancados.append(exe) or True)
    _deixar_em_espera(instalado)

    assert upd.reiniciar(instalado, NOME_EXE) == instalado / NOME_EXE
    assert relancados == [instalado / NOME_EXE]
    assert (instalado / NOME_EXE).read_bytes() == b"binario novo"
    assert upd.ler_pendente(instalado, NOME_EXE) is None


def test_reiniciar_sem_pendente_relanca_como_esta(instalado, monkeypatch):
    monkeypatch.setattr("sys.frozen", True, raising=False)
    relancados = []
    monkeypatch.setattr(upd, "relancar", lambda exe: relancados.append(exe) or True)

    assert upd.reiniciar(instalado, NOME_EXE) == instalado / NOME_EXE
    assert relancados == [instalado / NOME_EXE]
    assert (instalado / NOME_EXE).read_bytes() == b"binario velho"


def test_reiniciar_com_troca_quebrada_relanca_a_versao_que_estava(instalado, monkeypatch):
    monkeypatch.setattr("sys.frozen", True, raising=False)
    monkeypatch.setattr(upd, "aplicar_pendente",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    relancados = []
    monkeypatch.setattr(upd, "relancar", lambda exe: relancados.append(exe) or True)

    assert upd.reiniciar(instalado, NOME_EXE) == instalado / NOME_EXE
    assert relancados == [instalado / NOME_EXE]


def test_reiniciar_em_desenvolvimento_nao_faz_nada(instalado, monkeypatch):
    monkeypatch.delattr("sys.frozen", raising=False)
    monkeypatch.setattr(upd, "relancar", lambda exe: pytest.fail("não relança em dev"))
    assert upd.reiniciar(instalado, NOME_EXE) is None


# =============================================================
# THROTTLE
# =============================================================

def test_primeira_execucao_verifica_sempre(instalado):
    cfg = upd.ConfigAtualizacao(ultima_verificacao="")
    a = upd.Atualizador(instalado, NOME_EXE, "http://x", "2.6.1", cfg)
    # Quem acabou de extrair o pacote de entrada está várias versões atrás de
    # propósito; esperar até amanhã seria absurdo.
    assert a.deve_verificar() is True


def test_verificacao_recente_nao_repete(instalado):
    from datetime import datetime, timezone
    agora = datetime.now(timezone.utc).isoformat(timespec="seconds")
    a = upd.Atualizador(instalado, NOME_EXE, "http://x", "2.6.1",
                        upd.ConfigAtualizacao(ultima_verificacao=agora))
    assert a.deve_verificar() is False


def test_intervalo_casa_com_o_cache_do_cdn():
    """5 min é o `max-age` do `raw`: mais rápido não vê nada novo, mais
    devagar atrasa a versão sem economizar nada. O teto do backoff é 1 h."""
    from datetime import timedelta
    assert upd.INTERVALO_VERIFICACAO == timedelta(minutes=5)
    assert upd.INTERVALO_MAXIMO == timedelta(hours=1)


def test_verificacao_de_duas_horas_atras_repete(instalado):
    from datetime import datetime, timedelta, timezone
    passado = (datetime.now(timezone.utc) - timedelta(hours=2)) \
        .isoformat(timespec="seconds")
    a = upd.Atualizador(instalado, NOME_EXE, "http://x", "2.6.1",
                        upd.ConfigAtualizacao(ultima_verificacao=passado))
    assert a.deve_verificar() is True


# =============================================================
# MONITOR PERIÓDICO
# =============================================================
# Até 2026-09-04 só havia a checagem da partida: quem deixa o Gerenciador
# aberto o dia inteiro — o uso normal — nunca via versão publicada depois de a
# janela abrir.

def test_rodada_baixa_quando_ha_versao_nova(instalado, monkeypatch):
    a = upd.Atualizador(instalado, NOME_EXE, "http://x", "2.6.1",
                        upd.ConfigAtualizacao())
    monkeypatch.setattr(upd, "consultar_se_mudou",
                        lambda url, etag=None, timeout=15: (
                            upd.Manifesto.de_dados(_manifesto_dict("2.7.0")),
                            "etag"))
    baixou = []
    monkeypatch.setattr(a, "baixar", lambda: baixou.append(True))

    a._rodada_periodica()

    assert baixou == [True]


def test_rodada_nao_faz_nada_com_o_automatico_desligado(instalado, monkeypatch):
    a = upd.Atualizador(instalado, NOME_EXE, "http://x", "2.6.1",
                        upd.ConfigAtualizacao(automatica=False))
    monkeypatch.setattr(upd, "consultar_se_mudou",
                        lambda url, etag=None, timeout=15: pytest.fail(
                            "não pode consultar com o automático desligado"))

    a._rodada_periodica()


def test_falha_periodica_nao_propaga(instalado, monkeypatch):
    """Exceção aqui mataria a thread e o dia inteiro passaria sem verificar."""
    a = upd.Atualizador(instalado, NOME_EXE, "http://x", "2.6.1",
                        upd.ConfigAtualizacao())
    monkeypatch.setattr(a, "verificar",
                        lambda forcado=False: (_ for _ in ()).throw(
                            RuntimeError("proxy recusou")))

    a._rodada_periodica()      # não pode levantar


def test_monitor_sobe_uma_vez_so_e_para_quando_mandam(instalado):
    a = upd.Atualizador(instalado, NOME_EXE, "http://x", "2.6.1",
                        upd.ConfigAtualizacao())
    try:
        a.monitorar()
        primeiro = a._monitor
        a.monitorar()
        assert a._monitor is primeiro, "duas threads verificariam em dobro"
    finally:
        a.parar_monitor()
    a._monitor.join(timeout=5)
    assert not a._monitor.is_alive()


def test_monitor_tem_piso_de_intervalo(instalado, monkeypatch):
    """Intervalo minúsculo por engano viraria laço quente batendo no GitHub."""
    from datetime import timedelta
    esperas = []

    class _EventoEspiao(upd.threading.Event):
        def wait(self, timeout=None):
            esperas.append(timeout)
            return True               # sai do laço na primeira volta

    a = upd.Atualizador(instalado, NOME_EXE, "http://x", "2.6.1",
                        upd.ConfigAtualizacao())
    a._parar_monitor = _EventoEspiao()
    a.monitorar(timedelta(seconds=1))
    a._monitor.join(timeout=5)

    assert esperas and esperas[0] >= 60


def test_desligado_nao_verifica_sozinho_mas_aceita_forcado(instalado, monkeypatch):
    cfg = upd.ConfigAtualizacao(automatica=False)
    a = upd.Atualizador(instalado, NOME_EXE, "http://x", "2.6.1", cfg)
    assert a.deve_verificar() is False

    # Desligar o automático não é renunciar a atualizar.
    monkeypatch.setattr(upd, "consultar_se_mudou",
                        lambda url, etag=None, timeout=15: (
                            upd.Manifesto.de_dados(_manifesto_dict("2.7.0")),
                            "etag"))
    assert a.verificar(forcado=True)["estado"] == upd.DISPONIVEL


def test_falha_de_rede_vira_erro_visivel(instalado, monkeypatch):
    # Proxy corporativo bloqueando o GitHub não pode virar "sempre em dia".
    def explode(url, etag=None, timeout=15):
        raise upd.ErroAtualizacao("Não foi possível consultar atualizações: x")

    monkeypatch.setattr(upd, "consultar_se_mudou", explode)
    a = upd.Atualizador(instalado, NOME_EXE, "http://x", "2.6.1",
                        upd.ConfigAtualizacao())
    estado = a.verificar(forcado=True)
    assert estado["estado"] == upd.ERRO
    assert "consultar" in estado["mensagem"]
