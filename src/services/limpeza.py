"""Remoção de uma instância que o Gerenciador não alcança mais.

Quando a instância ainda está cadastrada, quem remove é o Gerenciador — ele já
faz isso com rollback e é a única verdade sobre ambiente. Este módulo é para o
resto: instância cujo ambiente-pai saiu da lista, ou pasta que ficou no disco
sem cadastro nenhum. Sem ele, um ambiente Protheus inteiro (2,5 GB cada aqui)
fica de pé para sempre.

Ordem, e o porquê de cada passo:

1. **matar os processos** — AppServer e DbAccess seguram arquivos abertos
   dentro da pasta; apagar antes deixa metade da árvore para trás;
2. **derrubar o banco** — enquanto ele está anexado, o SQL Server mantém o
   MDF/LDF bloqueados. `DROP DATABASE` (e não `detach`) porque aqui a intenção
   é sumir com tudo: o drop também apaga os arquivos físicos;
3. **excluir o DSN** — System DSN em `HKLM`, criado pelo clone com o nome do
   banco. Exige administrador, e é por isso que o executável voltou a nascer
   elevado;
4. **apagar as pastas** — a do ambiente e o workspace do banco.

Cada passo é reportado por si. Falhar em um não cancela os outros: parar no
meio deixaria o pior dos mundos, com metade removida e nenhum registro do que
sobrou.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)

_SEM_JANELA = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# Credenciais de toda base, como definido pelo usuário — as mesmas que o clone
# usa para criar o DSN (`services/paralelos.py`).
SQL_USER = "sa"
SQL_PASS = "123456"

CHAVE_ODBC = r"SOFTWARE\ODBC\ODBC.INI"
CHAVE_FONTES = r"SOFTWARE\ODBC\ODBC.INI\ODBC Data Sources"


# ─────────────────────────────────────────────────────────────
# PROCESSOS
# ─────────────────────────────────────────────────────────────

def matar_processos_da_pasta(pasta: str | Path) -> dict:
    """Encerra o que estiver rodando de dentro daquela pasta.

    Por caminho do executável, não por nome de imagem: `taskkill /IM
    appserver.exe` derrubaria o ambiente que o usuário abriu à mão. Cobre a
    instância cujo PID ninguém anotou — a que ficou no disco sem registro.
    """
    alvo = str(pasta or "").rstrip("\\/")
    if not alvo or os.name != "nt":
        return {"ok": True, "mortos": 0}

    # `Win32_Process` é o que sabe o caminho do executável; `taskkill` não.
    script = (
        f"$alvo = '{alvo}\\*'; "
        "$p = Get-CimInstance Win32_Process | "
        "Where-Object { $_.ExecutablePath -like $alvo }; "
        "if ($p) { $p | ForEach-Object { Stop-Process -Id $_.ProcessId -Force "
        "-ErrorAction SilentlyContinue }; $p.Count } else { 0 }"
    )
    try:
        saida = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=60, creationflags=_SEM_JANELA)
        mortos = int((saida.stdout or "0").strip() or 0)
    except (OSError, ValueError, subprocess.SubprocessError) as e:
        return {"ok": False, "erro": f"Não deu para encerrar os processos: {e}"}
    if mortos:
        log.info("[LIMPEZA] %d processo(s) encerrado(s) em %s.", mortos, alvo)
    return {"ok": True, "mortos": mortos}


# ─────────────────────────────────────────────────────────────
# BANCO
# ─────────────────────────────────────────────────────────────

def conectar(servidor: str, driver: str, usuario: str = SQL_USER,
             senha: str = SQL_PASS):
    """Conexão de administração no SQL Server.

    `autocommit`: `ALTER DATABASE` e `DROP DATABASE` não rodam dentro de
    transação. O import fica aqui dentro para o módulo continuar carregável
    numa máquina sem o driver — quem não vai limpar não precisa dele.
    """
    import pyodbc

    return pyodbc.connect(
        f"DRIVER={{{driver}}};SERVER={servidor};UID={usuario};PWD={senha}",
        autocommit=True, timeout=30)


def derrubar_banco(banco: str, servidor: str, driver: str) -> dict:
    """`DROP DATABASE`, que também apaga o MDF/LDF de onde estiverem.

    Banco inexistente não é erro: a limpeza roda justamente sobre restos, e
    metade deles já não tem base anexada.
    """
    if not banco:
        return {"ok": True, "pulado": "sem banco no registro"}
    if not servidor or not driver:
        return {"ok": False,
                "erro": "Sem servidor/driver do Gerenciador para falar com o SQL."}
    try:
        conexao = conectar(servidor, driver)
    except Exception as e:                      # pyodbc.Error e ImportError
        return {"ok": False, "erro": f"Não conectei no SQL Server: {e}"}

    try:
        cursor = conexao.cursor()
        existe = cursor.execute("SELECT DB_ID(?)", banco).fetchone()
        if not existe or existe[0] is None:
            return {"ok": True, "pulado": "banco não existe no servidor"}
        # SINGLE_USER derruba as sessões presas — sem isso o drop trava
        # esperando a conexão que o AppServer deixou aberta.
        cursor.execute(
            f"ALTER DATABASE [{banco}] SET SINGLE_USER WITH ROLLBACK IMMEDIATE")
        cursor.execute(f"DROP DATABASE [{banco}]")
        log.info("[LIMPEZA] Banco %s derrubado.", banco)
        return {"ok": True, "derrubado": banco}
    except Exception as e:
        return {"ok": False, "erro": f"Falha ao derrubar {banco}: {e}"}
    finally:
        try:
            conexao.close()
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────
# ODBC
# ─────────────────────────────────────────────────────────────

def excluir_dsn(nome: str) -> dict:
    """Tira o System DSN do registro do Windows.

    O clone cria o DSN com o nome do banco. Some com a chave e com a entrada em
    `ODBC Data Sources`, que é o índice — deixar só o índice cria fonte de
    dados quebrada na lista do Windows.
    """
    if not nome:
        return {"ok": True, "pulado": "sem DSN"}
    if os.name != "nt":
        return {"ok": True, "pulado": "fora do Windows"}
    import winreg

    removeu = False
    try:
        winreg.DeleteKey(winreg.HKEY_LOCAL_MACHINE, f"{CHAVE_ODBC}\\{nome}")
        removeu = True
    except FileNotFoundError:
        pass
    except PermissionError:
        return {"ok": False, "erro": f"Sem permissão para remover o DSN {nome}."}
    except OSError as e:
        return {"ok": False, "erro": f"Falha ao remover o DSN {nome}: {e}"}

    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, CHAVE_FONTES, 0,
                            winreg.KEY_SET_VALUE) as chave:
            winreg.DeleteValue(chave, nome)
            removeu = True
    except FileNotFoundError:
        pass
    except OSError as e:
        return {"ok": False, "erro": f"DSN {nome} continua na lista do ODBC: {e}"}

    if removeu:
        log.info("[LIMPEZA] DSN %s removido.", nome)
    return {"ok": True, "removido": removeu}


# ─────────────────────────────────────────────────────────────
# PASTAS
# ─────────────────────────────────────────────────────────────

def apagar_pasta(caminho: str | Path) -> dict:
    """Apaga a árvore inteira. Só de dentro de um caminho plausível.

    A trava de profundidade existe porque este módulo recebe caminho vindo de
    arquivo de registro: um valor errado ali não pode virar `rmtree` na raiz de
    um disco.
    """
    # Antes de virar `Path`: `Path("")` é `.`, o diretório atual — campo em
    # branco no registro não pode apontar para a pasta do programa.
    if not str(caminho or "").strip():
        return {"ok": True, "pulado": "caminho vazio"}
    alvo = Path(caminho)
    if not alvo.exists():
        return {"ok": True, "pulado": "não existe"}
    if len(alvo.parts) < 3:
        return {"ok": False,
                "erro": f"Caminho raso demais para apagar: {alvo}"}

    try:
        shutil.rmtree(alvo)
    except OSError as e:
        return {"ok": False,
                "erro": f"Não apagou tudo em {alvo}: {e} "
                        "(arquivo em uso ou sem permissão?)"}
    log.info("[LIMPEZA] Pasta apagada: %s", alvo)
    return {"ok": True, "apagado": str(alvo)}


# ─────────────────────────────────────────────────────────────
# ORQUESTRAÇÃO
# ─────────────────────────────────────────────────────────────

def remover(item: dict, *, servidor: str = "", driver: str = "") -> dict:
    """Remove uma instância inteira e conta o que aconteceu em cada passo.

    `item` é uma linha do inventário. Nada aqui decide **se** pode remover —
    isso é do inventário e da confirmação na tela; aqui só se executa.
    """
    ambiente = item.get("ambiente", "?")
    passos, erros = [], []

    def _passo(nome: str, resultado: dict) -> None:
        passos.append({"passo": nome, **resultado})
        if not resultado.get("ok"):
            erros.append(f"{nome}: {resultado.get('erro', 'falhou')}")

    for pasta in (item.get("pasta"), item.get("workspace_banco")):
        if pasta:
            _passo("processos", matar_processos_da_pasta(pasta))

    _passo("banco", derrubar_banco(item.get("banco", ""), servidor, driver))
    _passo("dsn", excluir_dsn(item.get("banco", "")))

    for caminho in item.get("caminhos") or []:
        _passo(f"pasta {caminho}", apagar_pasta(caminho))

    if erros:
        log.warning("[LIMPEZA] %s saiu com pendência: %s", ambiente, "; ".join(erros))
    else:
        log.info("[LIMPEZA] %s removido por completo.", ambiente)
    return {"ok": not erros, "ambiente": ambiente, "passos": passos,
            "erros": erros}
