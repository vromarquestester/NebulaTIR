"""Execução das suítes do TIR.

Modelo de **fila**: todas as rotinas escolhidas entram numa fila e são
atribuídas à instância livre. Em sequencial há uma instância só, então a fila
vira uma lista ordenada — mas o desenho já é o do paralelo, para o segundo
caso não exigir reescrever isto.

Ciclo de cada rotina, como especificado:

    prepara a pasta → roda o lançador no venv do TIR → gera log e PNG →
    manda o Gerenciador restaurar o banco → espera terminar → próxima

Abortar mata o processo do teste e esvazia a fila; o que já rodou fica.
"""

from __future__ import annotations

import logging
import queue
import subprocess
import threading
import time
from pathlib import Path

from services import drivers, preparacao, venv_tir

log = logging.getLogger(__name__)

_SEM_JANELA = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# Estados de uma rotina na corrida — o mesmo vocabulário que a UI pinta.
NA_FILA = "fila"
RODANDO = "rodando"
OK = "ok"
FALHOU = "erro"
ABORTADO = "abortado"

# Prefixo que o lançador imprime a cada início e fim de caso. Mesmo valor de
# `tir_report.MARCA_PROGRESSO` — o módulo de lá roda no venv do TIR, noutro
# interpretador, então a constante não pode ser importada.
MARCA_PROGRESSO = "[nebula_caso]"

# Teto de tempo de UMA unidade de execução (rotina inteira ou fatia dela).
#
# Sem isto o lançador fica preso para sempre. Foi o que travou a corrida de
# 14/08: o AppServer de uma instância parou de atender, o TIR entrou no ciclo
# de `restart_browser` — que **não** fecha o Firefox anterior — e o lançador
# ficou mais de uma hora tentando. O slot nunca liberou, a corrida nunca
# terminou, e cada tentativa deixava mais um navegador comendo memória.
#
# O valor é generoso de propósito: uma rotina de verdade leva minutos, e um
# caso sozinho já levou 6. Isto é rede de segurança, não política de prazo.
LIMITE_UNIDADE_SEG = 45 * 60
INTERVALO_VIGIA_SEG = 5.0


class Execucao:
    """Uma corrida. Vive enquanto houver rotina na fila ou em execução."""

    def __init__(self, ambiente: str, rotinas: list[dict], config: dict,
                 estado_gerenciador, fila_eventos: queue.Queue,
                 instancias: int = 1, restaurar_banco: bool = True,
                 ambientes_por_slot: list[str] | None = None,
                 config_por_ambiente: dict | None = None,
                 religar_ambiente=None):
        self.ambiente = ambiente
        self.instancias = max(1, int(instancias))
        # Em paralelo cada trabalhador tem o SEU ambiente (e o seu banco), e é
        # nele que a restauração acontece. Em sequencial, todos apontam para o
        # ambiente principal.
        self._por_slot = list(ambientes_por_slot or [])
        self._config = config
        # Cada instância tem a SUA porta, logo a sua Url. Usar a config do
        # ambiente principal em todos os slots faria os três navegadores
        # baterem na mesma porta — e dois deles não teriam ninguém ouvindo.
        self._config_por_ambiente = dict(config_por_ambiente or {})
        self._estado = estado_gerenciador
        self._eventos = fila_eventos
        self._restaurar = restaurar_banco
        # Restaurar o banco encerra o AppServer daquele ambiente (o pipeline
        # precisa liberar os arquivos da base). Sem religar, a próxima rotina
        # do mesmo slot encontraria a porta fechada.
        self._religar = religar_ambiente

        self._fila: queue.Queue = queue.Queue()
        self._situacao: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._parar = threading.Event()
        self._processos: dict[str, subprocess.Popen] = {}
        self._threads: list[threading.Thread] = []
        self._rotinas = {r["rotina"]: r for r in rotinas}
        # Quantas unidades faltam por rotina. Só quando a última termina é que
        # o banco é restaurado e o relatório é montado — restaurar entre casos
        # da MESMA rotina não faz sentido: cada caso liga e desliga o que
        # precisa, e o dado de que ele depende veio da base congelada.
        self._pendentes: dict[str, int] = {}
        self._parciais: dict[str, Path] = {}
        # Unidades que o vigia derrubou por estourar o teto de tempo. A causa
        # tem que aparecer no relatório: "código 1" não diz nada.
        self._estourados: set[str] = set()
        # Árvore de acompanhamento: uma entrada por instância, com a rotina que
        # ela roda agora e o histórico de casos dela. É o que a pessoa olha
        # durante a corrida — a lista de rotinas só muda de estado no fim, e um
        # caso do TIR leva minutos.
        self._instancias: dict[int, dict] = {
            slot: {"slot": slot, "ambiente": self.ambiente_do_slot(slot),
                   "rotina": "", "caso": "", "estado": "ocioso", "casos": []}
            for slot in range(1, self.instancias + 1)
        }

        for rotina in rotinas:
            unidades = rotina.get("unidades") or [rotina.get("casos") or []]
            nome = rotina["rotina"]
            self._pendentes[nome] = len(unidades)
            for casos in unidades:
                self._fila.put({"rotina": rotina, "casos": list(casos)})
            self._situacao[nome] = {
                "rotina": nome, "modulo": rotina.get("modulo", ""),
                "estado": NA_FILA, "casos": rotina.get("casos", []),
                "log": "", "png": "", "mensagem": "",
                "dividida": len(unidades) > 1,
                "partes": len(unidades),
            }

    # ── leitura ──
    @property
    def ativa(self) -> bool:
        """Corrida em andamento.

        Abortar encerra a corrida **do ponto de vista do programa**, mesmo que
        alguma thread demore a morrer: o lançador do TIR deixa geckodriver e
        Firefox para trás, e a leitura da saída pode ficar pendurada. Amarrar
        `ativa` só à vida das threads deixava o botão Executar TIR desabilitado
        para sempre depois de um aborto.
        """
        if self._parar.is_set():
            return False
        return any(t.is_alive() for t in self._threads)

    def instantaneo(self) -> dict:
        with self._lock:
            itens = [dict(v) for v in self._situacao.values()]
            arvore = [{**v, "casos": [dict(c) for c in v["casos"]]}
                      for v in self._instancias.values()]
        return {
            "ambiente": self.ambiente,
            "ativa": self.ativa,
            "abortada": self._parar.is_set(),
            "instancias": self.instancias,
            # Uma entrada por instância: ambiente, rotina em execução e os
            # casos com o que já se sabe de cada um.
            "arvore": arvore,
            "rotinas": itens,
            "total": len(itens),
            "concluidas": sum(1 for i in itens
                              if i["estado"] in (OK, FALHOU, ABORTADO)),
        }

    # ── ciclo de vida ──
    def iniciar(self) -> None:
        log.info("[TIR] Corrida em %s: %d unidade(s) para %d instância(s) — %s",
                 self.ambiente, self._fila.qsize(), self.instancias,
                 ", ".join(self._por_slot) or self.ambiente)
        if self.instancias > 1 and self._fila.qsize() < self.instancias:
            self._emitir(f"Há {self._fila.qsize()} unidade(s) de trabalho para "
                         f"{self.instancias} instâncias: algumas vão ficar "
                         f"ociosas.", "WARNING")
        for indice in range(self.instancias):
            t = threading.Thread(target=self._trabalhar, args=(indice + 1,),
                                 name=f"tir-{indice + 1}", daemon=True)
            t.start()
            self._threads.append(t)

    def abortar(self) -> dict:
        """Para tudo: esvazia a fila e mata os processos em execução."""
        self._parar.set()
        try:
            while True:
                self._fila.get_nowait()
        except queue.Empty:
            pass

        mortos = 0
        with self._lock:
            processos = list(self._processos.items())
        for nome, proc in processos:
            if proc.poll() is None and _matar_arvore(proc):
                mortos += 1
                log.warning("[TIR] %s abortado.", nome)
            drivers.encerrar_do_lancador(proc.pid)
        # O aborto pode pegar o lançador entre um caso e outro, com o driver
        # já desligado do pai. A varredura de órfãos fecha essa brecha.
        drivers.encerrar_orfaos()
        self._marcar_pendentes_como_abortados()
        return {"ok": True, "mortos": mortos}

    def _marcar_pendentes_como_abortados(self) -> None:
        with self._lock:
            for item in self._situacao.values():
                if item["estado"] in (NA_FILA, RODANDO):
                    item["estado"] = ABORTADO
                    item["mensagem"] = "Interrompido pelo usuário."

    # ── trabalho ──
    def _anotar(self, rotina: str, **campos) -> None:
        with self._lock:
            self._situacao[rotina].update(campos)

    def _emitir(self, texto: str, nivel: str = "INFO") -> None:
        self._eventos.put({"kind": "log", "level": nivel, "text": texto})

    # ── árvore de acompanhamento ──
    def _slot_assume(self, slot: int, rotina: str, casos: list) -> None:
        """A instância pegou uma rotina: zera o caso e semeia a lista de casos.

        Os casos já entram como `fila`; o lançador vai anunciando qual começou
        e como terminou. Semear aqui é o que faz a árvore mostrar o tamanho da
        rotina desde o primeiro segundo, em vez de crescer linha a linha.
        """
        with self._lock:
            entrada = self._instancias.setdefault(
                slot, {"slot": slot, "casos": []})
            entrada.update({
                "ambiente": self.ambiente_do_slot(slot),
                "rotina": rotina, "caso": "", "estado": RODANDO,
                "casos": [{"nome": c, "estado": NA_FILA} for c in casos],
            })

    def _slot_caso(self, slot: int, caso: str, estado: str) -> None:
        with self._lock:
            entrada = self._instancias.get(slot)
            if entrada is None:
                return
            entrada["caso"] = caso if estado == RODANDO else ""
            for item in entrada["casos"]:
                if item["nome"] == caso:
                    item["estado"] = estado
                    break
            else:
                # Caso que o suite adicionou e a árvore não previa (rotina não
                # dividida, lista de casos desatualizada no disco).
                entrada["casos"].append({"nome": caso, "estado": estado})

    def _slot_libera(self, slot: int) -> None:
        with self._lock:
            entrada = self._instancias.get(slot)
            if entrada is None:
                return
            entrada["caso"] = ""
            entrada["estado"] = "ocioso"
            # Caso que ficou "rodando" quando o lançador morreu no meio não
            # pode ficar girando para sempre na tela.
            for item in entrada["casos"]:
                if item["estado"] == RODANDO:
                    item["estado"] = ABORTADO if self._parar.is_set() else FALHOU

    def ambiente_do_slot(self, slot: int) -> str:
        """Ambiente que o trabalhador `slot` usa (1-based)."""
        if 1 <= slot <= len(self._por_slot):
            return self._por_slot[slot - 1]
        return self.ambiente

    def _trabalhar(self, slot: int) -> None:
        pegas = 0
        while not self._parar.is_set():
            try:
                unidade = self._fila.get_nowait()
            except queue.Empty:
                # Sai no log: slot que nunca pegou nada é a assinatura de um
                # paralelismo que não aconteceu, e a tela sozinha não conta
                # essa história.
                log.info("[TIR] Slot %d (%s) encerrou após %d unidade(s).",
                         slot, self.ambiente_do_slot(slot), pegas)
                if not pegas:
                    self._emitir(f"Instância {slot} ({self.ambiente_do_slot(slot)}) "
                                 f"não recebeu trabalho: a fila já estava "
                                 f"vazia.", "WARNING")
                return
            pegas += 1
            log.info("[TIR] Slot %d (%s) pegou %s [%s].", slot,
                     self.ambiente_do_slot(slot), unidade["rotina"]["rotina"],
                     ", ".join(unidade["casos"]) or "rotina inteira")
            self._rodar_uma(slot, unidade["rotina"], unidade["casos"])

    def _rodar_uma(self, slot: int, rotina: dict, casos: list) -> None:
        nome = rotina["rotina"]
        ambiente = self.ambiente_do_slot(slot)
        dividida = self._situacao[nome].get("dividida", False)
        rotulo = f"{nome}:{casos[0]}" if dividida and casos else nome
        self._anotar(nome, estado=RODANDO)
        self._emitir(f"[FASE] Instância {slot} · {nome} — preparando")

        # A pasta é do ambiente BASE: o resultado do teste não pertence à
        # instância que calhou de rodá-lo, e separar por instância espalharia
        # tudo em árvores quase idênticas. Já a CONFIG é a da instância, com a
        # URL da porta dela.
        config = self._config_por_ambiente.get(ambiente, self._config)
        # Em sequencial o arquivo continua sendo `config.json`: só há um
        # leitor, e mudar o nome à toa complicaria a inspeção manual da pasta.
        # Em paralelo cada instância tem o seu — é o que impede duas fatias da
        # mesma rotina de sobrescrever a URL uma da outra.
        preparo = preparacao.preparar_rotina(
            self.ambiente, rotina, config,
            instancia="" if ambiente == self.ambiente else ambiente)
        if not preparo.get("ok"):
            self._anotar(nome, estado=FALHOU, mensagem=preparo.get("erro", ""))
            self._emitir(f"{nome}: {preparo.get('erro')}", "ERROR")
            return
        if preparo.get("faltando"):
            self._emitir(f"{nome}: faltando no pacote — "
                         f"{', '.join(preparo['faltando'])}", "WARNING")

        pasta = Path(preparo["pasta"])
        parcial = None
        if dividida:
            parcial = pasta / preparacao.NOME_PARCIAIS / f"{casos[0]}.json"
            self._parciais[nome] = parcial.parent

        # Rotina inteira: os casos da árvore são todos os do suite. Fatia: só
        # os que esta instância recebeu.
        self._slot_assume(slot, nome, casos if dividida
                          else (self._situacao[nome].get("casos") or casos))
        try:
            codigo = self._lancar(rotulo, pasta, Path(preparo["suite"]),
                                  casos if dividida else None, parcial, slot,
                                  Path(preparo["config"]))
        finally:
            self._slot_libera(slot)
        if self._parar.is_set():
            self._anotar(nome, estado=ABORTADO,
                         mensagem="Interrompido pelo usuário.")
            return

        with self._lock:
            self._pendentes[nome] -= 1
            faltam = self._pendentes[nome]
            if codigo != 0 and not self._situacao[nome].get("mensagem"):
                # Só quando o lançador não disse a causa: a mensagem dele é
                # sempre melhor que "código 2".
                self._situacao[nome]["mensagem"] = (
                    f"O lançador terminou com código {codigo}.")

        self._emitir(f"{rotulo}: {'concluído' if codigo == 0 else 'com falhas'} "
                     f"(código {codigo}).", "INFO" if codigo == 0 else "WARNING")

        if faltam > 0:
            # Outra instância ainda roda casos desta rotina. Restaurar o banco
            # agora derrubaria o ambiente dela no meio do teste, e o relatório
            # sairia incompleto.
            return

        pasta_log = Path(preparo["log"])
        if dividida:
            self._juntar_relatorio(nome, pasta, pasta_log)

        estado_final = OK if not self._situacao[nome].get("mensagem") else FALHOU
        self._anotar(
            nome,
            estado=estado_final,
            log=_mais_recente(pasta_log, "*.log"),
            png=_mais_recente(pasta_log, "*.png"),
        )
        self._restaurar_banco(nome, ambiente)

    def _lancar(self, nome: str, pasta: Path, suite: Path,
                casos: list | None = None, parcial: Path | None = None,
                slot: int = 0, config: Path | None = None) -> int:
        """Roda o lançador no venv do TIR, transmitindo a saída para o log."""
        python = venv_tir.python_do_venv()
        cmd = [str(python), "nebula_run.py", suite.name]
        if config is not None:
            # Explícito, porque a pasta é compartilhada entre as fatias da
            # mesma rotina: a busca automática acharia o config de outra
            # instância e os dois navegadores iriam para o mesmo AppServer.
            cmd += ["--config", str(config)]
        for caso in (casos or []):
            cmd += ["--somente", caso]
        if parcial is not None:
            # Execução dividida: cada fatia grava o próprio resultado e o
            # relatório é montado quando a última terminar.
            cmd += ["--parcial", str(parcial), "--sem-png"]
        self._emitir(f"[FASE] {nome} — executando o TIR")
        log.info("[TIR] %s: %s (em %s)", nome, " ".join(cmd), pasta)

        # `drivers/` na frente do PATH: o Selenium acha ali antes do que vem no
        # pacote do TIR, que envelhece.
        ambiente_proc = drivers.ambiente_com_drivers()
        if config is not None:
            # Fecha o último caminho pelo qual o config errado entraria: o
            # `tir_report` resolve a pasta de log relendo o config, e a busca
            # automática parte da pasta, que é compartilhada.
            ambiente_proc["TIR_CONFIG"] = str(config)

        try:
            proc = subprocess.Popen(
                cmd, cwd=str(pasta), stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                errors="replace", bufsize=1, creationflags=_SEM_JANELA,
                env=ambiente_proc)
        except OSError as e:
            self._emitir(f"{nome}: não foi possível iniciar o TIR — {e}", "ERROR")
            return -1

        with self._lock:
            self._processos[nome] = proc

        vigia = self._vigiar_prazo(nome, proc)

        motivo = ""
        for linha in proc.stdout:
            linha = linha.rstrip()
            if not linha:
                continue
            # Progresso não vai para o console: ele já está sendo desenhado na
            # árvore, e repetir cada início e fim de caso afogaria o log.
            if linha.startswith(MARCA_PROGRESSO):
                self._progresso(slot, linha)
                continue
            self._emitir(f"  {nome} | {linha}")
            # O lançador reporta a causa quando nem o primeiro caso rodou.
            if "[nebula_run] Motivo:" in linha:
                motivo = linha.split("Motivo:", 1)[1].strip()
        proc.wait()
        vigia.set()
        if nome in self._estourados:
            self._anotar(nome.split(":")[0],
                         mensagem=f"Passou de {LIMITE_UNIDADE_SEG // 60} min "
                                  f"sem terminar e foi encerrado.")
        elif motivo:
            self._anotar(nome.split(":")[0], mensagem=motivo)

        # Sempre — terminou bem, falhou ou foi abortado. Quando o teste morre
        # antes do fim, o TIR não chega no TearDown e deixa driver e navegador
        # headless vivos; sem esta linha eles se acumulam a cada tentativa.
        encerrados = drivers.encerrar_do_lancador(proc.pid)
        if encerrados:
            self._emitir(f"  {nome} | {encerrados} processo(s) de navegador "
                         f"encerrados.")

        with self._lock:
            self._processos.pop(nome, None)
        return proc.returncode

    def _vigiar_prazo(self, nome: str, proc: subprocess.Popen) -> threading.Event:
        """Mata o lançador que passou do teto e devolve o Event que a desliga.

        Precisa de thread própria: quem chama fica bloqueado lendo o `stdout`
        do processo, e um lançador travado não escreve nada — a leitura não
        volta sozinha. `taskkill /T` leva o driver e o navegador junto, senão
        eles seguram o `stdout` aberto e a leitura continua pendurada mesmo
        com o lançador morto.
        """
        pronto = threading.Event()

        def vigiar():
            limite = time.monotonic() + LIMITE_UNIDADE_SEG
            while not pronto.wait(INTERVALO_VIGIA_SEG):
                if proc.poll() is not None or self._parar.is_set():
                    return
                if time.monotonic() < limite:
                    continue
                with self._lock:
                    self._estourados.add(nome)
                self._emitir(
                    f"{nome}: passou de {LIMITE_UNIDADE_SEG // 60} min sem "
                    f"terminar. Encerrando para liberar a instância.", "ERROR")
                _matar_arvore(proc)
                drivers.encerrar_do_lancador(proc.pid)
                return

        threading.Thread(target=vigiar, name=f"prazo-{nome}",
                         daemon=True).start()
        return pronto

    def _progresso(self, slot: int, linha: str) -> None:
        """Lê `[nebula_caso] INICIO <caso>` / `FIM <caso> <ok|erro>`.

        É o único canal em tempo real que existe: o resultado da suite só é
        conhecido quando ela acaba, e cada caso do TIR leva minutos.
        """
        partes = linha.split()
        if len(partes) < 3:
            return
        evento, caso = partes[1], partes[2]
        if evento == "INICIO":
            self._slot_caso(slot, caso, RODANDO)
        elif evento == "FIM":
            resultado = partes[3] if len(partes) > 3 else "erro"
            self._slot_caso(slot, caso, OK if resultado == "ok" else FALHOU)

    def _juntar_relatorio(self, nome: str, pasta: Path,
                          pasta_log: Path) -> None:
        """Monta log e PNG únicos a partir das parciais da rotina dividida.

        Quem lê o relatório não precisa saber que a rotina foi repartida — a
        ordem é a do suite, não a de chegada das instâncias.

        `--out` é explícito porque numa rotina dividida não existe
        `config.json` na pasta, e sim um por instância: a busca automática do
        `tir_report` não teria de onde tirar o `LogFolder`.
        """
        parciais = self._parciais.get(nome)
        if parciais is None or not parciais.is_dir():
            return
        ordem = ",".join(self._situacao[nome].get("casos") or [])
        cmd = [str(venv_tir.python_do_venv()), "nebula_merge.py",
               str(parciais), "--ordem", ordem, "--out", str(pasta_log)]
        self._emitir(f"[FASE] {nome} — juntando o relatório das instâncias")
        try:
            proc = subprocess.run(cmd, cwd=str(pasta), capture_output=True,
                                  text=True, encoding="utf-8", errors="replace",
                                  creationflags=_SEM_JANELA, timeout=300)
        except (OSError, subprocess.SubprocessError) as e:
            self._emitir(f"{nome}: falha ao juntar o relatório — {e}", "ERROR")
            return
        for linha in (proc.stdout or "").splitlines():
            if linha.strip():
                self._emitir(f"  {nome} | {linha.strip()}")
        if proc.returncode != 0:
            self._emitir(f"{nome}: relatório unificado não foi gerado.", "WARNING")

    def _restaurar_banco(self, nome: str, ambiente: str) -> None:
        """Somente Banco no Gerenciador, e espera terminar antes da próxima.

        É o que devolve o ambiente ao estado inicial. Sem isso, o teste
        seguinte encontra os dados que o anterior deixou.

        Em paralelo restaura o banco **daquela instância** — restaurar o do
        ambiente principal não adiantaria nada para o slot que rodou.
        """
        if not self._restaurar or self._parar.is_set():
            return
        self._emitir(f"[FASE] {nome} — restaurando o banco de {ambiente}")
        pedido = self._estado.restaurar_banco(ambiente)
        if not pedido.get("ok"):
            self._emitir(f"Falha ao pedir a restauração: "
                         f"{pedido.get('erro', '')}", "ERROR")
            return
        espera = self._estado.esperar_ocioso(parar=self._parar.is_set)
        if not espera.get("ok"):
            self._emitir(f"Restauração: {espera.get('erro', '')}", "WARNING")
            return
        self._emitir(f"{nome}: banco restaurado.")

        # O AppServer daquele ambiente foi encerrado pela restauração: sem
        # religar, a próxima rotina da fila acha a porta fechada.
        if self._religar is not None and not self._parar.is_set():
            self._emitir(f"[FASE] Religando {ambiente}")
            volta = self._religar(ambiente)
            if not volta.get("ok"):
                self._emitir(f"{ambiente} não voltou: {volta.get('erro', '')}",
                             "ERROR")


def _matar_arvore(proc: subprocess.Popen) -> bool:
    """Encerra o processo E os filhos dele.

    `proc.kill()` mata só o lançador; o geckodriver e o Firefox que ele abriu
    continuam vivos — e, pior, seguram o `stdout` aberto, deixando a leitura da
    saída pendurada. `taskkill /T` leva a árvore inteira.
    """
    try:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                       capture_output=True, timeout=30,
                       creationflags=_SEM_JANELA)
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        proc.kill()          # reserva, e no caso de não ser Windows
    except OSError:
        pass
    return True


def _mais_recente(pasta: Path, padrao: str) -> str:
    try:
        arquivos = sorted(pasta.glob(padrao), key=lambda p: p.stat().st_mtime)
    except OSError:
        return ""
    return str(arquivos[-1]) if arquivos else ""


def preparar_ambiente_python(fila_eventos: queue.Queue) -> dict:
    """Cria o venv do TIR e atualiza o framework, avisando na tela."""
    fila_eventos.put({"kind": "log", "level": "INFO",
                      "text": "[FASE] Preparando o ambiente Python do TIR"})
    # Antes de começar, varre o que sobrou de corridas anteriores — inclusive
    # de sessões passadas do programa, que a limpeza por lançador não alcança.
    sobras = drivers.encerrar_orfaos()
    if sobras:
        fila_eventos.put({
            "kind": "log", "level": "WARNING",
            "text": f"{sobras} processo(s) de navegador de corridas anteriores "
                    f"encerrados antes de começar.",
        })
    resultado = venv_tir.preparar()
    if not resultado.get("ok"):
        fila_eventos.put({"kind": "log", "level": "ERROR",
                          "text": resultado.get("erro", "")})
        return resultado
    versao = venv_tir.versao_instalada()
    fila_eventos.put({"kind": "log", "level": "INFO",
                      "text": f"tir_framework {versao or '(versão desconhecida)'} pronto."})

    # `drivers/` nasce com o que o TIR traz e passa a mandar no PATH. Trocar o
    # arquivo ali é como atualizar o driver sem mexer no venv.
    drivers.semear(venv_tir.python_do_venv())
    for driver in drivers.listar():
        fila_eventos.put({"kind": "log", "level": "INFO",
                          "text": f"driver: {driver['versao'] or driver['nome']}"})
    return {**resultado, "versao_tir": versao, "drivers": drivers.listar()}
