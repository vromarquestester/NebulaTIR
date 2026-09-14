"""Atualização automática do executável.

O programa consulta um manifesto público, baixa a versão nova **em espera** e
troca o binário na abertura seguinte. Desenho completo e o porquê de cada
decisão estão no card `canal-de-atualizacao` do hub.

O que torna isso possível no Windows sem processo auxiliar:

    Executável em uso não pode ser sobrescrito nem apagado, mas PODE ser
    renomeado.

Daí o ciclo: renomeia o `.exe` em uso para `.old`, move o novo para o lugar,
relança e encerra. O processo atual continua rodando a partir do `.old` — por
isso ele precisa relançar e sair, senão o usuário seguiria na versão velha
achando que atualizou.

Estrutura em disco, ao lado do executável::

    NebulaTIR.exe
    GerenciadorAmbientes.exe          as outras ferramentas da família
    update/
        NebulaTIR.exe.pendente.json   o que está em espera, por executável
        NebulaTIR.exe.new             o binário já baixado e conferido
        NebulaTIR.exe.old             versão anterior, para reverter
        NebulaTIR.exe.baixando        trava: quem está baixando este exe

Tudo em `update/` leva o nome do executável a que pertence. A pasta é
compartilhada pelas ferramentas da família (`services/familia.py`), e um
`pendente.json` único — como era até 2026-09-14 — fazia uma ferramenta
descartar a atualização em espera da outra: NebulaTIR abria, lia o pendente do
Gerenciador, não achava `NebulaTIR.exe.new` e apagava tudo.

O `.old` mora dentro de `update/`, e não ao lado do `.exe`: na pasta do
programa ele parecia um arquivo estranho que apareceu do nada. Renomear o
executável em uso para outra pasta do mesmo volume é o mesmo `rename` — o
Windows aceita; o que ele recusa é apagar ou sobrescrever.

Ele fica lá até a atualização seguinte o substituir. Apagá-lo na abertura
seguinte, como era antes, nunca deixava janela para o "Voltar à versão
anterior": o processo que troca relança e sai em seguida, e o filho chega à
limpeza com o pai já morto — o `.old` sumia segundos depois de nascer.

Regras que atravessam o módulo:

- **Atualização nunca impede o programa de abrir.** Toda falha aqui degrada
  para "abre a versão que já estava"; nada nesta camada pode derrubar a
  partida.
- **O `sha256` é a única prova de origem.** Não há certificado de code
  signing: sem conferir o hash publicado pelo pipeline, atualização automática
  seria execução de binário arbitrário baixado da internet. Zip que não bate é
  descartado, nunca instalado.
- **Nada é trocado com a janela aberta.** A troca acontece na partida, antes
  da janela subir, ou na saída, quando o usuário pede "Reiniciar agora" — nos
  dois casos sem ninguém usando o programa.
- **Quem verifica, verifica para a família inteira.** Cada rodada consulta o
  manifesto das irmãs instaladas na mesma pasta e deixa em espera o que estiver
  desatualizado. Irmã **fechada** é trocada na hora, pela mesma coreografia de
  renames (ninguém está rodando o binário); irmã **aberta** fica com o pendente
  e aplica na própria partida — ou a rodada seguinte daqui a troca, se ela já
  tiver fechado. Quem manda é o interruptor "automática" da ferramenta que
  detectou. Isto cobre a irmã antiga demais para ter atualizador: ela é
  trocada por fora. Irmã ausente pode ser instalada por `instalar_irma`.
- **A verificação é barata de propósito.** GET condicional com `ETag`: o
  `raw` responde `304` sem corpo (~550 B de cabeçalho) enquanto nada mudou, e
  só entrega o JSON quando há versão nova. Falha de rede dobra o intervalo até
  o teto — VPN caída não martela o GitHub.

Relançar exige limpar o ambiente do PyInstaller. O bootloader (onefile) deixa
`_PYI_*` no ambiente do processo, e a partir do PyInstaller 6.9 o executável
que nasce com essas variáveis se trata como filho e exige que o processo pai
seja o mesmo binário — o pai aqui é o `.old` recém-renomeado, e o filho morria
com "Security validation failure: parent process has different executable".
`PYINSTALLER_RESET_ENVIRONMENT=1` é a saída oficial para reinício de aplicação.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from services.familia import Ferramenta

log = logging.getLogger(__name__)

# Versão do formato do latest.json que este código entende. Manifesto de
# esquema maior é IGNORADO em vez de interpretado pela metade: um campo novo
# com significado desconhecido pode ser exatamente o que impede a instalação.
ESQUEMA_SUPORTADO = 1

PASTA_UPDATE = "update"
# Nome antigo, único para a pasta. Só é lido para não perder o que uma versão
# anterior deixou em espera; nunca mais é escrito.
ARQUIVO_PENDENTE_LEGADO = "pendente.json"
SUFIXO_PENDENTE = ".pendente.json"
SUFIXO_NOVO = ".new"
SUFIXO_ANTIGO = ".old"
SUFIXO_TRAVA = ".baixando"

# A cada 5 min, que é o `max-age` do CDN do `raw`: verificar mais rápido não
# vê nada novo, e mais devagar atrasa a versão sem economizar nada que
# importe. A requisição é condicional (`If-None-Match`): enquanto o manifesto
# não muda, a resposta é um `304` sem corpo. Nenhum processo é criado, nenhuma
# janela aparece.
INTERVALO_VERIFICACAO = timedelta(minutes=5)
# Em falha o intervalo dobra a cada tentativa até aqui. Rede corporativa,
# proxy e VPN caem o tempo todo; insistir a cada 5 min seria ruído no log e
# no proxy.
INTERVALO_MAXIMO = timedelta(hours=1)
# A marca `ultima_verificacao` vai para a configuração no máximo uma vez por
# hora. Ela só serve à partida (primeira execução verifica sempre); gravar a
# cada 5 min reescreveria a configuração o dia inteiro por nada.
INTERVALO_REGISTRO = timedelta(hours=1)
TIMEOUT_MANIFESTO = 15
TIMEOUT_DOWNLOAD = 300
CHUNK = 262144

# Estados que a interface mostra.
DESLIGADO = "desligado"
OCIOSO = "ocioso"
VERIFICANDO = "verificando"
EM_DIA = "em-dia"
DISPONIVEL = "disponivel"
BAIXANDO = "baixando"
PRONTO = "pronto"
ERRO = "erro"
# Só para as irmãs (a própria ferramenta não pode estar ausente, nem ser
# trocada com o programa aberto).
AUSENTE = "ausente"
ATUALIZADA = "atualizada"


class ErroAtualizacao(Exception):
    """Falha tratada: a mensagem vai para a interface."""


# =============================================================
# VERSÃO
# =============================================================

_SEMVER = re.compile(
    r"^v?(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)"
    r"(?:-(?P<pre>[0-9A-Za-z.-]+))?$"
)


def versao_tupla(versao: str) -> tuple:
    """Chave de ordenação de SemVer, com pré-lançamento ANTES do lançamento.

    `2.7.0-rc1 < 2.7.0` é a regra do SemVer 2.0.0, e ela importa aqui: sem
    isso, quem estivesse num `rc` receberia a final como se fosse a mesma
    versão e nunca sairia do pré-lançamento.
    """
    m = _SEMVER.match((versao or "").strip())
    if not m:
        raise ErroAtualizacao(f"Versão fora do formato SemVer: {versao!r}")
    pre = m["pre"]
    # 1 = lançamento final; 0 = pré-lançamento (ordena antes).
    return (int(m["major"]), int(m["minor"]), int(m["patch"]),
            1 if pre is None else 0,
            _partes_pre(pre))


def _partes_pre(pre: str | None) -> tuple:
    if not pre:
        return ()
    partes = []
    for p in pre.split("."):
        if p.isdigit():
            partes.append((0, int(p), ""))    # numérico ordena antes do texto
        else:
            partes.append((1, 0, p))
    return tuple(partes)


def e_prerelease(versao: str) -> bool:
    m = _SEMVER.match((versao or "").strip())
    return bool(m and m["pre"])


# =============================================================
# MANIFESTO
# =============================================================

@dataclass(frozen=True)
class Manifesto:
    """O `latest.json` publicado na vitrine, já validado."""

    ferramenta: str
    versao: str
    canal: str
    publicado_em: str
    obrigatoria: bool
    minima_suportada: str
    nome: str
    exe: str
    url: str
    sha256: str
    tamanho: int
    changelog: list = field(default_factory=list)

    @classmethod
    def de_dados(cls, dados: dict) -> "Manifesto":
        if not isinstance(dados, dict):
            raise ErroAtualizacao("Manifesto não é um objeto JSON.")

        esquema = dados.get("esquema")
        if esquema != ESQUEMA_SUPORTADO:
            raise ErroAtualizacao(
                f"Manifesto de esquema {esquema!r}; este programa entende "
                f"{ESQUEMA_SUPORTADO}. Atualize pelo pacote de instalação.")

        arquivo = dados.get("arquivo") or {}
        faltando = [c for c in ("versao",) if not dados.get(c)]
        faltando += [f"arquivo.{c}" for c in ("url", "sha256", "exe")
                     if not arquivo.get(c)]
        if faltando:
            raise ErroAtualizacao(
                f"Manifesto incompleto: falta {', '.join(faltando)}.")

        versao = str(dados["versao"]).lstrip("vV")
        versao_tupla(versao)          # valida o formato agora, não na troca

        return cls(
            ferramenta=str(dados.get("ferramenta", "")),
            versao=versao,
            canal=str(dados.get("canal", "estavel")),
            publicado_em=str(dados.get("publicado_em", "")),
            obrigatoria=bool(dados.get("obrigatoria", False)),
            minima_suportada=str(dados.get("minima_suportada", "0.0.0")),
            nome=str(arquivo.get("nome", "")),
            exe=str(arquivo["exe"]),
            url=str(arquivo["url"]),
            sha256=str(arquivo["sha256"]).lower(),
            tamanho=int(arquivo.get("tamanho") or 0),
            changelog=list(dados.get("changelog") or []),
        )


def consultar_se_mudou(url: str, etag: str | None = None,
                       timeout: int = TIMEOUT_MANIFESTO
                       ) -> tuple[Manifesto | None, str | None]:
    """Lê o manifesto da vitrine, só se ele mudou desde o `etag` dado.

    Devolve `(manifesto, etag_novo)`. Com `etag` e nada mudado, o servidor
    responde `304` sem corpo e o manifesto vem `None` — quem chama reaproveita
    o que já tinha. Medido no `raw` do GitHub em 2026-09-14: `200` = 809 B de
    corpo + 934 B de cabeçalho; `304` = 0 B + 551 B.

    ⚠ `raw.githubusercontent.com` tem cache de CDN de 5 min (`max-age=300`):
    publicar e o programa não ver na hora é normal, não defeito.
    """
    cabecalhos = {"User-Agent": "atualizador"}
    if etag:
        cabecalhos["If-None-Match"] = etag
    req = urllib.request.Request(url, headers=cabecalhos)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            bruto = r.read().decode("utf-8")
            etag_novo = r.headers.get("ETag") or None
    except urllib.error.HTTPError as erro:
        if erro.code == 304:
            return None, etag
        raise ErroAtualizacao(f"Não foi possível consultar atualizações: {erro}")
    except (urllib.error.URLError, TimeoutError, OSError) as erro:
        # Rede corporativa, proxy e VPN fora do ar caem aqui. É falha de
        # verificação, não do programa — quem chama mostra e segue.
        raise ErroAtualizacao(f"Não foi possível consultar atualizações: {erro}")

    try:
        return Manifesto.de_dados(json.loads(bruto)), etag_novo
    except ValueError as erro:
        raise ErroAtualizacao(f"Manifesto ilegível: {erro}")


def consultar(url: str, timeout: int = TIMEOUT_MANIFESTO) -> Manifesto:
    """Lê o manifesto sem condição — sempre traz o corpo."""
    manifesto, _ = consultar_se_mudou(url, None, timeout)
    assert manifesto is not None      # sem etag não há 304
    return manifesto


def comparar(versao_atual: str, manifesto: Manifesto,
             incluir_prerelease: bool = False) -> dict:
    """Decide se a versão do manifesto deve ser oferecida."""
    atual = versao_tupla(versao_atual)
    nova = versao_tupla(manifesto.versao)

    if not incluir_prerelease and e_prerelease(manifesto.versao):
        return {"atualizar": False, "motivo": "publicada é um pré-lançamento"}

    if nova <= atual:
        return {"atualizar": False, "motivo": "já está na versão mais recente"}

    # ⚠ `minima_suportada` acima da versão do pacote de entrada quebraria a
    # porta de entrada em silêncio. A regra é do lado de quem publica; aqui só
    # se obedece — e se avisa, para o caso não virar "não atualiza e não diz".
    if versao_tupla(manifesto.minima_suportada) > atual:
        return {
            "atualizar": False,
            "motivo": (f"esta versão ({versao_atual}) é anterior à mínima "
                       f"suportada ({manifesto.minima_suportada}). Reinstale "
                       f"pelo pacote."),
            "bloqueada": True,
        }

    return {"atualizar": True, "motivo": "", "obrigatoria": manifesto.obrigatoria}


# =============================================================
# DOWNLOAD E PREPARO
# =============================================================

def sha256_do_arquivo(caminho: Path) -> str:
    h = hashlib.sha256()
    with Path(caminho).open("rb") as f:
        for bloco in iter(lambda: f.read(1024 * 1024), b""):
            h.update(bloco)
    return h.hexdigest()


def _baixar_simples(url: str, destino: Path, on_progress=None) -> Path:
    """Download de fluxo único, só com a biblioteca padrão.

    O zip de uma release fica na casa das dezenas de MB — abaixo do piso em que
    o download segmentado do Gerenciador se paga. Quem quiser outro caminho
    injeta `baixador` no `Atualizador`.
    """
    req = urllib.request.Request(url, headers={"User-Agent": "atualizador"})
    destino.parent.mkdir(parents=True, exist_ok=True)
    parcial = destino.with_suffix(destino.suffix + ".parcial")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_DOWNLOAD) as r:
            total = int(r.headers.get("Content-Length") or 0)
            baixado = 0
            with parcial.open("wb") as f:
                while True:
                    bloco = r.read(CHUNK)
                    if not bloco:
                        break
                    f.write(bloco)
                    baixado += len(bloco)
                    if on_progress:
                        on_progress(baixado, total)
    except (urllib.error.URLError, TimeoutError, OSError) as erro:
        parcial.unlink(missing_ok=True)
        raise ErroAtualizacao(f"Falha no download: {erro}")

    parcial.replace(destino)
    return destino


def limpar_marca_da_internet(caminho: Path) -> None:
    """Remove o alternate data stream `:Zone.Identifier`.

    Arquivo baixado nasce marcado, e binário sem assinatura Authenticode com
    essa marca é barrado pelo SmartScreen. Falha aqui é ignorada de propósito:
    a marca ausente é conveniência, não requisito.
    """
    try:
        os.remove(f"{caminho}:Zone.Identifier")
    except OSError:
        pass


def _extrair_exe(caminho_zip: Path, nome_exe: str, destino: Path) -> Path:
    """Tira do zip só o executável, e só se ele estiver na raiz.

    Nome de membro vem de arquivo externo: aceitar caminho faria um zip
    montado escrever fora da pasta de staging (Zip Slip). Aqui a comparação é
    pelo nome exato, sem separador — o que não bate não é extraído.
    """
    with zipfile.ZipFile(caminho_zip) as z:
        alvos = [n for n in z.namelist()
                 if n == nome_exe and "/" not in n and "\\" not in n]
        if not alvos:
            raise ErroAtualizacao(
                f"O pacote baixado não tem {nome_exe} na raiz.")
        destino.parent.mkdir(parents=True, exist_ok=True)
        with z.open(alvos[0]) as origem, destino.open("wb") as saida:
            shutil.copyfileobj(origem, saida)
    return destino


# =============================================================
# A TROCA — roda na partida, antes da janela
# =============================================================

def pasta_update(base_dir: Path) -> Path:
    return Path(base_dir) / PASTA_UPDATE


def arquivo_pendente(base_dir: Path, nome_exe: str) -> Path:
    """`update/<exe>.pendente.json` — um por executável, na pasta partilhada."""
    return pasta_update(base_dir) / f"{nome_exe}{SUFIXO_PENDENTE}"


def arquivo_novo(base_dir: Path, nome_exe: str) -> Path:
    return pasta_update(base_dir) / f"{nome_exe}{SUFIXO_NOVO}"


def _ler_json(arquivo: Path) -> dict | None:
    try:
        dados = json.loads(arquivo.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return dados if isinstance(dados, dict) else None


def ler_pendente(base_dir: Path, nome_exe: str) -> dict | None:
    """O que está em espera para ESTE executável.

    Lê o arquivo por exe; na falta dele, o `pendente.json` antigo — mas só se
    o `exe` gravado nele for o nosso. Uma versão anterior pode ter deixado o
    pendente da irmã lá, e esse não é nosso para ler nem para apagar.
    """
    dados = _ler_json(arquivo_pendente(base_dir, nome_exe))
    if dados is None:
        dados = _ler_json(pasta_update(base_dir) / ARQUIVO_PENDENTE_LEGADO)
        if dados is None or str(dados.get("exe", "")).lower() != nome_exe.lower():
            return None
    if str(dados.get("exe") or nome_exe).lower() != nome_exe.lower():
        return None
    return dados


def descartar_pendente(base_dir: Path, nome_exe: str) -> None:
    """Apaga o que estava em espera **deste** executável.

    Só o que leva o nosso nome. Apagar `*.new` da pasta inteira, como era,
    destruía a atualização baixada pela irmã.
    """
    arquivo_pendente(base_dir, nome_exe).unlink(missing_ok=True)
    arquivo_novo(base_dir, nome_exe).unlink(missing_ok=True)
    legado = pasta_update(base_dir) / ARQUIVO_PENDENTE_LEGADO
    dados = _ler_json(legado)
    if dados is not None and str(dados.get("exe", "")).lower() == nome_exe.lower():
        legado.unlink(missing_ok=True)


def aplicar_pendente(base_dir: Path, nome_exe: str) -> Path | None:
    """Troca o executável, se houver um em espera. Devolve o novo, ou None.

    Chamar **antes** de subir a janela e depois de garantir elevação — a pasta
    do programa costuma exigir administrador para escrita.

    Qualquer falha desfaz o que deu para desfazer e devolve None: o programa
    abre na versão que já estava. Atualização que impede a abertura é pior que
    atualização que não acontece.
    """
    base_dir = Path(base_dir)
    pendente = ler_pendente(base_dir, nome_exe)
    if not pendente:
        return None

    novo = arquivo_novo(base_dir, nome_exe)
    atual = base_dir / nome_exe
    antigo = caminho_antigo(base_dir, nome_exe)

    if not novo.exists():
        log.warning("[UPD] pendente sem arquivo — descartado.")
        descartar_pendente(base_dir, nome_exe)
        return None

    # O hash guardado é o do EXE já extraído, não o do zip: entre o download e
    # a próxima abertura o arquivo em espera fica no disco, e é essa janela que
    # a conferência aqui cobre.
    esperado = str(pendente.get("sha256_exe") or "").lower()
    if esperado and sha256_do_arquivo(novo) != esperado:
        log.error("[UPD] o arquivo em espera não confere com o hash — descartado.")
        descartar_pendente(base_dir, nome_exe)
        return None

    # Sem executável no lugar é instalação, não troca: nada a guardar como
    # anterior. É o caminho de `instalar_irma`.
    instalando = not atual.exists()

    if not instalando:
        try:
            antigo.unlink(missing_ok=True)
        except OSError:
            # Ainda mapeado por outro processo. Sem lugar para guardar a versão
            # anterior, não se troca: ficar sem rollback é pior que adiar.
            log.warning("[UPD] não foi possível remover o %s anterior — adiado.",
                        SUFIXO_ANTIGO)
            return None

        try:
            atual.rename(antigo)
        except OSError as erro:
            log.error("[UPD] falha ao renomear o executável em uso: %s", erro)
            return None

    try:
        shutil.move(str(novo), str(atual))
    except OSError as erro:
        log.error("[UPD] falha ao pôr a versão nova no lugar: %s — revertendo.",
                  erro)
        if not instalando:
            try:
                antigo.rename(atual)
            except OSError:
                log.critical("[UPD] o executável ficou como %s. Renomeie à mão.",
                             antigo.name)
        return None

    descartar_pendente(base_dir, nome_exe)
    log.info("[UPD] versão %s aplicada.", pendente.get("versao"))
    return atual


def caminho_antigo(base_dir: Path, nome_exe: str) -> Path:
    """Onde a versão anterior fica guardada: `update/<exe>.old`."""
    return pasta_update(base_dir) / f"{nome_exe}{SUFIXO_ANTIGO}"


def limpar_antigo(base_dir: Path, nome_exe: str) -> bool:
    """Recolhe o `.old` **ao lado do `.exe`** — o lugar visível, onde as versões
    até a 2.7.2 o deixavam. Quem sai de uma delas ainda o encontra lá na
    primeira abertura. O `.old` de `update/` não é tocado: ele é o "Voltar à
    versão anterior", e só a atualização seguinte o substitui.
    """
    legado = Path(base_dir) / f"{nome_exe}{SUFIXO_ANTIGO}"
    if not legado.exists():
        return False
    try:
        legado.unlink()
        log.info("[UPD] %s removido da pasta do programa.", legado.name)
        return True
    except OSError:
        return False           # ainda mapeado por um processo que está saindo


def pode_reverter(base_dir: Path, nome_exe: str) -> bool:
    return caminho_antigo(base_dir, nome_exe).exists()


def reverter(base_dir: Path, nome_exe: str) -> bool:
    """Volta para a versão anterior, trocando os dois de lugar.

    É a rede de segurança para o caso de a versão nova não servir: não há como
    detectar de dentro dela que ela mesma quebrou, então a saída é o usuário
    pedir a volta.
    """
    base_dir = Path(base_dir)
    atual = base_dir / nome_exe
    antigo = caminho_antigo(base_dir, nome_exe)
    if not antigo.exists():
        return False

    intermediario = pasta_update(base_dir) / f"{nome_exe}.revertendo"
    try:
        intermediario.unlink(missing_ok=True)
        atual.rename(intermediario)
        antigo.rename(atual)
        intermediario.rename(antigo)
    except OSError as erro:
        log.error("[UPD] falha ao reverter: %s", erro)
        return False
    log.info("[UPD] revertido para a versão anterior.")
    return True


VARIAVEL_RESET_PYINSTALLER = "PYINSTALLER_RESET_ENVIRONMENT"
PREFIXO_AMBIENTE_PYINSTALLER = "_PYI_"


def ambiente_para_relancar(ambiente: dict | None = None) -> dict:
    """Ambiente do filho: sem `_PYI_*` e com o reset pedido ao bootloader.

    Só o reset bastaria a partir do PyInstaller 6.22.1; tirar as variáveis
    também cobre bootloader mais velho, que não conhece o pedido e trataria o
    filho como subprocesso deste — exatamente o que dispara a validação.
    """
    base = os.environ if ambiente is None else ambiente
    limpo = {chave: valor for chave, valor in base.items()
             if not chave.startswith(PREFIXO_AMBIENTE_PYINSTALLER)}
    limpo[VARIAVEL_RESET_PYINSTALLER] = "1"
    return limpo


def relancar(caminho_exe: Path) -> bool:
    """Sobe o executável novo e deixa este processo terminar.

    Já estamos elevados quando isto roda, então o filho herda a elevação sem
    passar de novo pelo UAC. O ambiente vai limpo — ver o cabeçalho do módulo.
    """
    try:
        subprocess.Popen([str(caminho_exe)], close_fds=True,
                         env=ambiente_para_relancar())
        return True
    except OSError as erro:
        log.error("[UPD] não foi possível relançar: %s", erro)
        return False


def reiniciar(base_dir: Path, nome_exe: str) -> Path | None:
    """Rotina de saída do "Reiniciar agora": aplica o que estiver em espera e
    relança. Sem nada em espera, relança o executável como está.

    Chamar depois que a janela fechou e nada mais do programa está de pé — a
    troca renomeia o binário que este processo ainda executa, e ele precisa
    terminar logo em seguida. Devolve o executável relançado, ou None quando
    não deu para relançar (o programa simplesmente fecha).
    """
    if not getattr(sys, "frozen", False):
        return None            # em desenvolvimento não há binário para relançar
    base_dir = Path(base_dir)
    try:
        exe = aplicar_pendente(base_dir, nome_exe) or (base_dir / nome_exe)
    except Exception:          # noqa: BLE001 — falha na troca não pode travar a saída
        log.exception("[UPD] falha ao aplicar na saída; relançando como está.")
        exe = base_dir / nome_exe
    if not exe.exists():
        log.error("[UPD] %s não existe — não há o que relançar.", exe)
        return None
    if not relancar(exe):
        return None
    return exe


def preparar_partida(base_dir: Path, nome_exe: str) -> Path | None:
    """Rotina de partida: recolhe o `.old` legado e aplica o que estiver em espera.

    Devolve o executável a relançar quando trocou, ou None para seguir a
    abertura normal. Nunca levanta: falha aqui não pode impedir o programa de
    abrir.
    """
    if not getattr(sys, "frozen", False):
        return None            # em desenvolvimento não há binário para trocar
    try:
        limpar_antigo(base_dir, nome_exe)
        return aplicar_pendente(base_dir, nome_exe)
    except Exception:          # noqa: BLE001 — partida não pode cair por isto
        log.exception("[UPD] falha ao preparar a partida; seguindo sem trocar.")
        return None


# =============================================================
# A FAMÍLIA — versão da irmã e trava de download
# =============================================================

def versao_do_exe(caminho: Path) -> str | None:
    """A versão gravada no `VS_VERSIONINFO` do executável, ou None.

    É como se sabe a versão da irmã sem ela cooperar: uma ferramenta que nunca
    abriu não registrou nada em lugar nenhum, mas o binário carrega a versão
    (`generate_version_info.py` grava o `__version__` completo no
    `StringFileInfo`, pré-lançamento incluído — o campo numérico o perde).
    """
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes

    version = ctypes.WinDLL("version", use_last_error=True)
    caminho = str(caminho)
    tamanho = version.GetFileVersionInfoSizeW(caminho, None)
    if not tamanho:
        return None
    dados = ctypes.create_string_buffer(tamanho)
    if not version.GetFileVersionInfoW(caminho, 0, tamanho, dados):
        return None

    ponteiro = ctypes.c_void_p()
    comprimento = wintypes.UINT()

    def consulta(sub: str) -> bool:
        return bool(version.VerQueryValueW(
            dados, sub, ctypes.byref(ponteiro), ctypes.byref(comprimento)))

    # Texto primeiro: é o único lugar em que "2.8.0-rc1" sobrevive.
    traducoes = [(0x0409, 0x04B0)]
    if consulta("\\VarFileInfo\\Translation") and comprimento.value >= 4:
        pares = ctypes.cast(ponteiro, ctypes.POINTER(wintypes.WORD))
        traducoes = [(pares[i], pares[i + 1])
                     for i in range(0, comprimento.value // 2, 2)] + traducoes
    for idioma, pagina in traducoes:
        chave = f"\\StringFileInfo\\{idioma:04x}{pagina:04x}\\FileVersion"
        if consulta(chave) and comprimento.value:
            texto = ctypes.wstring_at(ponteiro, comprimento.value)
            texto = texto.rstrip("\x00").strip()
            if _SEMVER.match(texto):
                return texto.lstrip("vV")

    # Sem texto utilizável: o campo numérico (perde o pré-lançamento).
    if consulta("\\") and comprimento.value >= 16:
        campos = ctypes.cast(ponteiro, ctypes.POINTER(wintypes.DWORD))
        ms, ls = campos[2], campos[3]        # dwFileVersionMS, dwFileVersionLS
        return f"{ms >> 16}.{ms & 0xFFFF}.{ls >> 16}"
    return None


def _pid_vivo(pid: int) -> bool:
    if not pid:
        return False
    if os.name != "nt":
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    import ctypes
    from ctypes import wintypes
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return False
    try:
        codigo = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(codigo)):
            return True
        return codigo.value == STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def _exe_em_uso(caminho: Path) -> bool:
    """Se há processo rodando este binário.

    Windows não deixa abrir para escrita um executável mapeado — é a mesma
    regra que impede sobrescrevê-lo. Um `open` de escrita que falha diz "em
    uso" sem enumerar processo nenhum. Arquivo só-leitura também cai aqui e é
    tratado como em uso: na dúvida, não se troca por fora.
    """
    try:
        fd = os.open(str(caminho), os.O_RDWR)
    except PermissionError:
        return True
    except OSError:
        return False            # não existe, ou outro erro: não está rodando
    os.close(fd)
    return False


class TravaDownload:
    """`update/<exe>.baixando`: quem está baixando aquele executável.

    Duas ferramentas abertas veem a mesma versão nova ao mesmo tempo — a
    própria e a irmã — e sem isto as duas baixariam o mesmo zip para o mesmo
    caminho. A trava é um arquivo criado com `x` (atômico no NTFS) com o PID
    de quem baixa; trava de processo morto é lixo de crash e é tomada.

    Uso: ``with TravaDownload(base_dir, exe) as obtida: if not obtida: ...``
    """

    def __init__(self, base_dir: Path, nome_exe: str):
        self.arquivo = pasta_update(base_dir) / f"{nome_exe}{SUFIXO_TRAVA}"
        self.obtida = False

    def __enter__(self) -> bool:
        self.arquivo.parent.mkdir(parents=True, exist_ok=True)
        for _ in range(2):
            try:
                with self.arquivo.open("x", encoding="utf-8") as f:
                    json.dump({"pid": os.getpid(),
                               "quando": datetime.now(timezone.utc)
                               .isoformat(timespec="seconds")}, f)
                self.obtida = True
                return True
            except FileExistsError:
                dona = _ler_json(self.arquivo) or {}
                if _pid_vivo(int(dona.get("pid") or 0)):
                    return False          # outra ferramenta está baixando
                self.arquivo.unlink(missing_ok=True)    # sobra de crash
            except OSError:
                return False
        return False

    def __exit__(self, *_exc) -> None:
        if self.obtida:
            self.arquivo.unlink(missing_ok=True)


# =============================================================
# ORQUESTRAÇÃO
# =============================================================

@dataclass
class ConfigAtualizacao:
    """O que o usuário controla. Cada programa guarda isso do seu jeito.

    O Gerenciador usa `[GLOBAL]` do `bancos_config.ini`; o NebulaTIR usa o
    `preferencias.json`. O adaptador de cada um preenche isto e recebe de volta
    o `registrar_verificacao`.
    """
    automatica: bool = True
    incluir_prerelease: bool = False
    ultima_verificacao: str = ""
    ao_registrar: object = None      # callable(iso: str) -> None

    def registrar_verificacao(self, quando: str) -> None:
        self.ultima_verificacao = quando
        if callable(self.ao_registrar):
            self.ao_registrar(quando)


class Atualizador:
    """Verifica, baixa em espera e informa a interface — para si e para as irmãs.

    Nada aqui troca binário: a troca é do `preparar_partida`, na abertura
    seguinte. Isto é deliberado — trocar com o programa aberto exigiria
    processo auxiliar e deixaria o usuário sem saber em que versão está.

    `irmas` são as outras ferramentas da família (`services/familia.py`). A
    cada verificação o manifesto de cada uma é consultado; a que estiver
    instalada ao lado e desatualizada é baixada em espera com o nome dela, e
    ela mesma aplica na própria partida.
    """

    def __init__(self, base_dir: Path, nome_exe: str, url_manifesto: str,
                 versao_atual: str, config: ConfigAtualizacao,
                 baixador=None, irmas: tuple[Ferramenta, ...] = ()):
        self.base_dir = Path(base_dir)
        self.nome_exe = nome_exe
        self.url_manifesto = url_manifesto
        self.versao_atual = versao_atual
        self.config = config
        self.irmas = tuple(irmas)
        self._baixador = baixador or _baixar_simples

        self._trava = threading.Lock()
        self._estado = OCIOSO if config.automatica else DESLIGADO
        self._mensagem = ""
        self._manifesto: Manifesto | None = None
        self._progresso = 0
        self._thread: threading.Thread | None = None
        # Laço periódico (`monitorar`), separado da `_thread` de uma verificação
        # avulsa para os dois não disputarem o mesmo guarda.
        self._monitor: threading.Thread | None = None
        self._parar_monitor = threading.Event()
        # GET condicional: por URL, o `ETag` da última resposta com corpo e o
        # manifesto que veio nela. `304` reaproveita o manifesto guardado.
        self._cache: dict[str, tuple[str | None, Manifesto]] = {}
        # Falhas seguidas de rede: dobram o intervalo do monitor até o teto.
        self._falhas = 0
        # Quando a marca `ultima_verificacao` foi gravada por último.
        self._ultimo_registro: datetime | None = None
        # Situação de cada irmã, pelo nome do exe, para a interface.
        self._familia: dict[str, dict] = {}

    # ── leitura para a interface ──

    @property
    def estado(self) -> dict:
        pendente = ler_pendente(self.base_dir, self.nome_exe)
        with self._trava:
            m = self._manifesto
            return {
                "estado": self._estado,
                "mensagem": self._mensagem,
                "versao_atual": self.versao_atual,
                "versao_nova": (pendente or {}).get("versao") or (m.versao if m else ""),
                "changelog": (pendente or {}).get("changelog")
                             or (list(m.changelog) if m else []),
                "obrigatoria": bool(m.obrigatoria) if m else False,
                "progresso": self._progresso,
                "automatica": bool(self.config.automatica),
                "pode_reverter": pode_reverter(self.base_dir, self.nome_exe),
                "pendente": bool(pendente),
                "intervalo_seg": int(self.intervalo_atual().total_seconds()),
                "familia": [dict(v) for v in self._familia.values()],
            }

    def _marcar(self, estado: str, mensagem: str = "") -> None:
        with self._trava:
            self._estado = estado
            self._mensagem = mensagem

    # ── verificação ──

    def deve_verificar(self) -> bool:
        """Dentro do intervalo, não — e **sempre** na primeira execução.

        Quem acabou de extrair o pacote de entrada está, de propósito, várias
        versões atrás: esperar para contar isso seria absurdo.
        """
        if not self.config.automatica:
            return False
        marca = (self.config.ultima_verificacao or "").strip()
        if not marca:
            return True
        try:
            quando = datetime.fromisoformat(marca)
        except ValueError:
            return True
        if quando.tzinfo is None:
            quando = quando.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) - quando >= INTERVALO_VERIFICACAO

    def intervalo_atual(self) -> timedelta:
        """O que o monitor espera até a próxima rodada: dobra a cada falha."""
        base = INTERVALO_VERIFICACAO.total_seconds()
        teto = INTERVALO_MAXIMO.total_seconds()
        return timedelta(seconds=min(teto, base * (2 ** self._falhas)))

    def _consultar(self, url: str) -> Manifesto:
        """Manifesto da URL, pelo GET condicional. Levanta `ErroAtualizacao`."""
        etag_guardado, guardado = self._cache.get(url, (None, None))
        manifesto, etag = consultar_se_mudou(url, etag_guardado)
        if manifesto is None:
            if guardado is None:
                # `304` sem nada guardado só acontece com etag alheio no
                # cache — impossível aqui, mas não se confia em "impossível".
                manifesto, etag = consultar_se_mudou(url, None)
            else:
                return guardado
        self._cache[url] = (etag, manifesto)
        return manifesto

    def _registrar(self) -> None:
        """Grava a marca no máximo uma vez por `INTERVALO_REGISTRO`."""
        agora = datetime.now(timezone.utc)
        if (self._ultimo_registro is not None
                and agora - self._ultimo_registro < INTERVALO_REGISTRO):
            return
        self._ultimo_registro = agora
        self.config.registrar_verificacao(agora.isoformat(timespec="seconds"))

    def verificar(self, forcado: bool = False) -> dict:
        """Consulta o manifesto — o próprio e o das irmãs. `forcado` ignora o
        intervalo e o desligado.

        Desligar o automático não é renunciar a atualizar: o botão "Verificar
        agora" continua valendo. Com ele desligado, as irmãs são só
        conferidas, nunca baixadas.
        """
        if not forcado and not self.deve_verificar():
            return self.estado

        self._marcar(VERIFICANDO)
        try:
            manifesto = self._consultar(self.url_manifesto)
        except ErroAtualizacao as erro:
            # Erro de verificação é mostrado, nunca engolido: "sempre em dia"
            # silencioso esconderia proxy bloqueando o GitHub.
            self._falhas += 1
            self._marcar(ERRO, str(erro))
            return self.estado

        self._falhas = 0
        with self._trava:
            self._manifesto = manifesto
        self._registrar()

        decisao = comparar(self.versao_atual, manifesto,
                           self.config.incluir_prerelease)
        if not decisao["atualizar"]:
            if decisao.get("bloqueada"):
                self._marcar(ERRO, decisao["motivo"])
            else:
                self._marcar(EM_DIA, decisao["motivo"])
        else:
            pendente = ler_pendente(self.base_dir, self.nome_exe)
            if pendente and pendente.get("versao") == manifesto.versao:
                self._marcar(PRONTO, "reinicie o programa para aplicar")
            else:
                self._marcar(DISPONIVEL, f"versão {manifesto.versao} disponível")

        self.verificar_irmas(baixar=self.config.automatica)
        return self.estado

    # ── a família ──

    def verificar_irmas(self, baixar: bool) -> list[dict]:
        """Confere cada irmã instalada ao lado e, se `baixar`, deixa em espera
        a que estiver desatualizada.

        Nunca levanta e nunca muda o estado próprio: irmã ausente, manifesto
        fora do ar ou exe sem versão legível são registrados na situação dela
        e a rodada segue. O que acontece com a irmã não é notícia sobre nós.
        """
        situacoes = []
        for irma in self.irmas:
            situacao = self._situacao_da_irma(irma, baixar)
            with self._trava:
                self._familia[irma.exe] = situacao
            situacoes.append(situacao)
        return situacoes

    def _situacao_da_irma(self, irma: Ferramenta, baixar: bool) -> dict:
        situacao = {"nome": irma.nome, "exe": irma.exe, "versao_atual": "",
                    "versao_nova": "", "estado": AUSENTE, "mensagem": ""}
        caminho = self.base_dir / irma.exe
        if not caminho.exists():
            situacao["mensagem"] = "não está instalado nesta pasta"
            return situacao

        versao = versao_do_exe(caminho)
        if not versao:
            situacao.update(estado=ERRO,
                            mensagem="não foi possível ler a versão do executável")
            return situacao
        situacao["versao_atual"] = versao

        try:
            manifesto = self._consultar(irma.url_manifesto)
        except ErroAtualizacao as erro:
            situacao.update(estado=ERRO, mensagem=str(erro))
            return situacao

        decisao = comparar(versao, manifesto, self.config.incluir_prerelease)
        if not decisao["atualizar"]:
            situacao.update(estado=EM_DIA, mensagem=decisao["motivo"])
            return situacao
        situacao["versao_nova"] = manifesto.versao

        pendente = ler_pendente(self.base_dir, irma.exe)
        em_espera = bool(pendente and pendente.get("versao") == manifesto.versao)

        if not em_espera:
            if not baixar:
                situacao.update(estado=DISPONIVEL,
                                mensagem=f"versão {manifesto.versao} disponível")
                return situacao
            try:
                em_espera = self._baixar_em_espera(irma.exe, manifesto)
            except ErroAtualizacao as erro:
                situacao.update(estado=ERRO, mensagem=str(erro))
                return situacao
            if not em_espera:
                situacao.update(estado=BAIXANDO,
                                mensagem="outro programa está baixando")
                return situacao

        # Em espera. Fechada, troca agora — ninguém está rodando o binário, e
        # uma irmã velha demais para ter atualizador nunca aplicaria sozinha.
        if baixar and not _exe_em_uso(caminho):
            if aplicar_pendente(self.base_dir, irma.exe) is not None:
                situacao.update(estado=ATUALIZADA,
                                versao_atual=manifesto.versao,
                                mensagem=f"atualizado para {manifesto.versao}")
                return situacao
        situacao.update(estado=PRONTO,
                        mensagem="baixada; entra quando o programa abrir")
        return situacao

    def instalar_irma(self, nome_exe: str) -> dict:
        """Baixa e põe na pasta uma irmã que não está instalada.

        É o "Instalar NebulaTIR" do Gerenciador: o satélite não roda sem ele, e
        quem tem o Gerenciador é quem o quer ao lado. Pedido explícito, nunca
        rodada automática — instalar programa que ninguém pediu não é
        atualização. Devolve a situação da irmã, como em `verificar_irmas`.
        """
        irma = next((f for f in self.irmas if f.exe.lower() == nome_exe.lower()),
                    None)
        if irma is None:
            return {"nome": nome_exe, "exe": nome_exe, "versao_atual": "",
                    "versao_nova": "", "estado": ERRO,
                    "mensagem": "não é uma ferramenta desta família"}
        situacao = {"nome": irma.nome, "exe": irma.exe, "versao_atual": "",
                    "versao_nova": "", "estado": ERRO, "mensagem": ""}
        caminho = self.base_dir / irma.exe
        try:
            if caminho.exists():
                raise ErroAtualizacao(f"{irma.exe} já está instalado.")
            manifesto = self._consultar(irma.url_manifesto)
            situacao["versao_nova"] = manifesto.versao
            if not self._baixar_em_espera(irma.exe, manifesto):
                raise ErroAtualizacao("outro programa está baixando")
            if aplicar_pendente(self.base_dir, irma.exe) is None:
                raise ErroAtualizacao(
                    f"não foi possível pôr {irma.exe} na pasta.")
        except ErroAtualizacao as erro:
            situacao["mensagem"] = str(erro)
        else:
            situacao.update(estado=ATUALIZADA, versao_atual=manifesto.versao,
                            mensagem=f"instalado na versão {manifesto.versao}")
        with self._trava:
            self._familia[irma.exe] = situacao
        return situacao

    # ── download ──

    def _baixar_em_espera(self, nome_exe: str, manifesto: Manifesto,
                          on_progress=None) -> bool:
        """Baixa, confere o hash, extrai o executável e deixa em espera com o
        nome de `nome_exe`. Devolve False se outra ferramenta já está baixando
        este mesmo executável. Levanta `ErroAtualizacao` em falha.
        """
        pasta = pasta_update(self.base_dir)
        pasta.mkdir(parents=True, exist_ok=True)
        caminho_zip = pasta / (manifesto.nome or f"{nome_exe}.zip")
        novo = arquivo_novo(self.base_dir, nome_exe)

        with TravaDownload(self.base_dir, nome_exe) as obtida:
            if not obtida:
                return False
            try:
                self._baixador(manifesto.url, caminho_zip, on_progress)

                obtido = sha256_do_arquivo(caminho_zip)
                if obtido != manifesto.sha256:
                    # Sem assinatura de código, este hash é a única prova de
                    # origem. O que não bate é descartado, nunca instalado.
                    raise ErroAtualizacao(
                        "O pacote baixado não confere com o hash publicado — "
                        "descartado.")

                _extrair_exe(caminho_zip, manifesto.exe, novo)
                limpar_marca_da_internet(novo)

                arquivo_pendente(self.base_dir, nome_exe).write_text(json.dumps({
                    "versao": manifesto.versao,
                    "exe": nome_exe,
                    "sha256_zip": manifesto.sha256,
                    "sha256_exe": sha256_do_arquivo(novo),
                    "changelog": list(manifesto.changelog),
                    "baixado_em": datetime.now(timezone.utc)
                                  .isoformat(timespec="seconds"),
                    "baixado_por": self.nome_exe,
                }, ensure_ascii=False, indent=2), encoding="utf-8")
            except ErroAtualizacao:
                descartar_pendente(self.base_dir, nome_exe)
                raise
            except OSError as erro:
                descartar_pendente(self.base_dir, nome_exe)
                raise ErroAtualizacao(
                    f"falha ao preparar a atualização: {erro}")
            finally:
                caminho_zip.unlink(missing_ok=True)
        return True

    def baixar(self) -> dict:
        """Baixa a própria atualização e deixa em espera."""
        with self._trava:
            manifesto = self._manifesto
        if manifesto is None:
            self._marcar(ERRO, "verifique antes de baixar")
            return self.estado

        def progresso(baixado, total):
            if total:
                with self._trava:
                    self._progresso = int(baixado * 100 / total)

        self._marcar(BAIXANDO)
        self._progresso = 0
        try:
            baixou = self._baixar_em_espera(self.nome_exe, manifesto, progresso)
        except ErroAtualizacao as erro:
            self._marcar(ERRO, str(erro))
            return self.estado

        if not baixou:
            # A irmã aberta viu a mesma versão e chegou antes. O pendente
            # aparece com o nosso nome quando ela terminar; a rodada seguinte
            # o encontra e marca "pronto".
            self._marcar(DISPONIVEL, "outro programa está baixando esta versão")
            return self.estado

        self._progresso = 100
        self._marcar(PRONTO, "reinicie o programa para aplicar")
        return self.estado

    # ── automático ──

    def em_segundo_plano(self, baixar_se_houver: bool = True) -> None:
        """Verifica (e baixa) sem travar a janela.

        Mesmo padrão da descoberta de ambientes: thread daemon disparada na
        partida. Morre com o processo — nada aqui merece segurar o fechamento.
        """
        if not self.config.automatica:
            return
        if self._thread and self._thread.is_alive():
            return

        def tarefa():
            try:
                estado = self.verificar()
                if baixar_se_houver and estado["estado"] == DISPONIVEL:
                    self.baixar()
            except Exception:      # noqa: BLE001
                log.exception("[UPD] verificação em segundo plano falhou.")

        self._thread = threading.Thread(target=tarefa, daemon=True,
                                        name="atualizacao")
        self._thread.start()

    def monitorar(self, intervalo: timedelta | None = None) -> None:
        """Repete a verificação enquanto o programa estiver aberto.

        Até 2026-09-04 só havia a checagem da partida: quem deixa o programa
        aberto o dia inteiro — que é o uso normal — nunca via versão publicada
        depois de abrir a janela.

        Roda em thread daemon própria, separada da `_thread` de
        `em_segundo_plano`, para não disputar aquele guarda de "já tem uma
        rodando" e travar o botão "Verificar agora".

        ⚠ Sem processo e sem janela: `consultar_se_mudou` é `urllib` puro. A
        verificação nunca dispara `subprocess` — o único `Popen` deste módulo
        é o relançar do executável, que só acontece ao aplicar a troca.

        Falha é silenciosa por decisão: rede corporativa, proxy e VPN caem o
        tempo todo, e um alarme a cada rodada seria ruído para algo que não é
        problema do usuário. Fica no log, e o intervalo dobra até o teto.

        `intervalo` fixo é para teste; sem ele, a espera é `intervalo_atual()`,
        que cresce com as falhas.
        """
        def espera() -> float:
            if intervalo is not None:
                segundos = intervalo.total_seconds()
            else:
                segundos = self.intervalo_atual().total_seconds()
            # Piso de 60 s: intervalo minúsculo por engano viraria laço quente
            # batendo no GitHub sem parar.
            return max(60.0, segundos)

        def laco():
            while not self._parar_monitor.wait(espera()):
                self._rodada_periodica()

        if self._monitor and self._monitor.is_alive():
            return
        self._monitor = threading.Thread(target=laco, daemon=True,
                                         name="atualizacao-monitor")
        self._monitor.start()

    def _rodada_periodica(self) -> None:
        """Uma passada do laço. Separado do `monitorar` para ser testável.

        Nunca levanta: exceção aqui mataria a thread e o programa passaria o
        resto do dia sem verificar, em silêncio.
        """
        if not self.config.automatica:
            return                     # desligado: só volta a dormir
        try:
            estado = self.verificar(forcado=True)
            if estado["estado"] == DISPONIVEL:
                self.baixar()
        except Exception:              # noqa: BLE001
            log.debug("[UPD] verificação periódica falhou.", exc_info=True)

    def parar_monitor(self) -> None:
        """Acorda o laço para ele sair. Usado no fechamento e nos testes."""
        self._parar_monitor.set()

    def descartar(self) -> dict:
        """Joga fora o que está em espera (o usuário recusou)."""
        descartar_pendente(self.base_dir, self.nome_exe)
        self._marcar(OCIOSO)
        return self.estado
