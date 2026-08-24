"""Bridge falso: um Gerenciador de Ambientes de mentira, servido de verdade.

Os testes sobem um `http.server` com o mesmo contrato de
`projeto_atualiza_banco/src/webui/bridge_server.py` (token no header, rotas
`/health` e `/ambientes`) e apontam o cliente para ele com um `bridge.json`
temporário. Nada aqui importa código do outro projeto — se o contrato mudar lá
sem mudar aqui, o teste passa e a integração quebra; por isso a rota e o nome
do header ficam explícitos, para saltarem à vista numa revisão.
"""

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

TOKEN = "token-de-teste"
NOME_APP = "gerenciador-ambientes"

PAYLOAD_PADRAO = {
    "ok": True,
    "bancos": [
        # `ambiente_ini` é a seção do appserver.ini — nem sempre igual ao nome
        # da lista: o Gerenciador tem a opção `nome_ambiente_ini`, e o padrão
        # é a seção literal `[environment]` do template.
        {"ambiente": "PAR_2510", "nome_banco": "P1212510MNTDBPAREXP",
         "port": "4321", "localizacao": "par", "versao": "2510",
         "connection": "Padrão", "appserver_exe": "C:/T/appserver.exe",
         "dbaccess_exe": "C:/T/dbaccess.exe", "ambiente_ini": "PAR_2510"},
        {"ambiente": "BRA_2410", "nome_banco": "P1212410MNTDBBRAEXP",
         "port": "4322", "localizacao": "bra", "versao": "2410",
         "connection": "Padrão", "appserver_exe": "", "dbaccess_exe": ""},
    ],
    "conexoes": [{"nome": "Padrão", "sql_server": "10.0.0.1",
                  "sql_user": "sa", "driver": "ODBC Driver 17"}],
    "conexao_ativa": "Padrão",
    # Tabela do Gerenciador: sigla → país por extenso, usada para achar a
    # pasta de testes.
    "localizacoes": {"par": "Paraguai", "bra": "Brasil", "mex": "México"},
    "ambientes": {
        "PAR_2510": {"estado": "running", "provisionado": True,
                     "tem_appserver": True, "tem_dbaccess": True, "tem_ini": True},
        "BRA_2410": {"estado": "stopped", "provisionado": False,
                     "tem_appserver": False, "tem_dbaccess": False, "tem_ini": False},
    },
    # Raízes do Gerenciador: onde ele instala ambiente (`base_path`) e onde
    # deixa o temp baixado (`pasta_destino`). O registro das instâncias mora
    # dentro da primeira.
    "base_path": r"C:\TOTVS",
    "pasta_destino": r"C:\TOTVS\Downloads",
    "vpn": True,
    "config_valida": True,
    "config_motivo": "",
    "ocupado": False,
    "operacao": "",
}


class BridgeFalso:
    """Servidor controlável: dá para derrubar, atrasar e trocar o payload."""

    def __init__(self):
        self.payload = json.loads(json.dumps(PAYLOAD_PADRAO))
        self.token = TOKEN
        self.atraso = 0.0
        self.chamadas = []
        servidor = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def do_GET(self):  # noqa: N802
                import time
                servidor.chamadas.append(self.path)
                if servidor.atraso:
                    time.sleep(servidor.atraso)
                if self.headers.get("X-Nebula-Token") != servidor.token:
                    return self._json(401, {"ok": False, "erro": "Token inválido."})
                if self.path == "/health":
                    return self._json(200, {
                        "ok": True, "app": NOME_APP, "versao": "1.2.3",
                        "pid": os.getpid(), "vpn": servidor.payload["vpn"],
                        "config_valida": servidor.payload["config_valida"],
                        "config_motivo": "", "ocupado": False, "operacao": "",
                    })
                if self.path == "/ambientes":
                    return self._json(200, servidor.payload)
                if self.path.startswith("/ambientes/"):
                    try:
                        i = int(self.path.rsplit("/", 1)[-1])
                        banco = servidor.payload["bancos"][i]
                    except (ValueError, IndexError):
                        return self._json(404, {"ok": False, "erro": "Inexistente."})
                    return self._json(200, {
                        "ok": True, "banco": banco,
                        "ambiente_ini": banco.get("ambiente_ini") or "environment",
                        "conexao": servidor.payload["conexoes"][0],
                        "pasta_trabalho": "C:/temp/" + banco["ambiente"],
                        "temp_download": "", "logs": "",
                        "url_ambiente": f"http://127.0.0.1:{banco['port']}/",
                    })
                return self._json(404, {"ok": False, "erro": "Rota desconhecida."})

            def _json(self, status, corpo):
                dados = json.dumps(corpo, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(dados)))
                self.end_headers()
                self.wfile.write(dados)

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._httpd.daemon_threads = True
        self.port = self._httpd.server_address[1]
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    def parar(self):
        self._httpd.shutdown()
        self._httpd.server_close()


@pytest.fixture
def bridge_falso():
    b = BridgeFalso()
    yield b
    b.parar()


@pytest.fixture
def registro(tmp_path, bridge_falso):
    """`bridge.json` apontando para o servidor falso, com o PID deste processo."""
    arquivo = tmp_path / "bridge.json"
    arquivo.write_text(json.dumps({
        "app": NOME_APP,
        "port": bridge_falso.port,
        "token": bridge_falso.token,
        "pid": os.getpid(),
        "versao": "1.2.3",
    }), encoding="utf-8")
    return arquivo
