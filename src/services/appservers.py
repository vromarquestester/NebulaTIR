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
import os
import socket
import subprocess
import time
from pathlib import Path

from services import appserver_ini, dbaccess_ini, webapp

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


def morreu_na_partida(proc: subprocess.Popen,
                      limite_seg: float | None = None) -> bool:
    """O processo encerrou dentro da janela de partida?

    Espera **até** o limite em vez de dormir cego e olhar depois: quem morre é
    detectado no instante em que morre, e quem sobrevive à janela é dado como
    no ar. Dormir o tempo cheio atrasava toda subida bem-sucedida em
    `ESPERA_SUBIDA_SEG`, e — pior — amarrava a deteção ao tempo de partida do
    binário. Numa máquina lenta o processo ainda estava nascendo quando a
    janela fechava, e um processo natimorto passava por vivo.
    """
    limite = ESPERA_SUBIDA_SEG if limite_seg is None else limite_seg
    try:
        proc.wait(timeout=limite)
    except subprocess.TimeoutExpired:
        return False
    return True


def porta_responde(porta: int, host: str = "127.0.0.1",
                   timeout: float = 1.0) -> bool:
    """Aceita conexão? É o que o navegador do TIR vai tentar."""
    if not porta:
        return False
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        return s.connect_ex((host, int(porta))) == 0


# ─────────────────────────────────────────────────────────────
# QUEM ESTÁ NA PORTA
# ─────────────────────────────────────────────────────────────
# A porta responder diz que ALGUÉM atende — não diz quem. Dois ambientes
# cadastrados na mesma porta (PAR_2510 e PAR_2610, os dois na 4321) apareciam
# ambos "no ar" com um só de pé, e o executar reaproveitava o errado. O dono é
# o processo que escuta a porta; o executável dele diz de qual ambiente é.

def pid_escutando(porta: int) -> int:
    """PID do processo que escuta `porta` em TCP, ou 0 se não achou.

    Lê o `netstat -ano`: existe em toda instalação do Windows, sem dependência
    nova. Fora do Windows devolve 0 — quem chama trata "não sei" como "não dá
    para desempatar" e mantém o comportamento antigo.
    """
    if not porta or os.name != "nt":
        return 0
    try:
        saida = subprocess.run(
            ["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=5,
            creationflags=_SEM_JANELA).stdout
    except (OSError, subprocess.SubprocessError):
        return 0
    return pid_no_netstat(saida, int(porta))


def pid_no_netstat(saida: str, porta: int) -> int:
    """Interpreta a saída do `netstat -ano`: linha LISTENING da porta → PID."""
    sufixo = f":{int(porta)}"
    for linha in (saida or "").splitlines():
        partes = linha.split()
        if len(partes) < 5 or partes[0].upper() != "TCP":
            continue
        local, estado, pid = partes[1], partes[3], partes[4]
        if estado.upper() != "LISTENING" or not local.endswith(sufixo):
            continue
        try:
            return int(pid)
        except ValueError:
            continue
    return 0


def exe_do_pid(pid: int) -> str:
    """Caminho do executável do processo, ou vazio se não deu para ler."""
    if not pid or os.name != "nt":
        return ""
    import ctypes
    from ctypes import wintypes
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not handle:
        return ""
    try:
        tamanho = wintypes.DWORD(32768)
        buffer = ctypes.create_unicode_buffer(tamanho.value)
        if not kernel32.QueryFullProcessImageNameW(handle, 0, buffer,
                                                   ctypes.byref(tamanho)):
            return ""
        return buffer.value
    finally:
        kernel32.CloseHandle(handle)


def dono_da_porta(porta: int) -> dict:
    """`{"pid": N, "exe": caminho}` de quem escuta a porta. Vazios se não soube."""
    pid = pid_escutando(porta)
    return {"pid": pid, "exe": exe_do_pid(pid) if pid else ""}


def mesmo_executavel(a: str, b: str) -> bool:
    """Compara caminhos de exe sem olhar caixa nem barra."""
    if not a or not b:
        return False
    return os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))


def mesma_pasta_do_appserver(exe_dono: str, appserver_exe: str) -> bool:
    """O processo que escuta a porta pertence a este AppServer?

    Comparar exe com exe falhava no real (2026-09-18): a porta do WebApp não
    é escutada pelo `appserver.exe`, e sim por um filho dele na mesma pasta,
    `.dyncall.exe`. Reproduzido subindo o PAR_2510: `netstat` dava a 4321 ao
    `C:/TOTVS/PAR_2510/Protheus/bin/appserver/.dyncall.exe`, e o
    desempate acusava "AppServer de outro ambiente" para o próprio ambiente.
    A pasta é o que identifica o ambiente — o nome do binário, não.
    """
    if not exe_dono or not appserver_exe:
        return False
    pasta = os.path.normcase(os.path.normpath(os.path.dirname(exe_dono)))
    return pasta == os.path.normcase(os.path.normpath(os.path.dirname(appserver_exe)))


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


def esperar_porta_livre(porta: int, limite_seg: float = 60,
                       intervalo_seg: float | None = None) -> dict:
    """Espera a porta parar de responder — o AppServer leva segundos para
    soltar tudo depois do `parar`, e clonar antes disso pega arquivo em uso."""
    intervalo = INTERVALO_SONDA_SEG if intervalo_seg is None else intervalo_seg
    fim = time.monotonic() + limite_seg
    while time.monotonic() < fim:
        if not porta_responde(int(porta)):
            return {"ok": True, "porta": int(porta)}
        time.sleep(intervalo)
    return {"ok": False,
            "erro": f"A porta {porta} continuou respondendo por {int(limite_seg)}s "
                    f"depois do parar. Pare o ambiente e tente de novo."}


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
    if morreu_na_partida(proc):
        return {"ok": False,
                "erro": f"O AppServer encerrou logo após subir "
                        f"(código {proc.returncode}). Porta ocupada ou "
                        f"appserver.ini inválido?"}

    log.info("[APPSERVER] %s no ar (PID %d).", exe.parent.name, proc.pid)
    return {"ok": True, "pid": proc.pid, "exe": str(exe)}


NOME_DBACCESS = "dbaccess64.exe"


PORTA_DBACCESS_PADRAO = 7890
ESPERA_DBACCESS_SEG = 30


def porta_do_dbaccess(dbaccess_exe: str) -> int:
    """Porta do `dbaccess.ini` ao lado do exe; 7890 quando não há chave."""
    from services import dbaccess_ini
    return dbaccess_ini.ler_porta(dbaccess_ini.caminho_do_ini(dbaccess_exe)) \
        or PORTA_DBACCESS_PADRAO


def dbaccess_no_ar(porta: int = 0) -> bool:
    """O DbAccess **desta porta** responde?

    Por porta, não por nome de processo. A corrida das 14:12 de 2026-09-18
    provou o defeito: com DbAccess isolado por instância, o do clone (7891)
    sobe antes do pai; o `tasklist` achava um `dbaccess64.exe` qualquer,
    dizia "já estava no ar", e o pai subia sem o seu na 7890 — `Falha ao
    conectar no DbAccess` no console e login pendurado. Sem porta (chamada
    antiga) cai no nome do processo.
    """
    if porta:
        return porta_responde(int(porta), timeout=1.0)
    try:
        saida = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {NOME_DBACCESS}",
                                "/NH"], capture_output=True, text=True, timeout=15,
                               creationflags=_SEM_JANELA).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return NOME_DBACCESS.casefold() in (saida or "").casefold()


def parar_dbaccess(porta: int = 0) -> bool:
    """Encerra o DbAccess. Só para trocar o `.ini` dele.

    Com porta, mata **só o dono daquela porta** — matar por nome de imagem
    levaria os DbAccess isolados das instâncias junto. Sem porta, o
    comportamento antigo.
    """
    try:
        pid = pid_escutando(int(porta)) if porta else 0
        if porta and not pid:
            return True
        alvo = ["/PID", str(pid)] if pid else ["/IM", NOME_DBACCESS]
        subprocess.run(["taskkill", "/F", *alvo], capture_output=True,
                       timeout=30, creationflags=_SEM_JANELA)
    except (OSError, subprocess.SubprocessError):
        return False
    for _ in range(10):
        if not dbaccess_no_ar(porta):
            return True
        time.sleep(1)
    return False


def garantir_dbaccess(dbaccess_exe: str, params: str = "",
                      reiniciar: bool = False, porta: int = 0) -> dict:
    """Sobe o DbAccess da porta deste ambiente se ela não responder.
    **Nunca mata o que está no ar** (salvo `reiniciar`, e só o desta porta).

    Um DbAccess atende todos os bancos do `dbaccess.ini` dele, e
    `subir_dbaccess` do Gerenciador mata o existente antes de subir o dele —
    aqui isso derrubaria as instâncias já rodando.

    `porta` vem do `dbaccess.ini` ao lado do exe quando não informada. Depois
    de subir, espera a porta responder: o AppServer que vier em seguida
    tenta 1+2+4+8+16 s e desiste — era o que atrasava o WebApp em 60 s.
    """
    porta = int(porta or porta_do_dbaccess(dbaccess_exe))
    if dbaccess_no_ar(porta):
        if not reiniciar:
            return {"ok": True, "subiu": False, "porta": porta,
                    "motivo": f"já estava no ar (porta {porta})"}
        # O DbAccess lê o `dbaccess.ini` na partida: alias novo só vale depois
        # de reiniciar. Feito ANTES de subir qualquer AppServer, para não
        # derrubar instância nenhuma.
        log.info("[DBACCESS] Reiniciando o da porta %s para carregar os aliases novos…", porta)
        parar_dbaccess(porta)

    exe = Path(dbaccess_exe or "")
    if not exe.is_file():
        return {"ok": False, "erro": f"DbAccess não encontrado: {exe}"}
    try:
        proc = subprocess.Popen([str(exe)] + _params_com_console(params),
                                cwd=str(exe.parent), creationflags=_COM_CONSOLE)
    except OSError as e:
        return {"ok": False, "erro": f"Falha ao iniciar o DbAccess: {e}"}

    if morreu_na_partida(proc):
        return {"ok": False,
                "erro": f"O DbAccess encerrou logo após subir "
                        f"(código {proc.returncode})."}
    pronta = esperar_porta(porta, limite_seg=ESPERA_DBACCESS_SEG)
    if not pronta.get("ok"):
        return {"ok": False, "pid": proc.pid,
                "erro": f"O DbAccess subiu (PID {proc.pid}) mas a porta {porta} "
                        f"não respondeu em {ESPERA_DBACCESS_SEG}s."}
    log.info("[DBACCESS] No ar na porta %s (PID %d).", porta, proc.pid)
    return {"ok": True, "subiu": True, "pid": proc.pid, "porta": porta}


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

    if morreu_na_partida(proc):
        return {"ok": False,
                "erro": f"O DbAccess da instância encerrou logo após subir "
                        f"(código {proc.returncode}). Porta {porta} ocupada?"}

    pronta = esperar_porta(int(porta), limite_seg=ESPERA_DBACCESS_SEG)
    if not pronta.get("ok"):
        return {"ok": False, "erro": f"DbAccess não abriu a porta {porta}."}

    log.info("[DBACCESS] Instância no ar na porta %s (PID %d).", porta, proc.pid)
    return {"ok": True, "subiu": True, "pid": proc.pid, "porta": int(porta)}


def garantir_webagent(nome: str, detalhes_por_nome,
                      sincronizar_webagent=None) -> dict:
    """Deixa a estação com o WebAgent da release do ambiente.

    O agente é **um só na estação** e a família dele acompanha a release do
    Protheus: a 2610 pede `1.1.2-RC4`, as anteriores `1.0.25`. O Gerenciador
    troca isso no "Subir" dele — só que quem sobe as instâncias paralelas é o
    NebulaTIR, que não passa por lá. Alternar 2510 ↔ 2610 daqui deixava a
    estação com o agente da release anterior, e o navegador é quem descobria.

    A troca em si roda no Gerenciador (rota `/webagent`): o instalador está na
    pasta do ambiente e duas janelas trocando o mesmo `package.json` brigariam.

    **Nunca derruba a corrida.** Devolve `{"ok": False, "aviso": ...}` para o
    chamador registrar; subir com o agente errado é problema de navegador, e
    barrar a corrida por isso seria pior que avisar.
    """
    detalhes = detalhes_por_nome(nome)
    if not detalhes.get("ok"):
        return {"ok": False, "aviso": detalhes.get("erro", "")}

    estado = detalhes.get("webagent") or {}
    if not estado:
        # Gerenciador anterior à rota: não há o que conferir, e inventar um
        # alvo aqui criaria a segunda verdade que o canal existe para evitar.
        return {"ok": True, "conferido": False}
    if estado.get("sincronizado"):
        return {"ok": True, "conferido": True, "trocado": False,
                "alvo": estado.get("alvo", "")}

    alvo = estado.get("alvo", "") or "?"
    atual = estado.get("estacao", "") or "nenhum"
    if not estado.get("tem_instalador"):
        return {"ok": False, "conferido": True,
                "aviso": f"O ambiente {nome} (Protheus "
                         f"{estado.get('versao_protheus') or '?'}) precisa do "
                         f"WebAgent {alvo} e a estação tem {atual}, mas o "
                         f"instalador não está em <raiz>/web-agent. Rode o "
                         f"Executar completo no Gerenciador para provisioná-lo."}

    if sincronizar_webagent is None:
        return {"ok": False, "conferido": True,
                "aviso": f"O ambiente {nome} precisa do WebAgent {alvo} e a "
                         f"estação tem {atual}."}

    troca = sincronizar_webagent(nome)
    if not troca.get("ok"):
        return {"ok": False, "conferido": True,
                "aviso": f"WebAgent {alvo} não instalado ({troca.get('erro') or '?'}); "
                         f"a estação segue em {atual}."}
    return {"ok": True, "conferido": True, "trocado": bool(troca.get("trocado")),
            "de": troca.get("de", ""), "para": troca.get("para", ""), "alvo": alvo}


def subir_para_instancias(instancias: list[dict], registro,
                          detalhes_por_nome,
                          dbaccess_por_instancia: bool = True,
                          sincronizar_webagent=None) -> dict:
    """Sobe um AppServer por instância paralela e anota o PID de cada uma.

    Antes de subir, grava no `appserver.ini` daquela instância as portas que o
    plano reservou. Sem isso todos os clones ficam com `[TCP] 8881` e
    `[HTTPREST] 8080` do template, e só o primeiro AppServer consegue escutar.

    Com `dbaccess_por_instancia`, cada uma ganha também o **próprio DbAccess**,
    na porta que o plano reservou — subido ANTES do AppServer, que precisa
    dele para abrir o ambiente.
    """
    subidos, erros, avisos = [], [], []

    # Uma vez por corrida, não por instância: o agente é da estação, e as
    # instâncias são clones do mesmo ambiente — logo, da mesma release.
    if instancias:
        agente = garantir_webagent(instancias[0]["ambiente"], detalhes_por_nome,
                                   sincronizar_webagent)
        if agente.get("aviso"):
            avisos.append(agente["aviso"])
            log.warning("[AGENT] %s", agente["aviso"])
        elif agente.get("trocado"):
            log.info("[AGENT] WebAgent da estação: %s → %s",
                     agente.get("de") or "nenhum", agente.get("para"))

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
        appserver_ini.desativar_appmonitor(ini_app)

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

        # WebApp da release, por instância: cada clone tem a própria pasta e
        # o próprio `webapp.dll`. Falha vira aviso — subir com a DLL errada é
        # ERR0003 no navegador, mas barrar a instância inteira seria pior.
        dll = webapp.garantir(detalhes.get("webapp"), Path(exe).parent, nome=nome)
        if dll.get("trocado"):
            log.info("[WEBAPP] %s: WebApp %s → %s.", nome, dll.get("de") or "embutido",
                     dll.get("alvo"))
        if dll.get("aviso"):
            avisos.append(f"WebApp: {dll['aviso']}")
            log.warning("[WEBAPP] %s", dll["aviso"])

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

    return {"ok": bool(subidos), "subidos": subidos, "erros": erros,
            "avisos": avisos}
