"""Entry point do NebulaTIR (pywebview + WebView2).

O NebulaTIR é a interface das execuções do TIR — hoje feitas só por linha de
comando. Ele é um braço do Gerenciador de Ambientes: não roda sozinho. Todo o
cadastro de ambientes, a clonagem, o `appserver.ini` e a checagem de VPN
continuam sendo responsabilidade do Gerenciador, que o NebulaTIR consulta pelo
canal loopback (`services/gerenciador_client.py`).

    uv run python main_web.py             # execução normal
    NEBULA_DEBUG=1 uv run python main_web.py   # abre o devtools do WebView2
"""

import os
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
SRC = RAIZ / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def _recurso(*partes: str) -> Path | None:
    """Resolve um arquivo empacotado (`sys._MEIPASS`) ou do repositório."""
    empacotado = getattr(sys, "_MEIPASS", None)
    if empacotado:
        candidato = Path(empacotado).joinpath(*partes)
        if candidato.exists():
            return candidato
    candidato = RAIZ.joinpath(*partes)
    return candidato if candidato.exists() else None


def caminho_interface() -> Path:
    """Local do `index.html`."""
    return _recurso("webui", "web", "index.html") or SRC / "webui" / "web" / "index.html"


def caminho_icone() -> str | None:
    icone = _recurso("resources", "app.ico")
    return str(icone) if icone else None


def main() -> None:
    import webview  # noqa: E402

    from _version import __version__  # noqa: E402
    from webui.api import Api  # noqa: E402

    api = Api()
    index = caminho_interface()

    janela = webview.create_window(
        f"NebulaTIR v{__version__}",
        str(index),
        js_api=api,
        width=1480,
        height=940,
        min_size=(1080, 700),
        maximized=True,
        background_color="#0A0812",
        text_select=True,
    )
    api._set_window(janela)

    icone = caminho_icone()
    try:
        webview.start(debug=os.environ.get("NEBULA_DEBUG") == "1",
                      **({"icon": icone} if icone else {}))
    finally:
        api._encerrar()


if __name__ == "__main__":
    main()
