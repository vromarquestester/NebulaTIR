"""Alocação de portas para execução paralela de ambientes.

Cada instância paralela é um AppServer próprio, e AppServer não compartilha
porta de escuta. Este módulo reparte as portas variáveis entre os slots.

Imutável, por decisão do usuário — não entra na alocação:

    [LICENSECLIENT] server=licensedev.totvs.com.br / port=8009

**`DBPort` deixou de ser imutável.** O desenho original era um único DbAccess
para todos os ambientes: o `dbaccess.ini` indexa por alias (`[MSSQL/<banco>]`),
então um processo bastava. Só que em corrida paralela as instâncias travavam
sem causa visível em nenhum log, e o DbAccess compartilhado é o único ponto
que todas dividem. A TOTVS documenta várias instâncias na mesma máquina, cada
uma em outra porta (`[GENERAL] Port` do `dbaccess.ini`), então dá para isolar
e observar se o sintoma some.

Isto é hipótese em teste, não conclusão: `dbaccess` entra na alocação como
qualquer outra porta, e o comportamento é comutável em
`preferencias.dbaccess_por_instancia`. Voltando a chave para falso, tudo
volta ao DbAccess único.

Regra de alocação, conforme pedido: soma 1 à porta; se estiver em uso, pega a
próxima livre; segue até achar. Tudo automático, sem perguntar nada.
"""

from __future__ import annotations

import logging
import socket

log = logging.getLogger(__name__)

# Chave lógica → porta do ambiente de referência. A do webapp vem do ambiente
# (cada um já tem a sua no Gerenciador), as outras saem do template.
BASES = {
    "webapp": 4321,
    "tcp": 8881,
    "httprest": 8080,
    "webagent": 21021,
    "sqlite": 5056,
    # Só entra quando cada instância tem o próprio DbAccess. Ver `alocar`.
    "dbaccess": 7890,
}

ROTULOS = {
    "webapp": "WebApp",
    "tcp": "TCP",
    "httprest": "HTTP REST",
    "webagent": "WebAgent",
    "sqlite": "SQLite",
    "dbaccess": "DbAccess",
}

# Fica de fora da alocação e é informada na tela como fixa.
IMUTAVEIS = {"licenseclient": 8009}

# A porta do DbAccess quando ele é um só para todos — o valor do template.
DBACCESS_PADRAO = 7890

PORTA_MIN = 1024
PORTA_MAX = 65535
LIMITE_BUSCA = 500   # portas tentadas antes de desistir de uma chave


def porta_livre(porta: int, host: str = "127.0.0.1") -> bool:
    """Livre = dá para escutar nela agora.

    Testa com `bind`, não com `connect`: uma porta sem ninguém escutando
    recusa conexão, mas pode estar reservada pelo sistema. `bind` é o mesmo
    que o AppServer vai fazer.
    """
    if not PORTA_MIN <= porta <= PORTA_MAX:
        return False
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, porta))
            return True
        except OSError:
            return False


def proxima_livre(inicio: int, reservadas: set[int],
                  checar=porta_livre) -> int | None:
    """Primeira porta livre a partir de `inicio`, pulando as já reservadas
    nesta mesma alocação — duas instâncias não podem receber a mesma."""
    porta = max(inicio, PORTA_MIN)
    for _ in range(LIMITE_BUSCA):
        if porta > PORTA_MAX:
            return None
        if porta not in reservadas and checar(porta):
            return porta
        porta += 1
    return None


def alocar(slots: int, base_webapp: int | None = None,
           checar=porta_livre, dbaccess_por_instancia: bool = True) -> dict:
    """Distribui as portas variáveis entre `slots` instâncias.

    O slot 1 tenta ficar com as portas originais do ambiente — assim uma
    execução sequencial (1 slot) não muda nada do que já funciona.

    Com `dbaccess_por_instancia=False`, todas apontam para a 7890 do template
    e um único processo atende todo mundo — o desenho anterior, mantido para
    dar meia-volta se o isolamento não resolver.
    """
    if slots < 1:
        return {"ok": False, "erro": "É preciso ao menos uma instância."}

    bases = dict(BASES)
    if base_webapp:
        bases["webapp"] = int(base_webapp)
    if not dbaccess_por_instancia:
        bases.pop("dbaccess", None)

    reservadas: set[int] = set()
    instancias = []
    for indice in range(slots):
        portas, faltou = {}, None
        for chave, base in bases.items():
            escolhida = proxima_livre(base + indice, reservadas, checar)
            if escolhida is None:
                faltou = chave
                break
            reservadas.add(escolhida)
            portas[chave] = escolhida
        if faltou:
            return {"ok": False,
                    "erro": f"Sem porta livre para {ROTULOS[faltou]} na "
                            f"instância {indice + 1}."}
        if not dbaccess_por_instancia:
            portas["dbaccess"] = DBACCESS_PADRAO
        instancias.append({
            "slot": indice + 1,
            "portas": portas,
            # Quem saiu do valor original merece destaque na tela: é o que o
            # appserver.ini daquele slot vai precisar sobrescrever.
            "deslocadas": [c for c, p in portas.items()
                           if c in bases and p != bases[c]],
        })

    return {"ok": True, "instancias": instancias, "imutaveis": dict(IMUTAVEIS)}
