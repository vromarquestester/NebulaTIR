"""Cliente do canal loopback do Gerenciador de Ambientes.

O NebulaTIR não lê o `bancos_config.ini` nem mede VPN/SQL por conta própria:
tudo vem do Gerenciador, que é a fonte da verdade. Dois motivos — evitar dois
processos disputando o mesmo INI, e evitar que as duas janelas mostrem estados
divergentes do mesmo ambiente.

Descoberta
----------
O Gerenciador publica `%LOCALAPPDATA%/GerenciadorAmbientes/bridge.json` com
porta e token da sessão (ver `webui/bridge_server.py` do outro projeto). O
arquivo é relido a cada ciclo: a porta muda a cada abertura do Gerenciador, e
cachear o valor deixaria o NebulaTIR falando com uma porta morta.

`EstadoGerenciador` mantém o cache que a UI consulta por polling. `online` é
verdade só com um 200 dentro do timeout — qualquer falha derruba na hora, sem
histerese, porque o gate da interface depende disso para travar os botões
assim que o Gerenciador fecha.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

log = logging.getLogger(__name__)

HEADER_TOKEN = "X-Nebula-Token"
NOME_APP = "gerenciador-ambientes"
TIMEOUT_SEG = 1.5
INTERVALO_POLL_SEG = 2.0

# Quanto tempo o canal pode ficar sem responder antes de o link ser dado como
# perdido. O Gerenciador trava em operações longas — restaurar banco faz
# attach de MDF/LDF de vários GB — e some do ar por dezenas de segundos. Sem
# tolerância, o NebulaTIR marcava offline no primeiro timeout e derrubava a
# corrida inteira por causa de uma indisponibilidade passageira.
TOLERANCIA_SEG = 90.0


def caminho_registro() -> Path:
    """`bridge.json` — precisa bater com `bridge_server.caminho_registro()`."""
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or ""
    raiz = Path(base) if base else Path.home()
    return raiz / "GerenciadorAmbientes" / "bridge.json"


def _pid_vivo(pid: int) -> bool:
    """Um crash do Gerenciador deixa o registro para trás; o PID desempata.

    Via Win32 e não `tasklist`: o utilitário leva ~2 s nesta máquina e a
    primeira leitura do estado é síncrona no boot — seria a janela abrindo
    travada. `OpenProcess` responde em microssegundos.
    """
    if not pid:
        return False
    if os.name != "nt":
        try:
            os.kill(pid, 0)
            return True
        except (OSError, ProcessLookupError):
            return False

    import ctypes
    from ctypes import wintypes

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return False  # não existe, ou é de outro usuário — em ambos, não é o nosso
    try:
        codigo = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(codigo)):
            return True  # sem resposta clara, deixa o HTTP decidir
        return codigo.value == STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


class GerenciadorOffline(Exception):
    """Canal indisponível: Gerenciador fechado, token velho ou timeout."""


class GerenciadorClient:
    """Chamadas cruas ao canal. Sem cache — quem cacheia é o EstadoGerenciador."""

    def __init__(self, registro: Path | None = None):
        self._registro = registro  # injetável no teste
        self._pid_cache: tuple[tuple, bool] | None = None

    def _local_do_registro(self) -> Path:
        return self._registro or caminho_registro()

    def _pid_confere(self, dados: dict) -> bool:
        """Confere o PID uma vez por sessão do Gerenciador, não a cada chamada.

        O `tasklist` custa um processo novo; com polling de 2 s isso seria
        dezenas de processos por minuto para responder sempre a mesma coisa. A
        assinatura (porta + pid) só muda quando o Gerenciador reabre — é aí que
        vale reconferir.
        """
        assinatura = (dados.get("port"), dados.get("pid"))
        if self._pid_cache and self._pid_cache[0] == assinatura:
            return self._pid_cache[1]
        vivo = _pid_vivo(int(dados.get("pid") or 0))
        self._pid_cache = (assinatura, vivo)
        return vivo

    def descobrir(self) -> dict | None:
        """Lê porta e token da sessão atual do Gerenciador.

        O PID desempata o caso do registro órfão: sem ele, um `bridge.json`
        deixado por um crash faria o NebulaTIR mandar o token da sessão para
        qualquer processo que tivesse herdado aquela porta.
        """
        arquivo = self._local_do_registro()
        try:
            dados = json.loads(arquivo.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if dados.get("app") != NOME_APP or not dados.get("port"):
            return None
        if not self._pid_confere(dados):
            return None
        return dados

    def _get(self, rota: str) -> dict:
        registro = self.descobrir()
        if not registro:
            raise GerenciadorOffline("Gerenciador de Ambientes não está aberto.")
        url = f"http://127.0.0.1:{registro['port']}{rota}"
        req = urllib.request.Request(url)
        req.add_header(HEADER_TOKEN, registro.get("token", ""))
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_SEG) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 401:
                raise GerenciadorOffline("Token do canal recusado — reabra o "
                                         "Gerenciador de Ambientes.")
            raise GerenciadorOffline(f"Canal respondeu {e.code}.")
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise GerenciadorOffline(f"Canal inacessível: {e}")

    def _post(self, rota: str, corpo: dict) -> dict:
        registro = self.descobrir()
        if not registro:
            raise GerenciadorOffline("Gerenciador de Ambientes não está aberto.")
        dados = json.dumps(corpo, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            f"http://127.0.0.1:{registro['port']}{rota}", data=dados, method="POST")
        req.add_header(HEADER_TOKEN, registro.get("token", ""))
        req.add_header("Content-Type", "application/json; charset=utf-8")
        try:
            # Timeout maior que o de leitura: a Api do Gerenciador responde
            # assim que dispara a thread, mas o caminho é mais longo.
            with urllib.request.urlopen(req, timeout=TIMEOUT_SEG * 4) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise GerenciadorOffline(f"Canal respondeu {e.code}.")
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise GerenciadorOffline(f"Canal inacessível: {e}")

    def health(self) -> dict:
        return self._get("/health")

    def restaurar_banco(self, ambiente: str) -> dict:
        """Dispara o Somente Banco no Gerenciador. Retorna sem esperar o fim."""
        return self._post("/executar", {"ambiente": ambiente,
                                        "modo": "somente_banco"})

    def restaurar_rpo(self, ambiente: str) -> dict:
        """Somente RPO: repõe o RPO baixado, apagando pacote e fonte aplicados."""
        return self._post("/executar", {"ambiente": ambiente,
                                        "modo": "somente_rpo"})

    def parar_ambiente(self, ambiente: str) -> dict:
        return self._post("/parar", {"ambiente": ambiente})

    def remover_ambiente(self, ambiente: str) -> dict:
        """Destrutivo: banco, DSN, pastas e cadastro. Sem volta."""
        return self._post("/remover", {"ambiente": ambiente})

    def clonar(self, origem: str, novo_ambiente: str, novo_banco: str,
               port: str, odbc_user: str, odbc_pass: str,
               subpasta: str = "", reaproveitar_temp: bool = False) -> dict:
        """`subpasta` e `reaproveitar_temp` decidem onde o clone nasce e se o
        temp do pai é copiado. Gerenciador antigo ignora os dois campos e volta
        ao comportamento de antes — o canal não quebra por causa deles."""
        return self._post("/clonar", {
            "origem": origem, "novo_ambiente": novo_ambiente,
            "novo_banco": novo_banco, "port": str(port),
            "odbc_user": odbc_user, "odbc_pass": odbc_pass,
            "subpasta": subpasta, "reaproveitar_temp": bool(reaproveitar_temp)})

    def ambientes(self) -> dict:
        return self._get("/ambientes")

    def detalhes(self, indice: int) -> dict:
        return self._get(f"/ambientes/{int(indice)}")


class EstadoGerenciador:
    """Cache do estado do Gerenciador, alimentado por uma thread de polling."""

    def __init__(self, client: GerenciadorClient | None = None,
                 intervalo: float = INTERVALO_POLL_SEG,
                 tolerancia_seg: float = TOLERANCIA_SEG):
        self._client = client or GerenciadorClient()
        self._intervalo = intervalo
        self._tolerancia = tolerancia_seg
        self._lock = threading.Lock()
        self._parar = threading.Event()
        self._thread: threading.Thread | None = None
        # Momento da primeira falha da sequência atual. Enquanto estiver
        # dentro da tolerância, o link continua valendo como online.
        self._falha_desde: float | None = None
        self._instantaneo = self._offline("Verificando o Gerenciador de Ambientes…")

    # ── ciclo de vida ──
    def iniciar(self) -> "EstadoGerenciador":
        self.atualizar()  # primeira leitura síncrona: a UI já abre com a verdade
        self._thread = threading.Thread(target=self._loop, name="nebula-poll",
                                        daemon=True)
        self._thread.start()
        return self

    def parar(self) -> None:
        self._parar.set()

    def _loop(self) -> None:
        while not self._parar.wait(self._intervalo):
            self.atualizar()

    # ── leitura ──
    @staticmethod
    def _offline(motivo: str) -> dict:
        return {
            "online": False,
            "motivo": motivo,
            "instavel": False,
            "versao": "",
            "vpn": None,
            "config_valida": None,
            "config_motivo": "",
            "ocupado": False,
            "operacao": "",
            "bancos": [],
            "ambientes": {},
            "conexoes": [],
            "conexao_ativa": "",
            "localizacoes": {},
            "pasta_destino": "",
            "base_path": "",
            "andamento": {},
        }

    def atualizar(self) -> dict:
        try:
            payload = self._client.ambientes()
            saude = self._client.health()
            self._falha_desde = None
            novo = {
                "online": True,
                "motivo": "",
                "instavel": False,
                "versao": saude.get("versao", ""),
                "vpn": payload.get("vpn"),
                "config_valida": payload.get("config_valida"),
                "config_motivo": payload.get("config_motivo", ""),
                "ocupado": bool(payload.get("ocupado")),
                "operacao": payload.get("operacao", ""),
                "andamento": saude.get("andamento") or {},
                "bancos": payload.get("bancos", []),
                "ambientes": payload.get("ambientes", {}),
                "conexoes": payload.get("conexoes", []),
                "conexao_ativa": payload.get("conexao_ativa", ""),
                "localizacoes": payload.get("localizacoes", {}),
                "pasta_destino": payload.get("pasta_destino", ""),
                "base_path": payload.get("base_path", ""),
            }
        except GerenciadorOffline as e:
            novo = self._na_falha(str(e))

        with self._lock:
            caiu = self._instantaneo["online"] and not novo["online"]
            subiu = not self._instantaneo["online"] and novo["online"]
            self._instantaneo = novo
        if caiu:
            log.warning("[LINK] Gerenciador de Ambientes ficou offline — "
                        "ações bloqueadas.")
        elif subiu:
            log.info("[LINK] Gerenciador de Ambientes online (v%s).",
                     novo["versao"] or "?")
        return novo

    def _na_falha(self, motivo: str) -> dict:
        """Falha de comunicação: mantém o link até estourar a tolerância.

        Operação longa no Gerenciador (restaurar banco faz attach de arquivos
        de vários GB) deixa o canal sem resposta por dezenas de segundos.
        Derrubar o link nesse intervalo interrompia a corrida por causa de uma
        indisponibilidade passageira.

        O último estado conhecido é preservado — inclusive `ambientes` e
        `bancos`, para a UI não piscar tudo em branco.
        """
        agora = time.monotonic()
        if self._falha_desde is None:
            self._falha_desde = agora

        parado_ha = agora - self._falha_desde
        anterior = self._instantaneo
        if anterior.get("online") and parado_ha < self._tolerancia:
            log.info("[LINK] Sem resposta há %.0fs (tolerância %.0fs): "
                     "mantendo o link. %s", parado_ha, self._tolerancia, motivo)
            return {**anterior, "instavel": True,
                    "motivo": f"Sem resposta há {int(parado_ha)}s — "
                              f"o Gerenciador pode estar em operação longa."}

        if anterior.get("online"):
            log.warning("[LINK] Sem resposta há %.0fs, acima da tolerância.",
                        parado_ha)
        return self._offline(motivo)

    @property
    def instantaneo(self) -> dict:
        with self._lock:
            return dict(self._instantaneo)

    @property
    def online(self) -> bool:
        return self.instantaneo["online"]

    def banco_por_nome(self, nome: str) -> dict | None:
        """Ambiente do Gerenciador, procurado por nome — não por índice.

        O índice muda quando o usuário remove um ambiente lá; o nome é o que a
        importação guarda.
        """
        for banco in self.instantaneo["bancos"]:
            if (banco.get("ambiente") or banco.get("nome_banco")) == nome:
                return banco
        return None

    def indice_por_nome(self, nome: str) -> int | None:
        for i, banco in enumerate(self.instantaneo["bancos"]):
            if (banco.get("ambiente") or banco.get("nome_banco")) == nome:
                return i
        return None

    def pais_do_ambiente(self, nome: str) -> str:
        """Nome do país por extenso, para achar a pasta de testes.

        `localizacao` do ambiente é a sigla (`par`); a tabela do Gerenciador
        traduz (`Paraguai`). Sem tradução, devolve a própria sigla, que ao
        menos aparece na mensagem de erro.
        """
        banco = self.banco_por_nome(nome) or {}
        sigla = (banco.get("localizacao") or "").strip()
        if not sigla:
            return ""
        return self.instantaneo["localizacoes"].get(sigla, sigla)

    def restaurar_banco(self, nome: str) -> dict:
        try:
            return self._client.restaurar_banco(nome)
        except GerenciadorOffline as e:
            return {"ok": False, "erro": str(e)}

    def restaurar_rpo(self, nome: str) -> dict:
        try:
            return self._client.restaurar_rpo(nome)
        except GerenciadorOffline as e:
            return {"ok": False, "erro": str(e)}

    def parar_ambiente(self, nome: str) -> dict:
        try:
            return self._client.parar_ambiente(nome)
        except GerenciadorOffline as e:
            return {"ok": False, "erro": str(e)}

    def clonar(self, **kwargs) -> dict:
        try:
            return self._client.clonar(**kwargs)
        except GerenciadorOffline as e:
            return {"ok": False, "erro": str(e)}

    def remover_ambiente(self, nome: str) -> dict:
        try:
            return self._client.remover_ambiente(nome)
        except GerenciadorOffline as e:
            return {"ok": False, "erro": str(e)}

    @property
    def pasta_destino(self) -> str:
        """Raiz de downloads/temp do Gerenciador — onde vive o RPO zerado."""
        return self.instantaneo.get("pasta_destino", "")

    def esperar_ocioso(self, limite_seg: float = 3600,
                       parar=None) -> dict:
        """Bloqueia até o Gerenciador terminar a operação em curso.

        O `executar` do Gerenciador roda em thread e devolve na hora; quem
        precisa do fim é quem chamou. `ocupado` no status é o sinal.
        """
        fim = time.monotonic() + limite_seg
        # Dá tempo de a flag subir antes de concluir que já acabou.
        time.sleep(1.5)
        while time.monotonic() < fim:
            if parar is not None and parar():
                return {"ok": False, "erro": "Interrompido."}
            estado = self.atualizar()
            if not estado["online"]:
                return {"ok": False, "erro": estado["motivo"]}
            # `instavel` é justamente o caso desta espera: o Gerenciador parou
            # de responder porque está no meio da operação que aguardamos.
            if not estado["ocupado"] and not estado.get("instavel"):
                return {"ok": True}
            time.sleep(2)
        return {"ok": False, "erro": "Tempo esgotado esperando o Gerenciador."}

    def detalhes_por_nome(self, nome: str) -> dict:
        indice = self.indice_por_nome(nome)
        if indice is None:
            return {"ok": False, "erro": "Ambiente não existe mais no Gerenciador."}
        try:
            return self._client.detalhes(indice)
        except GerenciadorOffline as e:
            return {"ok": False, "erro": str(e)}


def esperar_online(estado: EstadoGerenciador, segundos: float = 5.0) -> bool:
    """Auxiliar de teste/diagnóstico: aguarda o canal subir."""
    limite = time.monotonic() + segundos
    while time.monotonic() < limite:
        if estado.atualizar()["online"]:
            return True
        time.sleep(0.2)
    return False
