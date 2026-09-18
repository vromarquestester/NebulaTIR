"""Pacote de diagnóstico para suporte — botão no painel de log.

Mesmo desenho do `services/diagnostico.py` do Gerenciador de Ambientes: o
problema chega como print de tela e relato, e o arquivo que explicaria tudo
(`debug-*.log`) quase nunca vem junto. Este módulo junta tudo num `.zip` que
o usuário anexa numa mensagem. Pedido em 2026-09-18, junto com o rastro.

O que entra:

    logs/             nebula-*.log e debug-*.log (os mais recentes de cada)
    config/           preferencias.json, ambientes_importados.json, instances.json
    ultima_corrida/   tests/<ambiente>/<rotina> da corrida mais recente:
                      config.json (senha mascarada), logs do TIR e PNGs
    tela.bmp          janela do NebulaTIR no momento, quando a captura funcionar
    ambiente.txt      versão, Python, quem provisiona o venv (uv/python),
                      tir_framework e drivers, Gerenciador (online/versão),
                      ambientes importados com estado, instâncias paralelas,
                      portas conhecidas e quem as escuta, espaço em disco

Nada é enviado para lugar nenhum: o arquivo fica ao lado do executável e o
usuário decide para quem manda. `bridge.json` fica de fora — leva o token da
sessão do Gerenciador.
"""

from __future__ import annotations

import json
import logging
import platform
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# Quantos arquivos de log de cada tipo entram (os mais recentes).
MAX_LOGS = 6
# Teto por arquivo da última corrida: log do TIR pode passar de 100 MB quando
# o DebugLog está ligado, e o zip inteiro tem que caber num anexo.
MAX_BYTES_ARQUIVO = 25 * 1024 * 1024
CHAVES_MASCARADAS = ("password", "senha", "token")


def _nome_do_pacote() -> str:
    return f"diagnostico_nebulatir_{datetime.now():%Y%m%d_%H%M%S}.zip"


def _recentes(pasta: Path, padrao: str, limite: int = MAX_LOGS) -> list[Path]:
    if not pasta.is_dir():
        return []
    arquivos = [p for p in pasta.glob(padrao) if p.is_file()]
    # A data está no nome: desempata arquivos gravados no mesmo instante.
    arquivos.sort(key=lambda p: (p.stat().st_mtime, p.name), reverse=True)
    return arquivos[:limite]


def _saida(comando: list, timeout: int = 20) -> str:
    try:
        # `tasklist` fala na página de código OEM do console (cp850 em
        # pt-BR), não em UTF-8 — lido errado, os acentos viram lixo.
        proc = subprocess.run(comando, capture_output=True, text=True,
                              timeout=timeout, creationflags=_NO_WINDOW,
                              encoding="oem" if sys.platform == "win32" else "utf-8",
                              errors="replace")
        return (proc.stdout or "").strip() or (proc.stderr or "").strip()
    except Exception as e:
        return f"(falhou: {e})"


def mascarar_json(texto: str) -> str:
    """`config.json` da corrida com as chaves sensíveis trocadas por `***`."""
    try:
        dados = json.loads(texto)
    except ValueError:
        return texto

    def _m(v):
        if isinstance(v, dict):
            return {k: ("***" if any(s in str(k).lower() for s in CHAVES_MASCARADAS)
                        else _m(x)) for k, x in v.items()}
        if isinstance(v, list):
            return [_m(x) for x in v]
        return v
    return json.dumps(_m(dados), indent=2, ensure_ascii=False)


def ultima_corrida(pasta_tests: Path) -> Path | None:
    """`tests/<ambiente>/<rotina>` tocada por último. None sem corrida."""
    if not pasta_tests.is_dir():
        return None
    candidatas = [p for amb in pasta_tests.iterdir() if amb.is_dir()
                  for p in amb.iterdir() if p.is_dir()]
    if not candidatas:
        return None
    return max(candidatas, key=lambda p: p.stat().st_mtime)


def _arquivos_da_corrida(pasta: Path) -> list[Path]:
    """config.json, logs e PNGs — não os fontes do teste nem o lançador."""
    achados = []
    config = pasta / "config.json"
    if config.is_file():
        achados.append(config)
    for sub in (pasta / "log", pasta):
        if not sub.is_dir():
            continue
        for p in sub.iterdir():
            if p.is_file() and p.suffix.lower() in (".log", ".txt", ".png", ".html", ".json") \
                    and p not in achados:
                achados.append(p)
    return achados


def coletar_ambiente(versao_app: str, pasta_programa: Path,
                     estado: dict | None = None) -> str:
    """Texto do `ambiente.txt`: a máquina e o programa, como estão agora."""
    from services import appservers, drivers, venv_tir

    linhas = [
        f"NebulaTIR {versao_app}",
        f"Gerado em: {datetime.now():%Y-%m-%d %H:%M:%S}",
        f"Pasta do programa: {pasta_programa}",
        f"Executável: {sys.executable}  (congelado={getattr(sys, 'frozen', False)})",
        f"Python do NebulaTIR: {platform.python_version()}",
        f"Máquina: {platform.node()}  {platform.platform()}",
        "",
        "== Venv do TIR ==",
        f"provisionador: {venv_tir.provisionador() or 'nenhum (sem uv e sem Python 3.12)'}",
        f"uv: {shutil.which('uv') or '-'}",
        f"venv: {venv_tir.python_do_venv()}  existe={venv_tir.existe()}",
        f"tir_framework: {venv_tir.versao_instalada() or '-'}",
        "",
        "== Navegadores ==",
    ]
    try:
        from services import navegadores
        for n in navegadores.detalhar():
            linhas.append(f"  {n['nome']}: {n.get('versao') or '?'}  {n.get('exe', '')}")
    except Exception as e:
        linhas.append(f"  (falhou: {e})")

    linhas += ["", "== Drivers (pasta drivers/ = reserva; o Firefox usa o do "
               "webdriver_manager, ver geckodriver.log da corrida) =="]
    try:
        for d in drivers.listar():
            linhas.append(f"  {d.get('nome', '')}: {d.get('versao') or '-'}")
    except Exception as e:
        linhas.append(f"  (falhou: {e})")

    # `estado` é o retorno de `Api.get_status()` — o mesmo que a tela vê.
    estado = estado or {}
    linhas += ["", "== Gerenciador de Ambientes ==",
               f"link: {estado.get('link', '?')}  versão: {estado.get('gerenciador_versao') or '?'}  "
               f"motivo: {estado.get('link_motivo') or '-'}",
               f"vpn: {estado.get('vpn', '?')}  conexão SQL ativa: {estado.get('conexao_ativa', '?')}  "
               f"ocupado: {estado.get('ocupado', '?')} ({estado.get('operacao') or '-'})",
               "", "== Ambientes importados =="]
    portas = set()
    for nome, info in (estado.get("ambientes") or {}).items():
        porta = str(info.get("port") or "")
        if porta:
            portas.add(porta)
        linhas.append(f"  {nome}: estado={info.get('estado')} porta={porta or '-'} "
                      f"versao={info.get('versao', '-')} "
                      f"compartilhada={info.get('porta_compartilhada', False)} "
                      f"fonte={info.get('fonte_estado', '-')} "
                      f"no_gerenciador={info.get('existe_no_gerenciador', '?')}")
    if not estado.get("ambientes"):
        linhas.append("  (nenhum, ou Gerenciador offline)")

    linhas += ["", "== Instâncias paralelas =="]
    for inst in estado.get("instancias") or []:
        linhas.append(f"  {inst}")
    if not estado.get("instancias"):
        linhas.append("  (nenhuma registrada)")

    linhas += ["", "== Portas =="]
    for porta in sorted(portas | {"7890"}, key=lambda p: int(p) if p.isdigit() else 0):
        try:
            dono = appservers.dono_da_porta(int(porta))
        except (TypeError, ValueError):
            dono = {}
        linhas.append(f"  {porta}: {'PID ' + str(dono['pid']) + ' ' + dono['exe'] if dono.get('pid') else 'livre'}")

    linhas += ["", "== Processos ==",
               _saida(["tasklist", "/FI", "IMAGENAME eq appserver.exe"]),
               _saida(["tasklist", "/FI", "IMAGENAME eq dbaccess64.exe"]),
               _saida(["tasklist", "/FI", "IMAGENAME eq geckodriver.exe"]),
               _saida(["tasklist", "/FI", "IMAGENAME eq chromedriver.exe"]),
               "", "== Espaço em disco =="]
    try:
        uso = shutil.disk_usage(pasta_programa)
        linhas.append(f"  {pasta_programa.drive or pasta_programa}: "
                      f"livre {uso.free / 2**30:.1f} GB de {uso.total / 2**30:.1f} GB")
    except OSError as e:
        linhas.append(f"  (falhou: {e})")
    return "\n".join(linhas) + "\n"


def gerar_pacote(destino_zip: Path, *, pasta_programa: Path, pasta_logs: Path,
                 arquivos_config: list[Path], texto_ambiente: str,
                 print_tela: Path | None = None) -> Path:
    """Monta o zip de diagnóstico. Devolve o caminho gerado."""
    destino_zip = Path(destino_zip)
    destino_zip.parent.mkdir(parents=True, exist_ok=True)
    pulados = []

    with zipfile.ZipFile(destino_zip, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("ambiente.txt", texto_ambiente)

        for padrao in ("nebula-*.log", "debug-*.log"):
            for arq in _recentes(pasta_logs, padrao):
                z.write(arq, f"logs/{arq.name}")

        for arq in arquivos_config:
            if arq and Path(arq).is_file():
                # A config do TIR importada leva `Password` em claro — sai
                # mascarada como o config.json da corrida (2026-09-18).
                z.writestr(f"config/{Path(arq).name}",
                           mascarar_json(Path(arq).read_text(encoding="utf-8",
                                                             errors="replace")))

        corrida = ultima_corrida(pasta_programa / "tests")
        if corrida is not None:
            raiz = f"ultima_corrida/{corrida.parent.name}/{corrida.name}"
            for arq in _arquivos_da_corrida(corrida):
                relativo = arq.relative_to(corrida).as_posix()
                if arq.name == "config.json":
                    z.writestr(f"{raiz}/config.json",
                               mascarar_json(arq.read_text(encoding="utf-8",
                                                           errors="replace")))
                elif arq.stat().st_size > MAX_BYTES_ARQUIVO:
                    pulados.append(f"{relativo} ({arq.stat().st_size / 2**20:.0f} MB)")
                else:
                    z.write(arq, f"{raiz}/{relativo}")

        if print_tela and Path(print_tela).is_file():
            z.write(print_tela, "tela.bmp")

        if pulados:
            z.writestr("ultima_corrida/PULADOS.txt",
                       "Acima do teto de %d MB, não entraram:\n" % (MAX_BYTES_ARQUIVO // 2**20)
                       + "\n".join(pulados) + "\n")

    tamanho = destino_zip.stat().st_size
    log.info(f"[DIAG] ✓ Pacote de diagnóstico: {destino_zip} ({tamanho / 1024:.0f} KB)")
    return destino_zip


def revelar_no_explorer(caminho: Path) -> None:
    """Abre o Explorer com o arquivo selecionado — o usuário só arrasta."""
    try:
        subprocess.Popen(["explorer", "/select,", str(Path(caminho))])
    except Exception as e:
        log.debug(f"[DIAG] Não foi possível abrir o Explorer: {e}")
