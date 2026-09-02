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
    NebulaTIR.exe.old      versão anterior, para reverter
    update/
        pendente.json                 o que está em espera
        NebulaTIR.exe.new  o binário já baixado e conferido

Regras que atravessam o módulo:

- **Atualização nunca impede o programa de abrir.** Toda falha aqui degrada
  para "abre a versão que já estava"; nada nesta camada pode derrubar a
  partida.
- **O `sha256` é a única prova de origem.** Não há certificado de code
  signing: sem conferir o hash publicado pelo pipeline, atualização automática
  seria execução de binário arbitrário baixado da internet. Zip que não bate é
  descartado, nunca instalado.
- **Nada é trocado sem o usuário reabrir o programa.** A troca acontece na
  partida, antes da janela subir.
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

log = logging.getLogger(__name__)

# Versão do formato do latest.json que este código entende. Manifesto de
# esquema maior é IGNORADO em vez de interpretado pela metade: um campo novo
# com significado desconhecido pode ser exatamente o que impede a instalação.
ESQUEMA_SUPORTADO = 1

PASTA_UPDATE = "update"
ARQUIVO_PENDENTE = "pendente.json"
SUFIXO_NOVO = ".new"
SUFIXO_ANTIGO = ".old"

INTERVALO_VERIFICACAO = timedelta(hours=24)
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


def consultar(url: str, timeout: int = TIMEOUT_MANIFESTO) -> Manifesto:
    """Lê o manifesto da vitrine.

    ⚠ `raw.githubusercontent.com` tem cache de CDN de alguns minutos: publicar
    e o programa não ver na hora é normal, não defeito.
    """
    req = urllib.request.Request(
        url, headers={"User-Agent": "atualizador", "Cache-Control": "no-cache"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            bruto = r.read().decode("utf-8")
    except (urllib.error.URLError, TimeoutError, OSError) as erro:
        # Rede corporativa, proxy e VPN fora do ar caem aqui. É falha de
        # verificação, não do programa — quem chama mostra e segue.
        raise ErroAtualizacao(f"Não foi possível consultar atualizações: {erro}")

    try:
        return Manifesto.de_dados(json.loads(bruto))
    except ValueError as erro:
        raise ErroAtualizacao(f"Manifesto ilegível: {erro}")


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


def ler_pendente(base_dir: Path) -> dict | None:
    arquivo = pasta_update(base_dir) / ARQUIVO_PENDENTE
    try:
        dados = json.loads(arquivo.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return dados if isinstance(dados, dict) else None


def descartar_pendente(base_dir: Path) -> None:
    """Apaga o que estava em espera. Usado quando ele não serve mais."""
    pasta = pasta_update(base_dir)
    (pasta / ARQUIVO_PENDENTE).unlink(missing_ok=True)
    for arquivo in pasta.glob(f"*{SUFIXO_NOVO}"):
        arquivo.unlink(missing_ok=True)


def aplicar_pendente(base_dir: Path, nome_exe: str) -> Path | None:
    """Troca o executável, se houver um em espera. Devolve o novo, ou None.

    Chamar **antes** de subir a janela e depois de garantir elevação — a pasta
    do programa costuma exigir administrador para escrita.

    Qualquer falha desfaz o que deu para desfazer e devolve None: o programa
    abre na versão que já estava. Atualização que impede a abertura é pior que
    atualização que não acontece.
    """
    base_dir = Path(base_dir)
    pendente = ler_pendente(base_dir)
    if not pendente:
        return None

    novo = pasta_update(base_dir) / f"{nome_exe}{SUFIXO_NOVO}"
    atual = base_dir / nome_exe
    antigo = base_dir / f"{nome_exe}{SUFIXO_ANTIGO}"

    if not novo.exists() or not atual.exists():
        log.warning("[UPD] pendente sem arquivo — descartado.")
        descartar_pendente(base_dir)
        return None

    # O hash guardado é o do EXE já extraído, não o do zip: entre o download e
    # a próxima abertura o arquivo em espera fica no disco, e é essa janela que
    # a conferência aqui cobre.
    esperado = str(pendente.get("sha256_exe") or "").lower()
    if esperado and sha256_do_arquivo(novo) != esperado:
        log.error("[UPD] o arquivo em espera não confere com o hash — descartado.")
        descartar_pendente(base_dir)
        return None

    try:
        antigo.unlink(missing_ok=True)
    except OSError:
        # Ainda mapeado por outro processo. Sem lugar para guardar a versão
        # anterior, não se troca: ficar sem rollback é pior que adiar um dia.
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
        try:
            antigo.rename(atual)
        except OSError:
            log.critical("[UPD] o executável ficou como %s. Renomeie à mão.",
                         antigo.name)
        return None

    descartar_pendente(base_dir)
    log.info("[UPD] versão %s aplicada.", pendente.get("versao"))
    return atual


def limpar_antigo(base_dir: Path, nome_exe: str) -> bool:
    """Apaga o `.old`. Só funciona depois que a versão nova já subiu uma vez.

    No run em que a troca acontece o `.old` ainda está mapeado pelo processo em
    execução, e o Windows recusa a exclusão. Por isso a limpeza é sempre da
    abertura seguinte.
    """
    antigo = Path(base_dir) / f"{nome_exe}{SUFIXO_ANTIGO}"
    if not antigo.exists():
        return False
    try:
        antigo.unlink()
        log.info("[UPD] %s removido.", antigo.name)
        return True
    except OSError:
        return False


def pode_reverter(base_dir: Path, nome_exe: str) -> bool:
    return (Path(base_dir) / f"{nome_exe}{SUFIXO_ANTIGO}").exists()


def reverter(base_dir: Path, nome_exe: str) -> bool:
    """Volta para a versão anterior, trocando os dois de lugar.

    É a rede de segurança para o caso de a versão nova não servir: não há como
    detectar de dentro dela que ela mesma quebrou, então a saída é o usuário
    pedir a volta.
    """
    base_dir = Path(base_dir)
    atual = base_dir / nome_exe
    antigo = base_dir / f"{nome_exe}{SUFIXO_ANTIGO}"
    if not antigo.exists():
        return False

    intermediario = base_dir / f"{nome_exe}.revertendo"
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


def relancar(caminho_exe: Path) -> None:
    """Sobe o executável novo e deixa este processo terminar.

    Já estamos elevados quando isto roda, então o filho herda a elevação sem
    passar de novo pelo UAC.
    """
    try:
        subprocess.Popen([str(caminho_exe)], close_fds=True)
    except OSError as erro:
        log.error("[UPD] não foi possível relançar: %s", erro)


def preparar_partida(base_dir: Path, nome_exe: str) -> Path | None:
    """Rotina de partida: limpa o `.old` e aplica o que estiver em espera.

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
    """Verifica, baixa em espera e informa a interface.

    Nada aqui troca binário: a troca é do `preparar_partida`, na abertura
    seguinte. Isto é deliberado — trocar com o programa aberto exigiria
    processo auxiliar e deixaria o usuário sem saber em que versão está.
    """

    def __init__(self, base_dir: Path, nome_exe: str, url_manifesto: str,
                 versao_atual: str, config: ConfigAtualizacao,
                 baixador=None):
        self.base_dir = Path(base_dir)
        self.nome_exe = nome_exe
        self.url_manifesto = url_manifesto
        self.versao_atual = versao_atual
        self.config = config
        self._baixador = baixador or _baixar_simples

        self._trava = threading.Lock()
        self._estado = OCIOSO if config.automatica else DESLIGADO
        self._mensagem = ""
        self._manifesto: Manifesto | None = None
        self._progresso = 0
        self._thread: threading.Thread | None = None

    # ── leitura para a interface ──

    @property
    def estado(self) -> dict:
        pendente = ler_pendente(self.base_dir)
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
            }

    def _marcar(self, estado: str, mensagem: str = "") -> None:
        with self._trava:
            self._estado = estado
            self._mensagem = mensagem

    # ── verificação ──

    def deve_verificar(self) -> bool:
        """Uma vez por dia — e **sempre** na primeira execução.

        Quem acabou de extrair o pacote de entrada está, de propósito, várias
        versões atrás: esperar até amanhã para contar isso seria absurdo.
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

    def verificar(self, forcado: bool = False) -> dict:
        """Consulta o manifesto. `forcado` ignora o intervalo e o desligado.

        Desligar o automático não é renunciar a atualizar: o botão "Verificar
        agora" continua valendo.
        """
        if not forcado and not self.deve_verificar():
            return self.estado

        self._marcar(VERIFICANDO)
        try:
            manifesto = consultar(self.url_manifesto)
        except ErroAtualizacao as erro:
            # Erro de verificação é mostrado, nunca engolido: "sempre em dia"
            # silencioso esconderia proxy bloqueando o GitHub.
            self._marcar(ERRO, str(erro))
            return self.estado

        with self._trava:
            self._manifesto = manifesto
        self.config.registrar_verificacao(
            datetime.now(timezone.utc).isoformat(timespec="seconds"))

        decisao = comparar(self.versao_atual, manifesto,
                           self.config.incluir_prerelease)
        if not decisao["atualizar"]:
            if decisao.get("bloqueada"):
                self._marcar(ERRO, decisao["motivo"])
            else:
                self._marcar(EM_DIA, decisao["motivo"])
            return self.estado

        pendente = ler_pendente(self.base_dir)
        if pendente and pendente.get("versao") == manifesto.versao:
            self._marcar(PRONTO, "reinicie o programa para aplicar")
            return self.estado

        self._marcar(DISPONIVEL, f"versão {manifesto.versao} disponível")
        return self.estado

    # ── download ──

    def baixar(self) -> dict:
        """Baixa, confere o hash, extrai o executável e deixa em espera."""
        with self._trava:
            manifesto = self._manifesto
        if manifesto is None:
            self._marcar(ERRO, "verifique antes de baixar")
            return self.estado

        pasta = pasta_update(self.base_dir)
        pasta.mkdir(parents=True, exist_ok=True)
        caminho_zip = pasta / (manifesto.nome or f"{self.nome_exe}.zip")

        def progresso(baixado, total):
            if total:
                with self._trava:
                    self._progresso = int(baixado * 100 / total)

        self._marcar(BAIXANDO)
        self._progresso = 0
        try:
            self._baixador(manifesto.url, caminho_zip, progresso)

            obtido = sha256_do_arquivo(caminho_zip)
            if obtido != manifesto.sha256:
                # Sem assinatura de código, este hash é a única prova de
                # origem. O que não bate é descartado, nunca instalado.
                raise ErroAtualizacao(
                    "O pacote baixado não confere com o hash publicado — "
                    "descartado.")

            novo = pasta / f"{self.nome_exe}{SUFIXO_NOVO}"
            _extrair_exe(caminho_zip, manifesto.exe, novo)
            limpar_marca_da_internet(novo)

            (pasta / ARQUIVO_PENDENTE).write_text(json.dumps({
                "versao": manifesto.versao,
                "exe": self.nome_exe,
                "sha256_zip": manifesto.sha256,
                "sha256_exe": sha256_do_arquivo(novo),
                "changelog": list(manifesto.changelog),
                "baixado_em": datetime.now(timezone.utc)
                              .isoformat(timespec="seconds"),
            }, ensure_ascii=False, indent=2), encoding="utf-8")
        except ErroAtualizacao as erro:
            descartar_pendente(self.base_dir)
            self._marcar(ERRO, str(erro))
            return self.estado
        except OSError as erro:
            descartar_pendente(self.base_dir)
            self._marcar(ERRO, f"falha ao preparar a atualização: {erro}")
            return self.estado
        finally:
            caminho_zip.unlink(missing_ok=True)

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

    def descartar(self) -> dict:
        """Joga fora o que está em espera (o usuário recusou)."""
        descartar_pendente(self.base_dir)
        self._marcar(OCIOSO)
        return self.estado
