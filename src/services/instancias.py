"""Ambientes paralelos criados pelo NebulaTIR.

Guarda o que o Gerenciador não guarda: quais ambientes **este** programa
clonou, com que portas, em que pastas e sob que PIDs. Sem esse registro não há
como parar uma instância sozinha, excluir só o que foi criado aqui, nem saber
depois o que uma instância deixou espalhado pelo disco.

Regra que atravessa o módulo: **o ambiente principal nunca entra.** Ele é do
Gerenciador; o NebulaTIR só mexe no que ele mesmo gerou.

O PID é registrado por instância porque parar "todos os appserver.exe" mataria
também o ambiente que o usuário abriu à mão para outra coisa.

**Onde o arquivo mora.** Antes era `config/instancias.json`, ao lado do `.exe`
— cada pasta com o executável era uma instalação, e trocar de pasta perdia o
registro enquanto os ambientes continuavam no disco ocupando espaço. Agora ele
vive em `<base_path>\\NebulaInstancia\\instances.json`, junto dos ambientes que
descreve, e o registro antigo é migrado na primeira leitura.

`base_path` vem do Gerenciador (é o mesmo que ele usa para instalar ambiente).
Enquanto ele não responde, o registro cai no arquivo local — sem isso, abrir o
programa offline apagaria a memória do que já foi criado.

O DSN de ODBC não é guardado: o clone cria o DSN com o **nome do banco**
(`clonar_dsn_odbc(nome_dsn_novo=novo_banco)`), então guardar de novo criaria
duas verdades sobre o mesmo dado.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from datetime import datetime
from pathlib import Path

from services.recursos import pasta_do_programa

log = logging.getLogger(__name__)

# Pasta que passa a abrigar as instâncias e o registro delas, dentro da mesma
# raiz onde o Gerenciador instala os ambientes (`base_path`, ex.: C:\TOTVS).
PASTA_INSTANCIAS = "NebulaInstancia"
ARQUIVO = "instances.json"
# Registro antigo, ao lado do executável. Só é lido para migrar.
ARQUIVO_LEGADO = "instancias.json"

CRIADA = "criada"        # clonada, nunca subiu
RODANDO = "rodando"
PARADA = "parada"

_SEM_JANELA = getattr(subprocess, "CREATE_NO_WINDOW", 0)


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
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = kernel32.OpenProcess(0x1000, False, pid)
    if not handle:
        return False
    try:
        codigo = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(codigo)):
            return True
        return codigo.value == 259      # STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def _matar(pid: int) -> bool:
    if not pid or not _pid_vivo(pid):
        return False
    try:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                       capture_output=True, timeout=30,
                       creationflags=_SEM_JANELA)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


# O que o registro guarda sobre o disco. Lista fechada de propósito: campo
# solto vindo do Gerenciador entraria no arquivo sem ninguém decidir por isso.
CAMPOS_CAMINHO = ("pasta", "workspace_banco", "temp_pai", "base_path")


def _caminhos_limpos(caminhos: dict | None) -> dict:
    """Só os campos conhecidos, como texto, e sem os vazios."""
    return {campo: str(valor)
            for campo, valor in (caminhos or {}).items()
            if campo in CAMPOS_CAMINHO and str(valor or "").strip()}


def pasta_raiz(base_path: str | Path) -> Path:
    """`<base_path>\\NebulaInstancia` — onde as instâncias e o registro moram."""
    return Path(base_path) / PASTA_INSTANCIAS


def caminho_do_registro(base_path: str | Path) -> Path:
    return pasta_raiz(base_path) / ARQUIVO


def pasta_da_instancia(base_path: str | Path, ambiente: str) -> Path:
    return pasta_raiz(base_path) / ambiente


def arquivo_legado() -> Path:
    """Registro antigo, ao lado do executável."""
    return pasta_do_programa() / "config" / ARQUIVO_LEGADO


class Instancias:
    """Registro em `<base_path>\\NebulaInstancia\\instances.json`.

    `base_path` pode ser um valor fixo ou um chamável — na Api ele vem do
    Gerenciador, que só responde depois do boot. Enquanto não houver resposta,
    o registro usa o arquivo local e migra sozinho quando o caminho aparecer.
    """

    def __init__(self, arquivo: Path | None = None, base_path=None):
        # Caminho explícito manda em tudo (é o que os testes usam) e desliga a
        # migração: quem aponta o arquivo à mão não quer nada acontecendo atrás.
        self._fixo = Path(arquivo) if arquivo else None
        self._base_path = base_path
        self._arquivo = self._fixo or arquivo_legado()
        self._itens: list[dict] = self._carregar()

    # ── onde o arquivo mora ──
    def _base_atual(self) -> str:
        if self._fixo is not None:
            return ""
        base = self._base_path() if callable(self._base_path) else self._base_path
        return str(base or "").strip()

    @property
    def arquivo(self) -> Path:
        """Arquivo em uso agora, já resolvido e migrado se for o caso."""
        self._resolver()
        return self._arquivo

    def _resolver(self) -> None:
        """Passa a usar o registro do `base_path` assim que ele for conhecido.

        Migra o arquivo antigo uma vez, e só quando o novo ainda não existe: o
        registro que já vive junto dos ambientes é mais recente que o que ficou
        para trás na pasta do executável.
        """
        base = self._base_atual()
        if not base:
            return
        destino = caminho_do_registro(base)
        if destino == self._arquivo:
            return

        antigo = self._arquivo
        self._arquivo = destino
        if destino.exists():
            self._itens = self._carregar()
            return

        if self._itens:
            self._salvar()
            log.info("[INSTANCIAS] Registro migrado de %s para %s (%d item(ns)).",
                     antigo, destino, len(self._itens))
            # Renomeia o antigo: deixá-lo legível faria a próxima instalação
            # sem `base_path` reviver um registro já superado.
            try:
                antigo.replace(antigo.with_suffix(antigo.suffix + ".migrado"))
            except OSError:
                pass
        else:
            self._itens = self._carregar()

    # ── persistência ──
    def _carregar(self) -> list[dict]:
        try:
            dados = json.loads(self._arquivo.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        itens = dados.get("instancias") if isinstance(dados, dict) else dados
        return [i for i in (itens or []) if isinstance(i, dict) and i.get("ambiente")]

    def _salvar(self) -> None:
        self._arquivo.parent.mkdir(parents=True, exist_ok=True)
        self._arquivo.write_text(
            json.dumps({"instancias": self._itens}, ensure_ascii=False, indent=2),
            encoding="utf-8")

    # ── leitura ──
    def listar(self, origem: str | None = None) -> list[dict]:
        """Instâncias com o estado dos processos conferido no ato.

        O PID guardado pode ter morrido entre uma abertura e outra — devolver
        "rodando" sem conferir faria o usuário tentar parar um fantasma.
        """
        self._resolver()
        saida = []
        for item in self._itens:
            if origem and item.get("origem") != origem:
                continue
            copia = dict(item)
            vivos = {papel: _pid_vivo(int(copia.get("pids", {}).get(papel) or 0))
                     for papel in ("appserver", "dbaccess")}
            copia["vivos"] = vivos
            copia["estado"] = RODANDO if any(vivos.values()) else (
                PARADA if copia.get("estado") == RODANDO else copia.get("estado", CRIADA))
            saida.append(copia)
        return saida

    def nomes(self, origem: str | None = None) -> list[str]:
        return [i["ambiente"] for i in self.listar(origem)]

    def contem(self, ambiente: str) -> bool:
        self._resolver()
        return any(i["ambiente"] == ambiente for i in self._itens)

    def por_nome(self, ambiente: str) -> dict | None:
        self._resolver()
        return next((dict(i) for i in self._itens if i["ambiente"] == ambiente), None)

    # ── escrita ──
    def registrar(self, *, ambiente: str, origem: str, slot: int,
                  banco: str, portas: dict, caminhos: dict | None = None) -> dict:
        """Anota um ambiente recém-clonado.

        `caminhos` guarda onde a instância deixou coisa no disco: a pasta do
        ambiente, o workspace com MDF/LDF anexados e o temp do ambiente-pai,
        que é a fonte neutra do dia. É o que permite limpar de verdade depois,
        mesmo que o ambiente já tenha sumido do Gerenciador.
        """
        self._resolver()
        if self.contem(ambiente):
            return {"ok": False, "erro": f"'{ambiente}' já está registrado."}
        self._itens.append({
            "ambiente": ambiente,
            "origem": origem,
            "slot": slot,
            "banco": banco,
            "portas": dict(portas or {}),
            **_caminhos_limpos(caminhos),
            "pids": {},
            "estado": CRIADA,
            "criado_em": datetime.now().isoformat(timespec="seconds"),
        })
        self._salvar()
        log.info("[PARALELO] Instância %d registrada: %s (banco %s).",
                 slot, ambiente, banco)
        return {"ok": True, "ambiente": ambiente}

    def anotar_caminhos(self, ambiente: str, caminhos: dict) -> dict:
        """Completa os caminhos de uma instância já registrada.

        As instâncias criadas antes desta versão não têm nenhum: eles são
        preenchidos quando o Gerenciador responde. Só grava o que mudou — sem
        isso, toda listagem reescreveria o arquivo.
        """
        self._resolver()
        novos = _caminhos_limpos(caminhos)
        if not novos:
            return {"ok": True, "alterado": False}
        for item in self._itens:
            if item["ambiente"] != ambiente:
                continue
            mudou = {c: v for c, v in novos.items() if item.get(c) != v}
            if mudou:
                item.update(mudou)
                self._salvar()
            return {"ok": True, "alterado": bool(mudou)}
        return {"ok": False, "erro": "Instância não registrada."}

    def anotar_pid(self, ambiente: str, papel: str, pid: int) -> dict:
        self._resolver()
        for item in self._itens:
            if item["ambiente"] == ambiente:
                item.setdefault("pids", {})[papel] = int(pid)
                item["estado"] = RODANDO
                self._salvar()
                return {"ok": True}
        return {"ok": False, "erro": "Instância não registrada."}

    def parar(self, ambientes: list[str]) -> dict:
        """Encerra os processos das instâncias pedidas — só dessas.

        Matar por nome de imagem (`taskkill /IM appserver.exe`) derrubaria
        também o ambiente que o usuário subiu à mão. Por isso vai por PID.
        """
        self._resolver()
        parados, ignorados = [], []
        for nome in ambientes or []:
            item = next((i for i in self._itens if i["ambiente"] == nome), None)
            if item is None:
                ignorados.append(nome)
                continue
            mortos = 0
            for papel, pid in list(item.get("pids", {}).items()):
                if _matar(int(pid or 0)):
                    mortos += 1
                item["pids"][papel] = 0
            item["estado"] = PARADA
            parados.append({"ambiente": nome, "processos": mortos})
        self._salvar()
        if parados:
            log.info("[PARALELO] Paradas: %s",
                     ", ".join(p["ambiente"] for p in parados))
        return {"ok": True, "parados": parados, "ignorados": ignorados}

    def remover(self, ambientes: list[str]) -> dict:
        """Tira do registro (o ambiente em si é removido pelo Gerenciador)."""
        self._resolver()
        alvos = set(ambientes or [])
        antes = len(self._itens)
        self._itens = [i for i in self._itens if i["ambiente"] not in alvos]
        self._salvar()
        return {"ok": True, "removidos": antes - len(self._itens)}

    def proximo_slot(self, origem: str) -> int:
        self._resolver()
        usados = {i.get("slot", 0) for i in self._itens if i.get("origem") == origem}
        slot = 1
        while slot in usados:
            slot += 1
        return slot
