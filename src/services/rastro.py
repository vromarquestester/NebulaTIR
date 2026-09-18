"""Rastro detalhado para o `debug-*.log` — o que o programa fez, passo a passo.

O Log de Execução mostrado ao usuário é enxuto de propósito (INFO, filtrado
por origem) e o `nebula-*.log` guarda o DEBUG dos módulos da aplicação. Este
módulo alimenta um terceiro nível, o `debug-*.log`, que existe para quando
alguém precisa entender **depois do fato** o que aconteceu: cada ação que a
interface disparou (clique, aba, modal, preferência), cada chamada da API
com argumentos, retorno e duração, cada subprocesso aberto com comando, PID e
código de saída, cada etapa medida.

Pedido do usuário em 2026-09-18, junto com o botão de diagnóstico: o
NebulaTIR tem a execução mais complexa da família, e o relato de tela não
bastava para reconstruir o que o usuário fez e o que o sistema respondeu.

Cópia adaptada do `core/rastro.py` do Gerenciador de Ambientes. O logger
chama-se `rastro` de propósito: o `FiltroAplicacao` do `log_bridge` só deixa
passar `services.*`/`webui.*`, então nada daqui vai para a tela nem para o
`nebula-*.log` — só para o arquivo de debug, que aceita tudo.
"""

from __future__ import annotations

import functools
import logging
import subprocess
import threading
import time
from contextlib import contextmanager

log = logging.getLogger("rastro")

# Chaves cujo valor nunca vai para o log, mesmo em debug.
SEGREDOS = ("password", "senha", "pass", "pwd", "token", "secret")

# Tamanho máximo de um valor repr'd numa linha: listas de rotinas e configs
# inteiras cabem, saída de subprocesso não.
MAX_VALOR = 600


def mascarar(dados):
    """Copia a estrutura trocando valores sensíveis por `***`."""
    if isinstance(dados, dict):
        return {
            k: ("***" if any(s in str(k).lower() for s in SEGREDOS) else mascarar(v))
            for k, v in dados.items()
        }
    if isinstance(dados, (list, tuple)):
        return type(dados)(mascarar(v) for v in dados)
    return dados


def _curto(valor) -> str:
    texto = repr(mascarar(valor))
    if len(texto) > MAX_VALOR:
        texto = texto[:MAX_VALOR] + f"… (+{len(texto) - MAX_VALOR})"
    return texto


def _formatar(dados: dict) -> str:
    return " ".join(f"{k}={_curto(v)}" for k, v in dados.items())


def _thread() -> str:
    nome = threading.current_thread().name
    return "" if nome == "MainThread" else f"[{nome}] "


# ── Ações e etapas ──────────────────────────────────────────

def acao(_nome: str, **dados) -> None:
    """Registra uma ação disparada pela interface ou pela API.

    O primeiro parâmetro leva sublinhado porque `nome=` é argumento comum
    dos métodos da API (`executar_tir(nome)`), e chegaria em `dados`.
    """
    log.debug(f"{_thread()}[AÇÃO] {_nome} {_formatar(dados)}".rstrip())


def ui(evento: str, dados: dict | None = None) -> None:
    """Evento vindo do JavaScript: clique, aba, modal, painel de log, campo."""
    log.debug(f"[UI] {evento} {_formatar(dados or {})}".rstrip())


def resultado(nome: str, retorno, duracao_ms: float | None = None) -> None:
    """Desfecho de uma ação, resumindo o retorno."""
    resumo = retorno
    if isinstance(retorno, dict):
        resumo = {k: v for k, v in retorno.items()
                  if k in ("ok", "erro", "criado", "via", "estado", "versao")}
        if not resumo:
            resumo = f"<dict {len(retorno)} chaves: {', '.join(list(retorno)[:8])}>"
    elif isinstance(retorno, (list, tuple)):
        resumo = f"<{len(retorno)} itens>"
    tempo = f" ({duracao_ms:.0f} ms)" if duracao_ms is not None else ""
    log.debug(f"{_thread()}[AÇÃO] {nome} → {_curto(resumo)}{tempo}")


@contextmanager
def etapa(_nome: str, **dados):
    """Mede e registra início/fim de um trecho, inclusive quando ele falha."""
    nome = _nome
    log.debug(f"{_thread()}[ETAPA] ▶ {nome} {_formatar(dados)}".rstrip())
    inicio = time.perf_counter()
    try:
        yield
    except Exception as e:
        ms = (time.perf_counter() - inicio) * 1000
        log.debug(f"{_thread()}[ETAPA] ✗ {nome} falhou após {ms:.0f} ms: "
                  f"{type(e).__name__}: {e}")
        raise
    else:
        ms = (time.perf_counter() - inicio) * 1000
        log.debug(f"{_thread()}[ETAPA] ■ {nome} ({ms:.0f} ms)")


def nota(_texto: str, **dados) -> None:
    """Linha avulsa: estado interno, decisão tomada, valor lido."""
    log.debug(f"{_thread()}[NOTA] {_texto} {_formatar(dados)}".rstrip())


# ── Métodos da API ──────────────────────────────────────────

# Consultados em loop pela tela (a cada 1–2 s). Registrá-los encheria o
# arquivo com dezenas de milhares de linhas iguais por dia e afogaria o que
# interessa; o que eles devolvem já aparece nas ações que mudam estado.
METODOS_SEM_RASTRO = frozenset({
    "poll_logs", "get_status", "estado_execucao", "estado_exclusao",
    "estado_limpeza", "atualizacao_estado", "reinicio_pedido",
    "pode_executar", "estado_rpo",
    # Registra a si mesmo, com o nome do evento da tela.
    "rastro_ui",
})


def rastreado(fn):
    """Decora um método da API: argumentos na entrada, resumo e tempo na saída."""
    nome = fn.__name__

    @functools.wraps(fn)
    def _envolto(self, *args, **kwargs):
        dados = {}
        if args:
            dados["args"] = args
        if kwargs:
            dados.update(kwargs)
        acao(nome, **dados)
        inicio = time.perf_counter()
        try:
            retorno = fn(self, *args, **kwargs)
        except Exception as e:
            ms = (time.perf_counter() - inicio) * 1000
            log.debug(f"{_thread()}[AÇÃO] {nome} ✗ {type(e).__name__}: {e} ({ms:.0f} ms)")
            raise
        resultado(nome, retorno, (time.perf_counter() - inicio) * 1000)
        return retorno
    return _envolto


def rastrear_classe(cls):
    """Aplica `rastreado` a todo método público da classe, menos os de polling."""
    for nome, membro in list(vars(cls).items()):
        if nome.startswith("_") or nome in METODOS_SEM_RASTRO:
            continue
        if callable(membro) and not isinstance(membro, (staticmethod, classmethod, property)):
            setattr(cls, nome, rastreado(membro))
    return cls


# ── Subprocessos ────────────────────────────────────────────
#
# Um gancho só, no `subprocess.Popen`, em vez de uma linha em cada um dos ~15
# pontos que abrem processo: AppServer, DbAccess, netstat, taskkill, pip, uv,
# drivers, o próprio lançador do TIR. Registra comando, cwd e PID na abertura
# e código de saída com duração no `wait` (o `run`/`communicate` passam por
# ele). Instalado uma vez, só em produção — os testes não passam por aqui.

_gancho_instalado = False


def instalar_gancho_subprocesso() -> None:
    global _gancho_instalado
    if _gancho_instalado:
        return
    _gancho_instalado = True

    init_original = subprocess.Popen.__init__
    wait_original = subprocess.Popen.wait

    @functools.wraps(init_original)
    def _init(self, args, *a, **kw):
        cwd = kw.get("cwd")
        try:
            init_original(self, args, *a, **kw)
        except Exception as e:
            log.debug(f"{_thread()}[PROC] ✗ não abriu {_curto(args)}"
                      f"{' cwd=' + str(cwd) if cwd else ''}: {type(e).__name__}: {e}")
            raise
        self._rastro_inicio = time.perf_counter()
        log.debug(f"{_thread()}[PROC] ▶ PID {self.pid} {_curto(args)}"
                  f"{' cwd=' + str(cwd) if cwd else ''}")

    @functools.wraps(wait_original)
    def _wait(self, *a, **kw):
        codigo = wait_original(self, *a, **kw)
        if not getattr(self, "_rastro_fim", False):
            self._rastro_fim = True
            inicio = getattr(self, "_rastro_inicio", None)
            ms = f" ({(time.perf_counter() - inicio) * 1000:.0f} ms)" if inicio else ""
            log.debug(f"{_thread()}[PROC] ■ PID {self.pid} saiu com {codigo}{ms}")
        return codigo

    subprocess.Popen.__init__ = _init
    subprocess.Popen.wait = _wait
