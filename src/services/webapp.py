"""`webapp.dll` da release, provisionado aqui antes de subir cada AppServer.

O WebApp é a interface que o navegador carrega, e ele **acompanha a release**
do Protheus: até a 2510 serve o `webapp.dll` embutido (10.1.8); a 2610 exige
o da família 26 (`26.0.0-ts9`) — com o antigo, o navegador responde

    ERR0003: Incompatibilidad de versiones entre TOTVS Webapp y
             TOTVS Application Server

Quem sabe qual versão cada release pede é o Gerenciador (`binarios_por_versao`
do `config/urls.json`), e é ele quem troca a DLL no "Executar" dele. Só que
as instâncias paralelas sobem por aqui, sem passar por lá — e o usuário pediu
(2026-09-18) que o NebulaTIR tenha autonomia para deixar cada instância com
o WebApp certo, 2510 ou 2610, sem mandar ninguém abrir o Gerenciador.

Divisão: o **alvo e a URL** vêm pelo canal, no bloco `webapp` do detalhe do
ambiente (uma verdade só, lá). O **download e a troca** acontecem aqui, na
pasta do AppServer da instância — cópia da lógica de `services/webapp.py` do
Gerenciador, com `urllib` no lugar do `requests`, que este projeto não tem.

⚠ Trocar a DLL não basta. O AppServer extrai os assets dela para
`bin/appserver/webapp/` no primeiro boot e **não regrava a pasta se ela já
existe** — seguiria servindo `webapp-10.1.8.min.css` com a DLL nova. A pasta
é renomeada junto e nasce de novo no boot seguinte.

Ambiente **já no ar** com DLL errada não é derrubado: a troca só vale depois
de reiniciar o AppServer, e parar o ambiente do Gerenciador é justamente o
que o usuário pediu para não fazer. Vira aviso.
"""

from __future__ import annotations

import logging
import os
import shutil
import stat
import tempfile
import urllib.error
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path

from services import rastro

log = logging.getLogger(__name__)

NOME_DLL = "webapp.dll"
PASTA_ASSETS = "webapp"
# Marca da versão instalada — a mesma que o Gerenciador grava e lê. A DLL não
# expõe a versão de um jeito barato, e a pasta de assets só existe depois do
# primeiro boot.
ARQUIVO_MARCA = "webapp.versao"
TEMPO_LIMITE_DOWNLOAD = 300


def versao_instalada(pasta_appserver: str | Path) -> str:
    """Versão registrada pela última troca. Vazio = DLL embutida ou desconhecida."""
    arq = Path(pasta_appserver) / ARQUIVO_MARCA
    if not arq.is_file():
        return ""
    try:
        return arq.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _remover_somente_leitura(caminho: Path) -> None:
    """Arquivos do Protheus vêm read-only; sem isso a sobrescrita dá WinError 5."""
    try:
        os.chmod(caminho, stat.S_IWRITE | stat.S_IREAD)
    except OSError:
        pass


def _baixar(url: str, destino: Path) -> Path:
    req = urllib.request.Request(url, headers={"User-Agent": "NebulaTIR"})
    with urllib.request.urlopen(req, timeout=TEMPO_LIMITE_DOWNLOAD) as resp, \
            open(destino, "wb") as saida:
        shutil.copyfileobj(resp, saida, 1024 * 256)
    return destino


def _aposentar_assets(pasta_appserver: Path) -> Path | None:
    """Tira do caminho a pasta de assets da versão anterior.

    Renomear em vez de apagar: se algo mais der errado, a pasta antiga ainda
    está ali para conferência. O AppServer recria a sua no próximo boot.
    """
    assets = pasta_appserver / PASTA_ASSETS
    if not assets.is_dir():
        return None
    alvo = pasta_appserver / f"{PASTA_ASSETS}.bak-{datetime.now():%Y%m%d_%H%M%S}"
    shutil.move(str(assets), str(alvo))
    return alvo


def provisionar(pasta_appserver: str | Path, versao: str, url_zip: str) -> dict:
    """Baixa o pacote da versão e instala o `webapp.dll` junto do AppServer.

    O zip traz um arquivo só, `webapp.dll` na raiz. Devolve `{"ok", "versao",
    "assets_renomeados"}` ou `{"ok": False, "erro"}` — nunca levanta: quem
    chama decide se avisa ou barra.
    """
    destino = Path(pasta_appserver)
    if not destino.is_dir():
        return {"ok": False, "erro": f"Pasta do AppServer não existe: {destino}"}
    if not url_zip:
        return {"ok": False, "erro": "O Gerenciador não informou a URL do WebApp."}

    with rastro.etapa(f"webapp {versao}", pasta=str(destino), url=url_zip):
        with tempfile.TemporaryDirectory(prefix="nebulatir_webapp_") as tmp:
            zip_path = Path(tmp) / "smartclientwebapp.zip"
            try:
                _baixar(url_zip, zip_path)
            except (urllib.error.URLError, OSError, ValueError) as e:
                return {"ok": False,
                        "erro": f"Não consegui baixar o WebApp {versao} de {url_zip}: {e}. "
                                f"VPN no ar?"}
            try:
                with zipfile.ZipFile(zip_path) as z:
                    membro = next((m for m in z.namelist()
                                   if Path(m).name.lower() == NOME_DLL), None)
                    if membro is None:
                        return {"ok": False,
                                "erro": f"O pacote do WebApp {versao} não traz {NOME_DLL}."}
                    dll = destino / NOME_DLL
                    if dll.exists():
                        _remover_somente_leitura(dll)
                    with z.open(membro) as origem, open(dll, "wb") as saida:
                        shutil.copyfileobj(origem, saida)
            except (zipfile.BadZipFile, OSError) as e:
                return {"ok": False, "erro": f"Falha ao instalar o WebApp {versao}: {e}"}

        renomeada = _aposentar_assets(destino)
        (destino / ARQUIVO_MARCA).write_text(versao, encoding="utf-8")

    log.info("[WEBAPP] WebApp %s instalado em %s (%.1f MB).", versao, destino,
             (destino / NOME_DLL).stat().st_size / 1048576)
    return {"ok": True, "versao": versao,
            "assets_renomeados": str(renomeada) if renomeada else ""}


def garantir(bloco: dict | None, pasta_appserver: str | Path,
             no_ar: bool = False, nome: str = "") -> dict:
    """Deixa a pasta do AppServer com o WebApp que a release exige.

    `bloco` é o `webapp` do detalhe do ambiente (canal). Sem bloco —
    Gerenciador anterior à chave — não há o que conferir: `conferido=False`
    e a corrida segue como antes. Sem alvo (2510 e anteriores), a DLL
    embutida é a certa.

    A marca é lida **desta** pasta, não do bloco: o bloco vem do ambiente
    registrado no Gerenciador, e o clone recém-copiado pode ter a DLL do pai
    ou uma mais velha — o que vale é o que está no disco da instância.

    `no_ar=True` (AppServer já rodando, do Gerenciador ou de corrida anterior)
    nunca troca: a DLL nova só valeria depois de reiniciar, e derrubar o
    ambiente é o que o usuário pediu para não fazer. Vira `aviso`.
    """
    if not isinstance(bloco, dict) or not bloco or not bloco.get("ok", True):
        return {"ok": True, "conferido": False}
    alvo = (bloco.get("alvo") or "").strip()
    if not alvo:
        return {"ok": True, "conferido": True, "trocado": False, "alvo": ""}

    pasta = Path(pasta_appserver)
    instalado = versao_instalada(pasta)
    if instalado == alvo:
        return {"ok": True, "conferido": True, "trocado": False, "alvo": alvo}

    if no_ar:
        return {"ok": True, "conferido": True, "trocado": False, "alvo": alvo,
                "aviso": f"{nome or pasta}: o AppServer já está no "
                         f"ar com o WebApp {instalado or 'embutido'}, e a release pede "
                         f"{alvo}. Não troquei para não derrubar o ambiente — se o "
                         f"navegador acusar ERR0003, pare e suba de novo."}

    log.info("[WEBAPP] %s: WebApp %s → %s (a release exige).", pasta,
             instalado or "embutido", alvo)
    troca = provisionar(pasta, alvo, bloco.get("url_zip", ""))
    if not troca.get("ok"):
        return {"ok": False, "conferido": True, "trocado": False, "alvo": alvo,
                "aviso": troca["erro"]}
    return {"ok": True, "conferido": True, "trocado": True, "alvo": alvo,
            "de": instalado, "assets_renomeados": troca.get("assets_renomeados", "")}
