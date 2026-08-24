"""Sobe e derruba os AppServer das instâncias paralelas.

Quem sobe é o NebulaTIR, não o Gerenciador — e por um motivo: só assim o PID
fica registrado aqui, e só com o PID dá para parar **uma** instância sem
derrubar as outras nem o ambiente que o usuário abriu à mão.

Elevação: o NebulaTIR roda como administrador (`uac_admin` no `.spec`), e
processo filho herda o token do pai. Por isso os AppServer nascem elevados sem
nenhum truque adicional.

O DbAccess **não** é subido aqui. Ele é um só para todos os bancos (o
`dbaccess.ini` indexa por alias) e o `subir_dbaccess` do Gerenciador mata o
que estiver rodando antes de subir o dele — dois processos brigariam pela
porta 7890.
"""

from __future__ import annotations

import logging
import socket
import subprocess
import time
from pathlib import Path

from services import appserver_ini, dbaccess_ini

log = logging.getLogger(__name__)

_SEM_JANELA = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# AppServer e DbAccess sobem **com janela**, um console por processo. Rodando
# calados eles viram fantasma: sobra DbAccess de pé com o ambiente marcado como
# parado, e a exclusão do ambiente falha sem que dê para ver quem está
# segurando o arquivo. Com a janela na tela, cada instância é visível e
# fechável à mão.
#
# São duas coisas, e as duas são necessárias:
#   - `CREATE_NEW_CONSOLE` dá um console próprio ao processo filho;
#   - `-console` faz o Protheus **escrever** nele. Sem o parâmetro o binário
#     sobe em modo silencioso e o console fica em branco.
_COM_CONSOLE = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
PARAM_CONSOLE = "-console"


def _params_com_console(params: str) -> list[str]:
    """Argumentos do processo, garantindo `-console` sem duplicar.

    A comparação ignora maiúsculas e aceita a forma com barra (`/console`),
    que é a que aparece em alguns ambientes antigos.
    """
    lista = [p for p in (params or "").split() if p]
    ja_tem = any(p.lower().lstrip("-/") == "console" for p in lista)
    return lista if ja_tem else lista + [PARAM_CONSOLE]

# Processo vivo é o primeiro sinal, não o último: o AppServer nasce em
# instantes mas leva dezenas de segundos para publicar o WebApp (carrega
# webapp.dll, RPO, dicionário). Quem manda é a porta responder.
ESPERA_SUBIDA_SEG = 3.0
ESPERA_PORTA_SEG = 180.0
# O DbAccess abre a porta em segundos — não carrega RPO nem dicionário.
ESPERA_DBACCESS_SEG = 45.0
INTERVALO_SONDA_SEG = 1.0


def porta_responde(porta: int, host: str = "127.0.0.1",
                   timeout: float = 1.0) -> bool:
    """Aceita conexão? É o que o navegador do TIR vai tentar."""
    if not porta:
        return False
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        return s.connect_ex((host, int(porta))) == 0


def esperar_porta(porta: int, limite_seg: float | None = None,
                  parar=None) -> dict:
    """Espera a porta aceitar conexão.

    Sem isso o teste começa antes de o ambiente estar no ar e morre em
    `connectionFailure` — foi exatamente o que aconteceu na primeira corrida
    real: AppServer às 19:53:49, teste às 19:54:00, porta pronta depois.

    O limite é resolvido AQUI, não no valor padrão do parâmetro: default de
    função é avaliado na definição, e a constante viraria imutável em tempo
    de execução.
    """
    limite_seg = ESPERA_PORTA_SEG if limite_seg is None else limite_seg
    fim = time.monotonic() + limite_seg
    while time.monotonic() < fim:
        if parar is not None and parar():
            return {"ok": False, "erro": "Interrompido."}
        if porta_responde(porta):
            return {"ok": True, "porta": int(porta)}
        time.sleep(INTERVALO_SONDA_SEG)
    return {"ok": False,
            "erro": f"A porta {porta} não respondeu em {int(limite_seg)}s. "
                    f"O AppServer subiu mas não publicou o WebApp."}


def subir(appserver_exe: str, params: str = "") -> dict:
    """Inicia um AppServer e devolve o PID.

    `cwd` é a pasta do executável: o AppServer procura o `appserver.ini` no
    diretório atual, e é esse arquivo que carrega a porta daquela instância.
    """
    exe = Path(appserver_exe or "")
    if not exe.is_file():
        return {"ok": False, "erro": f"AppServer não encontrado: {exe}"}

    cmd = [str(exe)] + _params_com_console(params)
    try:
        proc = subprocess.Popen(cmd, cwd=str(exe.parent),
                                creationflags=_COM_CONSOLE)
    except OSError as e:
        return {"ok": False, "erro": f"Falha ao iniciar o AppServer: {e}"}

    # Morrer no primeiro segundo é o sintoma de porta ocupada ou .ini inválido;
    # devolver "ok" nesse caso faria o teste falhar lá na frente, longe da causa.
    time.sleep(ESPERA_SUBIDA_SEG)
    if proc.poll() is not None:
        return {"ok": False,
                "erro": f"O AppServer encerrou logo após subir "
                        f"(código {proc.returncode}). Porta ocupada ou "
                        f"appserver.ini inválido?"}

    log.info("[APPSERVER] %s no ar (PID %d).", exe.parent.name, proc.pid)
    return {"ok": True, "pid": proc.pid, "exe": str(exe)}


NOME_DBACCESS = "dbaccess64.exe"


def dbaccess_no_ar() -> bool:
    try:
        saida = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {NOME_DBACCESS}",
                                "/NH"], capture_output=True, text=True, timeout=15,
                               creationflags=_SEM_JANELA).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return NOME_DBACCESS.casefold() in (saida or "").casefold()


def parar_dbaccess() -> bool:
    """Encerra o DbAccess em execução. Só para trocar o `.ini` dele."""
    try:
        subprocess.run(["taskkill", "/F", "/IM", NOME_DBACCESS],
                       capture_output=True, timeout=30,
                       creationflags=_SEM_JANELA)
    except (OSError, subprocess.SubprocessError):
        return False
    for _ in range(10):
        if not dbaccess_no_ar():
            return True
        time.sleep(1)
    return False


def garantir_dbaccess(dbaccess_exe: str, params: str = "",
                      reiniciar: bool = False) -> dict:
    """Sobe o DbAccess se não houver nenhum. **Nunca mata o que está no ar.**

    Um DbAccess atende todos os bancos (o `dbaccess.ini` indexa por alias), e
    `subir_dbaccess` do Gerenciador mata o existente antes de subir o dele —
    aqui isso derrubaria as instâncias já rodando.

    Necessário porque parar o ambiente principal antes da corrida leva o
    DbAccess dele junto, e sem ele o AppServer sobe mas não serve ninguém.
    """
    if dbaccess_no_ar():
        if not reiniciar:
            return {"ok": True, "subiu": False, "motivo": "já estava no ar"}
        # O DbAccess lê o `dbaccess.ini` na partida: alias novo só vale depois
        # de reiniciar. Feito ANTES de subir qualquer AppServer, para não
        # derrubar instância nenhuma.
        log.info("[DBACCESS] Reiniciando para carregar os aliases novos…")
        parar_dbaccess()

    exe = Path(dbaccess_exe or "")
    if not exe.is_file():
        return {"ok": False, "erro": f"DbAccess não encontrado: {exe}"}
    try:
        proc = subprocess.Popen([str(exe)] + _params_com_console(params),
                                cwd=str(exe.parent), creationflags=_COM_CONSOLE)
    except OSError as e:
        return {"ok": False, "erro": f"Falha ao iniciar o DbAccess: {e}"}

    time.sleep(ESPERA_SUBIDA_SEG)
    if proc.poll() is not None:
        return {"ok": False,
                "erro": f"O DbAccess encerrou logo após subir "
                        f"(código {proc.returncode})."}
    log.info("[DBACCESS] No ar (PID %d).", proc.pid)
    return {"ok": True, "subiu": True, "pid": proc.pid}


def subir_dbaccess_da_instancia(dbaccess_exe: str, porta: int,
                                params: str = "") -> dict:
    """Sobe **um** DbAccess só para esta instância, na porta dela.

    Contrário de `garantir_dbaccess`, que procura um processo já no ar e o
    reaproveita: aqui cada instância tem o seu, isolado. A porta vem escrita
    no `dbaccess.ini` do clone (que tem precedência), e `-pNNNN` vai junto
    para o caso de o arquivo não ter a chave.

    Isolar existe para testar uma hipótese: em corrida paralela as instâncias
    travavam sem causa visível em log nenhum, e o DbAccess compartilhado era o
    único ponto que todas dividiam.
    """
    exe = Path(dbaccess_exe or "")
    if not exe.is_file():
        return {"ok": False, "erro": f"DbAccess não encontrado: {exe}"}
    if not porta:
        return {"ok": False, "erro": "Instância sem porta de DbAccess."}

    if porta_responde(int(porta)):
        # Já há alguém nessa porta: ou é o DbAccess desta instância de uma
        # corrida anterior, ou outra coisa. Subir por cima só criaria dois
        # processos brigando.
        log.info("[DBACCESS] Porta %s já responde; reaproveitando.", porta)
        return {"ok": True, "subiu": False, "porta": int(porta),
                "motivo": f"já havia alguém na porta {porta}"}

    argumentos = _params_com_console(params)
    if not any(p.lower().startswith("-p") and p[2:].isdigit() for p in argumentos):
        argumentos.append(f"-p{int(porta)}")

    try:
        proc = subprocess.Popen([str(exe)] + argumentos, cwd=str(exe.parent),
                                creationflags=_COM_CONSOLE)
    except OSError as e:
        return {"ok": False, "erro": f"Falha ao iniciar o DbAccess: {e}"}

    time.sleep(ESPERA_SUBIDA_SEG)
    if proc.poll() is not None:
        return {"ok": False,
                "erro": f"O DbAccess da instância encerrou logo após subir "
                        f"(código {proc.returncode}). Porta {porta} ocupada?"}

    pronta = esperar_porta(int(porta), limite_seg=ESPERA_DBACCESS_SEG)
    if not pronta.get("ok"):
        return {"ok": False, "erro": f"DbAccess não abriu a porta {porta}."}

    log.info("[DBACCESS] Instância no ar na porta %s (PID %d).", porta, proc.pid)
    return {"ok": True, "subiu": True, "pid": proc.pid, "porta": int(porta)}


def subir_para_instancias(instancias: list[dict], registro,
                          detalhes_por_nome,
                          dbaccess_por_instancia: bool = True) -> dict:
    """Sobe um AppServer por instância paralela e anota o PID de cada uma.

    Antes de subir, grava no `appserver.ini` daquela instância as portas que o
    plano reservou. Sem isso todos os clones ficam com `[TCP] 8881` e
    `[HTTPREST] 8080` do template, e só o primeiro AppServer consegue escutar.

    Com `dbaccess_por_instancia`, cada uma ganha também o **próprio DbAccess**,
    na porta que o plano reservou — subido ANTES do AppServer, que precisa
    dele para abrir o ambiente.
    """
    subidos, erros = [], []
    for item in instancias:
        nome = item["ambiente"]
        if item.get("vivos", {}).get("appserver"):
            # Vivo não é o bastante: confere se a porta responde antes de
            # liberar o teste para essa instância.
            porta_viva = (item.get("portas") or {}).get("webapp")
            if porta_viva and not porta_responde(int(porta_viva)):
                erros.append({"ambiente": nome,
                              "erro": f"O AppServer está de pé mas a porta "
                                      f"{porta_viva} não responde."})
                continue
            subidos.append({"ambiente": nome, "pid": item["pids"]["appserver"],
                            "reaproveitado": True})
            continue

        detalhes = detalhes_por_nome(nome)
        if not detalhes.get("ok"):
            erros.append({"ambiente": nome, "erro": detalhes.get("erro", "")})
            continue
        banco = detalhes.get("banco") or {}
        exe = banco.get("appserver_exe", "")

        ini_app = appserver_ini.caminho_do_ini(exe)

        # O WebMonitor abre porta fixa em todo AppServer e não sai do plano de
        # portas: com vários clones, os seguintes falham com `error 10048`. O
        # TIR fala com o WebApp, não com o monitor.
        appserver_ini.desativar_webmonitor(ini_app)

        # Identidade do ambiente para o semáforo e para o controle de RPO. O
        # clone herda a `SpecialKey` do original, e com ela igual o Protheus
        # trata os clones como o MESMO ambiente — o segundo a entrar leva
        # "Identificados acessos utilizando RPO divergentes" e não abre. Sem
        # isto o paralelismo não existe, por mais que porta, banco e DbAccess
        # estejam separados.
        chave = appserver_ini.aplicar_specialkey(ini_app, f"T{item.get('slot', 0)}")
        if chave.get("mudou"):
            log.info("[INI] %s: SpecialKey própria (%s).", nome,
                     chave["specialkey"])

        portas = item.get("portas") or {}
        if portas:
            escrita = appserver_ini.aplicar_portas(
                appserver_ini.caminho_do_ini(exe), portas)
            if not escrita.get("ok"):
                erros.append({"ambiente": nome, "erro": escrita["erro"]})
                continue
            if escrita.get("faltando"):
                log.warning("[INI] %s: seções não encontradas: %s", nome,
                            ", ".join(escrita["faltando"]))

        # O DbAccess vem primeiro: sem ele o AppServer sobe, publica a porta e
        # trava na hora de abrir o ambiente — o login fica pendurado até o
        # timeout, sem erro claro.
        if dbaccess_por_instancia:
            porta_db = portas.get("dbaccess")
            db_exe = banco.get("dbaccess_exe", "")
            ini_db = dbaccess_ini.caminho_do_ini(db_exe)
            escrita_db = dbaccess_ini.aplicar_porta(ini_db, int(porta_db or 0))
            if not escrita_db.get("ok"):
                erros.append({"ambiente": nome, "erro": escrita_db["erro"]})
                continue
            db = subir_dbaccess_da_instancia(
                db_exe, int(porta_db or 0), banco.get("dbaccess_params", ""))
            if not db.get("ok"):
                erros.append({"ambiente": nome, "erro": f"DbAccess: {db['erro']}"})
                continue
            if db.get("pid"):
                registro.anotar_pid(nome, "dbaccess", db["pid"])

        resultado = subir(exe, banco.get("appserver_params", ""))
        if not resultado.get("ok"):
            erros.append({"ambiente": nome, "erro": resultado["erro"]})
            continue

        registro.anotar_pid(nome, "appserver", resultado["pid"])

        # Só entra na lista de "no ar" quem responde: liberar o teste com a
        # porta ainda fechada é falha garantida no navegador.
        porta = portas.get("webapp") or banco.get("port")
        pronta = esperar_porta(int(porta or 0)) if porta else \
            {"ok": False, "erro": "Instância sem porta definida."}
        if not pronta.get("ok"):
            erros.append({"ambiente": nome, "erro": pronta["erro"]})
            continue

        subidos.append({"ambiente": nome, "pid": resultado["pid"],
                        "portas": portas, "reaproveitado": False})

    return {"ok": bool(subidos), "subidos": subidos, "erros": erros}
