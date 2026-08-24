"""Geração e manutenção dos ambientes paralelos.

Clonar é trabalho do Gerenciador (`clonar_ambiente`, 8 fases com rollback); o
NebulaTIR só decide **quantos**, **com que nome**, **em que porta** e guarda o
registro do que criou.

Credenciais: o usuário definiu que toda base usa `sa` / `123456`. Ficam aqui
como constante porque o clone precisa delas para criar o DSN e reconfigurar o
`dbaccess.ini` — mesmo quando a senha gravada está cifrada.

Sobre o RPO, dois caminhos que **não** se substituem:

- **zerado** — o RPO que o pipeline baixou para o `temp`. O `somente_rpo`
  limpa a pasta de destino antes de copiar, então apaga pacote e fonte
  compilados. Serve para comparar sem nada aplicado.
- **do ambiente** — a cópia que o NebulaTIR guardou do RPO como ele estava,
  com pacote e fonte. Serve para rodar os testes no estado real.

A cópia precisa existir antes: sem `guardar_rpo`, não há o que restaurar.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from services import portas as mod_portas
from services.instancias import PASTA_INSTANCIAS
from services.recursos import pasta_do_programa

log = logging.getLogger(__name__)

# Credenciais padrão de toda base, conforme definido pelo usuário.
ODBC_USER = "sa"
ODBC_PASS = "123456"

SUFIXO = "_TIR"          # PAR_2510 → PAR_2510_TIR1, _TIR2…
PASTA_SNAPSHOTS = "rpo_guardado"


def nome_paralelo(origem: str, slot: int) -> str:
    return f"{origem}{SUFIXO}{slot}"


def nome_banco_paralelo(banco_origem: str, slot: int) -> str:
    """`<banco do pai>_TIR<slot>`.

    O SQL Server recusa nome repetido, e o clone também — daí o sufixo. Mas a
    regra não é só evitar colisão com o pai: a instância do SQL tem bases de
    outras frentes, e é o sufixo que diz, olhando só a lista do MSSQL, de qual
    instância do NebulaTIR aquela base é. A primeira parte tem que continuar
    sendo o nome do banco do ambiente-pai, ou some o vínculo entre os dois.
    """
    return f"{banco_origem}{SUFIXO}{slot}"


def pasta_snapshots(ambiente: str) -> Path:
    return pasta_do_programa() / PASTA_SNAPSHOTS / ambiente


# ─────────────────────────────────────────────────────────────
# GERAÇÃO
# ─────────────────────────────────────────────────────────────

def gerar(*, origem: str, banco_origem: str, quantidade: int,
          estado_gerenciador, registro, plano_portas: dict,
          existentes: list[str] | None = None,
          caminhos_de=None,
          tempo_limite_seg: float = 5400) -> dict:
    """Clona o que faltar para chegar a `quantidade` instâncias.

    **Uma de cada vez.** O Gerenciador aceita uma operação por vez e o
    `clonar` devolve assim que dispara a thread, então cada clonagem é
    aguardada até o fim antes da próxima.

    `caminhos_de(ambiente, origem)` diz onde a instância ficou no disco. É
    anotado no registro na hora da criação, que é quando o Gerenciador
    certamente conhece o ambiente — depois que ele sai da lista de lá, esse
    dado não existe em lugar nenhum.

    Reaproveita o que já existe. **Instância existente não significa execução
    falha** — pode ser corrida concluída cuja limpeza ninguém fez. Por isso
    nada é apagado aqui: a exclusão é sempre manual, pelo botão.

    `tempo_limite_seg` é generoso (1h30) porque clonar copia MDF/LDF de vários
    GB e faz attach no SQL Server.
    """
    existentes = list(existentes or [])
    ja_tem = len(existentes)
    if ja_tem >= quantidade:
        return {"ok": True, "criados": [], "reaproveitados": existentes,
                "mensagem": f"{ja_tem} instância(s) já existiam; nada a clonar."}

    instancias_plano = plano_portas.get("instancias") or []
    criados, erros = [], []

    for indice in range(ja_tem, quantidade):
        slot = registro.proximo_slot(origem)
        ambiente = nome_paralelo(origem, slot)
        banco = nome_banco_paralelo(banco_origem, slot)
        # O slot 1 do plano é o ambiente principal; os paralelos pegam dos
        # seguintes, que já vêm com as portas deslocadas.
        portas_do_slot = (instancias_plano[indice]["portas"]
                          if indice < len(instancias_plano) else {})
        porta_webapp = portas_do_slot.get("webapp") or mod_portas.BASES["webapp"]

        log.info("[PARALELO] Clonando %s → %s (porta %s)…",
                 origem, ambiente, porta_webapp)
        resposta = estado_gerenciador.clonar(
            origem=origem, novo_ambiente=ambiente, novo_banco=banco,
            port=str(porta_webapp), odbc_user=ODBC_USER, odbc_pass=ODBC_PASS,
            # As instâncias ficam agrupadas e o temp do pai não é duplicado:
            # o que era copiado por clone (RPO, dicionários, ZIPs) já está lá,
            # congelado do dia, e nada o escreve durante a clonagem.
            subpasta=PASTA_INSTANCIAS, reaproveitar_temp=True)

        if not resposta.get("ok"):
            erros.append({"ambiente": ambiente, "erro": resposta.get("erro", "")})
            # Clonagem é cara e sequencial no Gerenciador: falhou uma, parar.
            break

        # `clonar` devolve na hora porque roda em thread — `ok` ali significa
        # "começou", não "terminou". Sem esperar, o próximo pedido bate no
        # `_ocupar` do Gerenciador e volta "Já existe uma operação em
        # andamento": era isso que fazia parar no primeiro ambiente.
        espera = estado_gerenciador.esperar_ocioso(limite_seg=tempo_limite_seg)
        if not espera.get("ok"):
            erros.append({"ambiente": ambiente,
                          "erro": f"A clonagem não terminou: {espera.get('erro', '')}"})
            break

        # `ok` da thread não é garantia: a clonagem pode ter falhado no meio.
        # O ambiente aparecer no Gerenciador é a prova.
        estado_gerenciador.atualizar()
        if estado_gerenciador.banco_por_nome(ambiente) is None:
            erros.append({"ambiente": ambiente,
                          "erro": "A clonagem terminou, mas o ambiente não "
                                  "apareceu no Gerenciador. Veja o log dele."})
            break

        registro.registrar(ambiente=ambiente, origem=origem, slot=slot,
                           banco=banco, portas=portas_do_slot,
                           caminhos=(caminhos_de(ambiente, origem)
                                     if caminhos_de else None))
        criados.append({"ambiente": ambiente, "banco": banco,
                        "portas": portas_do_slot, "slot": slot})
        log.info("[PARALELO] %s pronto.", ambiente)

    return {"ok": bool(criados) or not erros, "criados": criados,
            "reaproveitados": existentes, "erros": erros}


# ─────────────────────────────────────────────────────────────
# RPO
# ─────────────────────────────────────────────────────────────

_RE_DATA = __import__("re").compile(r"^\d{2}_\d{2}_\d{4}$")


def localizar_rpo_zerado(pasta_destino: str, ambiente: str) -> Path | None:
    """RPO baixado, na pasta de temp do Gerenciador.

    Estrutura: `<pasta_destino>/<ambiente>/temp/<DD_MM_AAAA>/`. Exemplo real:
    `C:\\TOTVS\\Downloads\\PAR_2510_V1\\temp\\31_07_2026`. Vale a pasta de data
    mais recente que contenha `.rpo`.
    """
    raiz = Path(pasta_destino or "") / ambiente / "temp"
    if not raiz.is_dir():
        return None
    candidatas = []
    for sub in raiz.iterdir():
        if sub.is_dir() and _RE_DATA.match(sub.name) and any(sub.glob("*.rpo")):
            try:
                from datetime import datetime
                candidatas.append((datetime.strptime(sub.name, "%d_%m_%Y"), sub))
            except ValueError:
                continue
    if not candidatas:
        return None
    candidatas.sort(key=lambda x: x[0], reverse=True)
    return candidatas[0][1]


def restaurar_rpo_zerado_local(pasta_destino: str, ambiente: str,
                               rpo_destino: str) -> dict:
    """Copia o RPO do temp por cima do RPO do ambiente.

    Alternativa ao `somente_rpo` do Gerenciador quando o arquivo já está em
    disco: evita ocupar o Gerenciador inteiro (que fica `ocupado`) só para
    copiar um arquivo que já foi baixado.
    """
    origem = localizar_rpo_zerado(pasta_destino, ambiente)
    if origem is None:
        return {"ok": False,
                "erro": "Não achei o RPO baixado em "
                        f"{pasta_destino}\\{ambiente}\\temp. Rode o download "
                        "pelo Gerenciador primeiro."}
    destino = Path(rpo_destino or "")
    if not destino.parent.exists():
        return {"ok": False, "erro": f"Destino do RPO não existe: {destino}"}

    destino.mkdir(parents=True, exist_ok=True)
    copiados = 0
    for arquivo in origem.glob("*.rpo"):
        shutil.copy2(arquivo, destino / arquivo.name)
        copiados += 1
    log.info("[RPO] %d arquivo(s) do RPO zerado copiados de %s para %s.",
             copiados, origem, destino)
    return {"ok": True, "origem": str(origem), "destino": str(destino),
            "arquivos": copiados}


def guardar_rpo(ambiente: str, rpo_dir: str) -> dict:
    """Copia o RPO do ambiente para poder repor depois com pacote e fonte."""
    origem = Path(rpo_dir or "")
    if not origem.is_dir():
        return {"ok": False, "erro": f"Diretório do RPO não encontrado: {origem}"}

    destino = pasta_snapshots(ambiente)
    if destino.exists():
        shutil.rmtree(destino, ignore_errors=True)
    destino.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(origem, destino)
    arquivos = sum(1 for _ in destino.rglob("*") if _.is_file())
    log.info("[RPO] Guardado o RPO de %s (%d arquivos) em %s.",
             ambiente, arquivos, destino)
    return {"ok": True, "pasta": str(destino), "arquivos": arquivos}


def tem_rpo_guardado(ambiente: str) -> bool:
    pasta = pasta_snapshots(ambiente)
    return pasta.is_dir() and any(pasta.rglob("*"))


def restaurar_rpo_do_ambiente(ambiente: str, rpo_dir: str) -> dict:
    """Repõe o RPO guardado — o estado com pacote e fonte aplicados."""
    guardado = pasta_snapshots(ambiente)
    if not tem_rpo_guardado(ambiente):
        return {"ok": False,
                "erro": "Não há RPO guardado deste ambiente. Use “Guardar RPO” "
                        "antes de rodar os testes."}
    destino = Path(rpo_dir or "")
    if not destino.parent.exists():
        return {"ok": False, "erro": f"Destino do RPO não existe: {destino}"}

    if destino.exists():
        shutil.rmtree(destino, ignore_errors=True)
    shutil.copytree(guardado, destino)
    log.info("[RPO] RPO de %s restaurado a partir da cópia guardada.", ambiente)
    return {"ok": True, "pasta": str(destino)}
