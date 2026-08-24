"""Ponte de logging entre os services e a interface web.

Cópia enxuta do `webui/log_bridge.py` do Gerenciador de Ambientes: o handler
despeja eventos numa `queue.Queue` que o JavaScript drena por polling
(`api.poll_logs`). Sem os eventos de download, que aqui não existem.

Formato dos eventos (dicts JSON-serializáveis):
    {"kind": "log", "level": "INFO", "text": "..."}
"""

import logging
import queue

# Só o que a aplicação emite chega ao Log. Sem o filtro, o logger do pywebview
# despeja centenas de linhas de introspecção COM do WebView2 na tela.
LOGGERS_DA_APLICACAO = ("services", "webui", "__main__", "root")

MAX_CARACTERES = 2000


class FiltroAplicacao(logging.Filter):
    """Aceita apenas registros originados nos pacotes da aplicação."""

    def __init__(self, prefixos: tuple[str, ...] = LOGGERS_DA_APLICACAO):
        super().__init__()
        self.prefixos = prefixos

    def filter(self, record: logging.LogRecord) -> bool:
        nome = record.name or "root"
        return any(nome == p or nome.startswith(p + ".") for p in self.prefixos)


class QueueLogHandler(logging.Handler):
    """Handler que publica cada LogRecord formatado na fila da UI."""

    def __init__(self, fila: queue.Queue):
        super().__init__()
        self.fila = fila

    def emit(self, record: logging.LogRecord) -> None:
        try:
            texto = self.format(record)
            if len(texto) > MAX_CARACTERES:
                texto = texto[:MAX_CARACTERES] + " […]"
            self.fila.put({"kind": "log", "level": record.levelname, "text": texto})
        except Exception:  # nunca deixa o logging derrubar a aplicação
            pass


def instalar_handler(fila: queue.Queue) -> QueueLogHandler:
    """Instala o handler no root logger (INFO+ da aplicação vai para a tela)."""
    handler = QueueLogHandler(fila)
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s",
                                           datefmt="%H:%M:%S"))
    handler.setLevel(logging.INFO)
    handler.addFilter(FiltroAplicacao())
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)

    for ruidoso in ("webview", "bottle", "comtypes", "urllib3", "PIL"):
        logging.getLogger(ruidoso).setLevel(logging.WARNING)
    logging.getLogger("webview").propagate = False

    instalar_arquivo()
    return handler


# ── Log em arquivo ──────────────────────────────────────────
# O painel só guarda o que está na tela: fechado o programa, some tudo. Quando
# uma instância paralela não subiu, o motivo foi para o painel e evaporou —
# ficou impossível dizer, horas depois, por que a corrida rodou com uma
# instância a menos. Um arquivo por dia resolve, e é onde olhar primeiro.

NOME_PASTA_LOG = "logs"
DIAS_GUARDADOS = 14


def pasta_de_log():
    from services.recursos import pasta_do_programa
    return pasta_do_programa() / NOME_PASTA_LOG


def instalar_arquivo() -> logging.Handler | None:
    """Handler de arquivo, um por dia, em `DEBUG` — mais fundo que a tela.

    A tela mostra `INFO`; aqui entra o `DEBUG` junto, que é onde moram os
    detalhes de porta, PID e caminho de `.ini` que resolvem diagnóstico.
    """
    import datetime

    try:
        pasta = pasta_de_log()
        pasta.mkdir(parents=True, exist_ok=True)
        hoje = datetime.date.today().strftime("%Y%m%d")
        handler = logging.FileHandler(pasta / f"nebula-{hoje}.log",
                                      encoding="utf-8")
    except OSError:
        # Sem permissão de escrita, não se derruba o programa por causa do log.
        return None

    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    handler.setLevel(logging.DEBUG)
    handler.addFilter(FiltroAplicacao())
    logging.getLogger().addHandler(handler)
    _limpar_antigos()
    return handler


def _limpar_antigos() -> int:
    """Apaga log com mais de `DIAS_GUARDADOS` dias: a pasta não pode só crescer."""
    import time

    limite = time.time() - DIAS_GUARDADOS * 86400
    apagados = 0
    try:
        for arquivo in pasta_de_log().glob("nebula-*.log"):
            if arquivo.stat().st_mtime < limite:
                arquivo.unlink()
                apagados += 1
    except OSError:
        pass
    return apagados
