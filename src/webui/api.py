"""Ponte JS ↔ Python do NebulaTIR.

Os métodos públicos desta classe viram `window.pywebview.api.<metodo>(...)` no
JavaScript e devolvem sempre estruturas JSON-serializáveis.

Contrato de retorno das ações:
    {"ok": True,  ...}                 sucesso
    {"ok": False, "erro": "mensagem"}  falha tratada (a UI exibe o texto)

Regra que atravessa o arquivo inteiro: **nada acontece com o Gerenciador de
Ambientes offline**. Cada ação confere o link antes de agir — a UI já desabilita
os botões, mas o backend não confia na UI.
"""

from __future__ import annotations

import logging
import os
import queue
import threading
import webbrowser
from pathlib import Path

from _version import __version__
from services import (
    analise_casos,
    appservers,
    catalogo_testes,
    config_tir,
    dbaccess_ini,
    execucao,
    inventario,
    limpeza,
    navegadores,
    paralelos,
    portas,
    preparacao,
)
from services import instancias as instancias_mod
from services.instancias import Instancias
from services.gerenciador_client import EstadoGerenciador
from services.importados import RepositorioImportados
from services.preferencias import MODOS, Preferencias
from webui import log_bridge

log = logging.getLogger(__name__)

MOTIVO_OFFLINE = ("O Gerenciador de Ambientes precisa estar aberto. "
                  "O NebulaTIR usa as funções dele para trabalhar.")


class Api:
    """Objeto exposto ao JavaScript (pywebview `js_api`)."""

    def __init__(self, arquivo_importados=None, iniciar_monitores: bool = True,
                 instalar_log: bool = True, estado=None,
                 arquivo_preferencias=None, checar_porta=None,
                 arquivo_instancias=None):
        """`arquivo_importados` / `iniciar_monitores` / `instalar_log` existem
        para teste: em produção o padrão vale."""
        self._importados = RepositorioImportados(arquivo_importados)
        self._prefs = Preferencias(arquivo_preferencias)
        # Injetável no teste: o que está livre na máquina muda a cada dia, e o
        # teste passaria ou falharia conforme o que estivesse aberto.
        self._checar_porta = checar_porta or portas.porta_livre
        self._estado = estado or EstadoGerenciador()
        # O registro mora em `<base_path>\NebulaInstancia`, e `base_path` vem do
        # Gerenciador — que ainda não respondeu neste ponto do boot. Por isso um
        # chamável: o caminho é resolvido na primeira leitura depois do link.
        self._instancias = Instancias(
            arquivo_instancias,
            base_path=lambda: self._estado.instantaneo.get("base_path", ""))

        self._fila = queue.Queue()
        if instalar_log:
            log_bridge.instalar_handler(self._fila)

        # Todo estado fica em atributo PRIVADO de propósito: o pywebview varre
        # `dir(js_api)` a cada load e desce recursivamente em qualquer atributo
        # público não-chamável (webview/util.py:get_functions). Um atributo
        # público apontando para a Window faz ele enumerar `window.native` e
        # percorrer milhares de propriedades .NET/COM até estourar a recursão —
        # minutos de janela travada no boot.
        self._window = None
        self._execucao = None       # corrida em andamento, se houver
        self._pid_principal = 0     # AppServer do ambiente principal, se subimos
        # O ambiente principal foi subido por NÓS? Só o que subimos é nosso
        # para derrubar. Reiniciar um AppServer que já atendia era o que fazia
        # Gerenciador e NebulaTIR perderem a noção de quem está no ar.
        self._principal_e_nosso = True
        # Exclusão de paralelos: roda em thread e a UI acompanha por polling.
        self._exclusao = {"ativa": False, "atual": "", "feitos": 0,
                          "total": 0, "removidos": [], "erros": []}
        # Limpeza do que o Gerenciador não alcança mais. Separada da exclusão
        # de propósito: são caminhos diferentes, e as duas nunca rodam juntas.
        self._limpeza = {"ativa": False, "atual": "", "feitos": 0,
                         "total": 0, "removidos": [], "erros": [],
                         "recusadas": []}

        if iniciar_monitores:
            self._estado.iniciar()

    # ── ciclo de vida (privados: não vão para o JS) ──
    def _set_window(self, janela) -> None:
        self._window = janela

    def _encerrar(self) -> None:
        self._estado.parar()

    # ─────────────────────────────────────────────────────────
    # ESTADO / LEITURA
    # ─────────────────────────────────────────────────────────

    def get_bootstrap(self) -> dict:
        """Payload inicial da UI."""
        return {
            "versao": __version__,
            "importados": self._importados.listar(),
            "preferencias": self._prefs.tudo,
            "modos": list(MODOS),
        }

    # ─────────────────────────────────────────────────────────
    # PREFERÊNCIAS (globais: o teto é da máquina, não do ambiente)
    # ─────────────────────────────────────────────────────────

    def get_preferencias(self) -> dict:
        return {"ok": True, "preferencias": self._prefs.tudo}

    def salvar_preferencias(self, dados: dict) -> dict:
        return self._prefs.salvar(dados)

    def get_status(self) -> dict:
        """Estado volátil consultado em polling pela UI (a cada 2 s).

        `link` é o gate mestre: com ele em False a interface trava tudo. VPN e
        SQL vêm do Gerenciador — o NebulaTIR não mede nenhum dos dois, para não
        exibir um estado divergente do que a outra janela mostra.
        """
        g = self._estado.instantaneo
        ambientes = {}
        for nome in self._importados.nomes:
            info = dict(g["ambientes"].get(nome) or {})
            banco = self._estado.banco_por_nome(nome) or {}
            info.setdefault("estado", "stopped")
            info["existe_no_gerenciador"] = bool(banco)
            info["port"] = banco.get("port", "")

            # ── Estado EFETIVO, não estado declarado ──
            #
            # O Gerenciador responde `running` só quando ele mesmo é o pai do
            # processo (`process_registry.status`, que guarda o Popen em
            # memória). Um AppServer subido pelo NebulaTIR — ou pelo próprio
            # Gerenciador antes de ser reaberto — fica invisível para ele, e a
            # bolinha aqui mostrava "parado" com o ambiente atendendo.
            #
            # A porta responder é observável e não tem dono: vale para quem
            # quer que tenha subido, e sobrevive ao reinício dos dois lados.
            info["porta_responde"] = self._porta_no_ar(info["port"])
            # `fonte_estado` vai sempre: quem lê precisa saber se o "no ar" veio
            # do Gerenciador (há handle, dá para parar por lá) ou da porta (o
            # processo é de outro dono).
            info["fonte_estado"] = "gerenciador"
            if info["porta_responde"] and info["estado"] != "running":
                info["estado"] = "running"
                info["fonte_estado"] = "porta"
            ambientes[nome] = info

        return {
            "link": g["online"],
            # Canal sem resposta, mas ainda dentro da tolerância: o Gerenciador
            # costuma sumir por dezenas de segundos em operação longa.
            "link_instavel": bool(g.get("instavel")),
            "link_motivo": g["motivo"] or (MOTIVO_OFFLINE if not g["online"] else ""),
            "gerenciador_versao": g["versao"],
            "vpn": g["vpn"],
            "config_valida": g["config_valida"],
            "config_motivo": g["config_motivo"],
            "ocupado": g["ocupado"],
            "operacao": g["operacao"],
            "conexao_ativa": g["conexao_ativa"],
            # Fase da operação em curso no Gerenciador (clonagem, restauração).
            "andamento": g.get("andamento") or {},
            # Exclusão de paralelos em andamento: a UI trava os comandos e
            # mostra qual ambiente está saindo.
            "exclusao": dict(self._exclusao),
            "importados": self._importados.nomes,
            "ambientes": ambientes,
        }

    def _porta_no_ar(self, porta) -> bool:
        """A porta do ambiente aceita conexão?

        Timeout curto de propósito: isto roda para cada ambiente importado a
        cada ciclo de status (2 s). Em loopback, quem vai responder responde em
        milissegundos; esperar mais só atrasaria a tela quando ninguém atende.
        """
        try:
            numero = int(str(porta).strip())
        except (TypeError, ValueError):
            return False
        if not numero:
            return False
        return appservers.porta_responde(numero, timeout=0.2)

    def poll_logs(self, limite: int = 200) -> list:
        """Drena a fila de logs (não bloqueia)."""
        eventos = []
        try:
            while len(eventos) < limite:
                eventos.append(self._fila.get_nowait())
        except queue.Empty:
            pass
        return eventos

    # ─────────────────────────────────────────────────────────
    # AMBIENTES
    # ─────────────────────────────────────────────────────────

    def listar_disponiveis(self) -> dict:
        """Ambientes do Gerenciador que ainda não foram importados."""
        if not self._estado.online:
            return {"ok": False, "erro": MOTIVO_OFFLINE}
        g = self._estado.instantaneo
        ja_tem = set(self._importados.nomes)
        disponiveis = []
        for banco in g["bancos"]:
            nome = banco.get("ambiente") or banco.get("nome_banco")
            if not nome or nome in ja_tem:
                continue
            info = g["ambientes"].get(nome) or {}
            disponiveis.append({
                "nome": nome,
                "nome_banco": banco.get("nome_banco", ""),
                "port": banco.get("port", ""),
                "localizacao": banco.get("localizacao", ""),
                "versao": banco.get("versao", ""),
                "conexao": banco.get("connection", ""),
                "estado": info.get("estado", "stopped"),
                "provisionado": info.get("provisionado", False),
            })
        return {"ok": True, "ambientes": disponiveis,
                "total_no_gerenciador": len(g["bancos"])}

    def importar_ambiente(self, nome: str) -> dict:
        """Importa por nome. O ambiente continua vivendo no Gerenciador.

        Junto vem a configuração inicial do TIR: `Url` a partir da porta e
        `Environment` a partir da seção do `appserver.ini` — os dois campos que
        o usuário não deveria ter que digitar, já que o Gerenciador sabe.
        """
        if not self._estado.online:
            return {"ok": False, "erro": MOTIVO_OFFLINE}
        if not self._estado.banco_por_nome(nome):
            return {"ok": False, "erro": f"'{nome}' não existe no Gerenciador."}

        detalhes = self._estado.detalhes_por_nome(nome)
        config = config_tir.padrao_para(
            url=detalhes.get("url_ambiente", "") if detalhes.get("ok") else "",
            ambiente_ini=detalhes.get("ambiente_ini", "") if detalhes.get("ok") else "",
            navegador=navegadores.preferido(),
            # O idioma sai da localização do ambiente: testcase de MEX é
            # escrito em espanhol e não acha um só campo numa tela em
            # português.
            pais=self._estado.pais_do_ambiente(nome),
        )
        return self._importados.importar(nome, config)

    def remover_importado(self, nome: str) -> dict:
        """Remove a referência local. Não apaga nada no Gerenciador."""
        if not self._estado.online:
            return {"ok": False, "erro": MOTIVO_OFFLINE}
        return self._importados.remover(nome)

    def detalhes_importado(self, nome: str) -> dict:
        """Config completa do ambiente, lida do Gerenciador na hora."""
        if not self._estado.online:
            return {"ok": False, "erro": MOTIVO_OFFLINE}
        if not self._importados.contem(nome):
            return {"ok": False, "erro": "Ambiente não está importado."}
        detalhes = self._estado.detalhes_por_nome(nome)
        if not detalhes.get("ok"):
            return detalhes
        info = self._estado.instantaneo["ambientes"].get(nome) or {}
        detalhes["execucao"] = {
            "estado": info.get("estado", "stopped"),
            "provisionado": info.get("provisionado", False),
            "tem_appserver": info.get("tem_appserver", False),
            "tem_dbaccess": info.get("tem_dbaccess", False),
            "tem_ini": info.get("tem_ini", False),
        }
        return detalhes

    def sincronizar(self) -> dict:
        """Releitura imediata, sem esperar o ciclo de 2 s."""
        estado = self._estado.atualizar()
        if not estado["online"]:
            return {"ok": False, "erro": estado["motivo"] or MOTIVO_OFFLINE}
        orfaos = [n for n in self._importados.nomes
                  if not self._estado.banco_por_nome(n)]
        if orfaos:
            log.warning("[SYNC] Sem correspondência no Gerenciador: %s",
                        ", ".join(orfaos))
        return {"ok": True, "orfaos": orfaos,
                "total": len(self._importados.nomes)}

    # ─────────────────────────────────────────────────────────
    # CATÁLOGO DE TESTES
    # ─────────────────────────────────────────────────────────

    def listar_testes(self, nome: str, busca: str = "") -> dict:
        """Rotinas do país do ambiente, já filtradas pela busca.

        O país sai da `localizacao` do ambiente traduzida pela tabela do
        Gerenciador (`par` → `Paraguai`). Ambiente de país que não tem pasta
        de testes simplesmente não lista nada — e diz por quê.
        """
        if not self._importados.contem(nome):
            return {"ok": False, "erro": "Ambiente não está importado."}
        if not self._estado.online:
            return {"ok": False, "erro": MOTIVO_OFFLINE}

        pais = self._estado.pais_do_ambiente(nome)
        if not pais:
            return {"ok": False,
                    "erro": "O ambiente não tem localização definida no "
                            "Gerenciador."}

        catalogo = catalogo_testes.escanear_pais(self._prefs.raiz_testes, pais)
        if not catalogo.get("ok"):
            return {**catalogo, "pais": pais, "rotinas": []}

        rotinas = catalogo_testes.filtrar(catalogo["rotinas"], busca)
        selecionadas = set(self._importados.selecao(nome))
        for rotina in rotinas:
            rotina["selecionada"] = rotina["rotina"] in selecionadas
        return {"ok": True, "pais": catalogo["pais"], "rotinas": rotinas,
                "total": len(catalogo["rotinas"]),
                "raiz": str(self._prefs.raiz_testes)}

    def salvar_selecao(self, nome: str, rotinas: list) -> dict:
        """Grava as rotinas escolhidas e devolve a árvore rotina → casos."""
        if not self._importados.contem(nome):
            return {"ok": False, "erro": "Ambiente não está importado."}
        gravado = self._importados.salvar_selecao(nome, list(rotinas or []))
        if not gravado.get("ok"):
            return gravado
        return self.get_selecao(nome)

    def get_selecao(self, nome: str) -> dict:
        """Árvore da seleção: cada rotina com os casos que o suite executa.

        Os casos são relidos do disco a cada chamada — o suite pode ter mudado
        desde a seleção, e mostrar a lista velha esconderia isso.
        """
        if not self._importados.contem(nome):
            return {"ok": False, "erro": "Ambiente não está importado."}

        escolhidas = self._importados.selecao(nome)
        if not escolhidas:
            return {"ok": True, "arvore": [], "total_casos": 0}

        pais = self._estado.pais_do_ambiente(nome) if self._estado.online else ""
        catalogo = catalogo_testes.escanear_pais(self._prefs.raiz_testes, pais) \
            if pais else {"ok": False, "rotinas": []}
        por_nome = {r["rotina"]: r for r in catalogo.get("rotinas", [])}

        arvore, total = [], 0
        for rotina in escolhidas:
            achada = por_nome.get(rotina)
            if achada is None:
                # Rotina selecionada que sumiu do disco: aparece marcada, não
                # some calada — senão o usuário executa achando que rodou.
                arvore.append({"rotina": rotina, "modulo": "", "casos": [],
                               "ausente": True, "tem_case": False})
                continue
            total += len(achada["casos"])
            arvore.append({**achada, "ausente": False})
        return {"ok": True, "arvore": arvore, "total_casos": total}

    # ─────────────────────────────────────────────────────────
    # EXECUÇÃO DO TIR
    # ─────────────────────────────────────────────────────────

    def pode_executar(self, nome: str = "") -> dict:
        """Por que o botão Executar TIR está (ou não) liberado.

        Devolve o motivo junto: botão apagado sem explicação é o que faz o
        usuário reabrir o programa achando que travou.
        """
        nome = nome or ""
        g = self._estado.instantaneo
        if not g["online"]:
            return {"ok": False, "motivo": MOTIVO_OFFLINE}
        if g["config_valida"] is not True:
            return {"ok": False, "motivo": "O Gerenciador está sem conexão SQL "
                                           "válida — resolva lá primeiro."}
        if g["vpn"] is not True:
            return {"ok": False, "motivo": "VPN offline."}
        if g["ocupado"]:
            return {"ok": False, "motivo": f"O Gerenciador está ocupado "
                                           f"({g['operacao'] or 'operação em curso'})."}
        if not self._importados.contem(nome):
            return {"ok": False, "motivo": "Selecione um ambiente importado."}
        if not self._importados.selecao(nome):
            return {"ok": False, "motivo": "Confirme ao menos uma rotina de teste."}
        if self._execucao is not None and self._execucao.ativa:
            return {"ok": False, "motivo": "Já existe uma execução em andamento."}
        if self._exclusao.get("ativa"):
            return {"ok": False,
                    "motivo": "Exclusão de ambientes em andamento — aguarde."}
        return {"ok": True, "motivo": ""}

    def executar_tir(self, nome: str) -> dict:
        """Dispara a corrida. Retorna na hora; a UI acompanha por polling."""
        liberado = self.pode_executar(nome)
        if not liberado["ok"]:
            return {"ok": False, "erro": liberado["motivo"]}

        self._ambiente_principal = nome
        selecao = self._importados.selecao(nome)
        catalogo = self.listar_testes(nome)
        if not catalogo.get("ok"):
            return catalogo
        por_nome = {r["rotina"]: r for r in catalogo["rotinas"]}
        rotinas = [por_nome[r] for r in selecao if r in por_nome]
        if not rotinas:
            return {"ok": False,
                    "erro": "As rotinas confirmadas não estão mais no disco."}

        # Divisão por caso: cada unidade vira um item da fila. Casos que
        # dependem uns dos outros ficam na mesma unidade, e portanto na mesma
        # instância e na ordem do suite.
        dividir = self._prefs.dividir_casos
        for rotina in rotinas:
            grupos, analise = analise_casos.unidades(rotina, dividir)
            rotina["unidades"] = grupos
            if analise is not None and len(grupos) != len(rotina.get("casos", [])):
                self._fila.put({"kind": "log", "level": "INFO",
                                "text": f"{rotina['rotina']}: {analise.motivo}"})

        ambiente_python = execucao.preparar_ambiente_python(self._fila)
        if not ambiente_python.get("ok"):
            return ambiente_python

        config = self._config_do_ambiente(nome)
        erro = config_tir.validar(config)
        if erro:
            return {"ok": False, "erro": erro}

        # ── Ambiente que já atende é reaproveitado, não derrubado ──
        #
        # Derrubar e subir de novo era o padrão, e criava o buraco: o
        # Gerenciador descarta o handle ao parar, o processo novo é filho do
        # NebulaTIR, e daí em diante NENHUM dos dois reconhece o ambiente — ele
        # fica no ar e os dois dizem "parado".
        #
        # Quem subiu, derruba. Se a porta já responde, o ambiente é de outro
        # dono: usamos como está e não o encerramos no fim.
        banco_principal = self._estado.banco_por_nome(nome) or {}
        porta_principal = banco_principal.get("port", "")
        self._principal_e_nosso = not self._porta_no_ar(porta_principal)

        if self._principal_e_nosso:
            # O Gerenciador segura um ambiente com AppServer e DbAccess
            # próprios. Sem derrubar isso, as portas e o DbAccess colidem com
            # os nossos.
            self._fila.put({"kind": "log", "level": "INFO",
                            "text": "[FASE] Liberando o ambiente do Gerenciador"})
            parada = self._estado.parar_ambiente(nome)
            if not parada.get("ok"):
                self._fila.put({"kind": "log", "level": "WARNING",
                                "text": f"Não consegui parar {nome} pelo "
                                        f"Gerenciador: {parada.get('erro', '')}"})
        else:
            self._fila.put({
                "kind": "log", "level": "INFO",
                "text": f"{nome} já está no ar na porta {porta_principal} — "
                        f"reaproveitando em vez de reiniciar.",
            })

        ambientes_por_slot = [nome]
        config_por_ambiente = {}
        if not self._prefs.paralelo:
            # Sequencial também roda sob o AppServer do NebulaTIR: acabamos de
            # parar o do Gerenciador, então sem subir de volta não há ninguém
            # atendendo a URL do teste.
            pronto_principal = self._subir_principal(nome)
            if not pronto_principal.get("ok"):
                return pronto_principal
        if self._prefs.paralelo:
            preparo = self._preparar_paralelos(nome)
            if not preparo.get("ok"):
                return preparo
            ambientes_por_slot = preparo["ambientes"]
            config_por_ambiente = self._config_por_instancia(
                config, ambientes_por_slot)

        # Restaurar entre rotinas é obrigatório, inclusive em paralelo: sem
        # isso a instância que liberar primeiro pega a base no estado que o
        # teste anterior deixou, e o resultado deixa de valer. Isso só passou a
        # ser possível quando o pipeline do Gerenciador virou escopado por
        # ambiente — antes ele encerrava `appserver.exe` por nome de imagem e
        # derrubava as instâncias vizinhas.
        restaurar_entre_rotinas = True

        self._execucao = execucao.Execucao(
            ambiente=nome, rotinas=rotinas, config=config,
            estado_gerenciador=self._estado, fila_eventos=self._fila,
            instancias=len(ambientes_por_slot),
            ambientes_por_slot=ambientes_por_slot,
            config_por_ambiente=config_por_ambiente,
            restaurar_banco=restaurar_entre_rotinas,
            religar_ambiente=self._religar_ambiente)
        self._execucao.iniciar()
        log.info("[TIR] Execução iniciada em %s: %d rotinas em %d instância(s).",
                 nome, len(rotinas), len(ambientes_por_slot))
        return {"ok": True, "rotinas": len(rotinas),
                "instancias": len(ambientes_por_slot),
                "ambientes": ambientes_por_slot,
                "versao_tir": ambiente_python.get("versao_tir", "")}

    def _garantir_dbaccess(self, nome: str, paralelos_nomes: list | None = None) -> dict:
        """Um DbAccess atende todos os bancos — mas precisa conhecer todos.

        O clone grava o alias no `dbaccess.ini` do próprio ambiente, e esse
        processo nunca sobe. Sem consolidar, o AppServer do clone pede um
        banco que o DbAccess em execução desconhece, e o login fica pendurado
        até o timeout, sem erro claro.
        """
        detalhes = self._estado.detalhes_por_nome(nome)
        if not detalhes.get("ok"):
            return {"ok": True}     # sem detalhes, deixa o AppServer reclamar
        banco = detalhes.get("banco") or {}
        exe = banco.get("dbaccess_exe", "")

        mudou = False
        if paralelos_nomes:
            destino = dbaccess_ini.caminho_do_ini(exe)
            origens = []
            for paralelo in paralelos_nomes:
                d = self._estado.detalhes_por_nome(paralelo)
                if d.get("ok"):
                    origens.append(dbaccess_ini.caminho_do_ini(
                        (d.get("banco") or {}).get("dbaccess_exe", "")))
            junta = dbaccess_ini.consolidar(destino, origens)
            mudou = junta.get("mudou", False)
            if junta.get("adicionados"):
                self._fila.put({
                    "kind": "log", "level": "INFO",
                    "text": "DbAccess: aliases adicionados — "
                            + ", ".join(junta["adicionados"]),
                })

        db = appservers.garantir_dbaccess(exe, banco.get("dbaccess_params", ""),
                                          reiniciar=mudou)
        self._fila.put({
            "kind": "log",
            "level": "INFO" if db.get("ok") else "ERROR",
            "text": f"DbAccess: {db.get('motivo') or db.get('erro') or 'iniciado'}",
        })
        return db

    def _subir_principal(self, nome: str) -> dict:
        """Sobe DbAccess e AppServer do ambiente principal e espera a porta."""
        db = self._garantir_dbaccess(nome)
        if not db.get("ok"):
            return {"ok": False, "erro": f"DbAccess: {db.get('erro')}"}

        detalhes = self._estado.detalhes_por_nome(nome)
        banco = (detalhes.get("banco") or {}) if detalhes.get("ok") else {}
        porta = str(banco.get("port") or "").strip()

        if porta and appservers.porta_responde(int(porta)):
            self._fila.put({"kind": "log", "level": "INFO",
                            "text": f"{nome} já responde na porta {porta}."})
            return {"ok": True, "reaproveitado": True}

        self._fila.put({"kind": "log", "level": "INFO",
                        "text": f"[FASE] Subindo o AppServer de {nome}"})
        subida = appservers.subir(banco.get("appserver_exe", ""),
                                  banco.get("appserver_params", ""))
        if not subida.get("ok"):
            return {"ok": False, "erro": subida["erro"]}
        self._pid_principal = subida["pid"]

        pronta = appservers.esperar_porta(int(porta or 0)) if porta else \
            {"ok": False, "erro": "Ambiente sem porta configurada."}
        if not pronta.get("ok"):
            return {"ok": False, "erro": pronta["erro"]}
        self._fila.put({"kind": "log", "level": "INFO",
                        "text": f"{nome} no ar na porta {porta}."})
        return {"ok": True}

    def _religar_ambiente(self, ambiente: str) -> dict:
        """Sobe de novo DbAccess e AppServer de um ambiente, e espera a porta.

        Chamado depois de restaurar o banco, que encerra os processos daquele
        ambiente para liberar os arquivos da base.
        """
        db = appservers.garantir_dbaccess(
            *self._exes_do_ambiente(ambiente, ("dbaccess_exe", "dbaccess_params")))
        if not db.get("ok"):
            return {"ok": False, "erro": f"DbAccess: {db.get('erro')}"}

        exe, params = self._exes_do_ambiente(
            ambiente, ("appserver_exe", "appserver_params"))
        banco = self._estado.banco_por_nome(ambiente) or {}
        porta = str(banco.get("port") or "").strip()
        if porta and appservers.porta_responde(int(porta)):
            return {"ok": True, "reaproveitado": True}

        subida = appservers.subir(exe, params)
        if not subida.get("ok"):
            return subida
        if self._instancias.contem(ambiente):
            self._instancias.anotar_pid(ambiente, "appserver", subida["pid"])
        elif ambiente == getattr(self, "_ambiente_principal", ""):
            self._pid_principal = subida["pid"]
        return appservers.esperar_porta(int(porta)) if porta else \
            {"ok": False, "erro": "Ambiente sem porta configurada."}

    def _exes_do_ambiente(self, ambiente: str, campos: tuple) -> tuple:
        detalhes = self._estado.detalhes_por_nome(ambiente)
        banco = (detalhes.get("banco") or {}) if detalhes.get("ok") else {}
        return tuple(banco.get(c, "") for c in campos)

    def _config_por_instancia(self, base: dict, ambientes: list) -> dict:
        """Config do TIR de cada instância, com a Url da porta DELA.

        Sem isso os três navegadores abrem a mesma URL, e só a instância que
        ocupou aquela porta responde — foi o que aconteceu no primeiro teste
        real: três `config.json` apontando para 127.0.0.1:4321.
        """
        por_ambiente = {}
        for ambiente in ambientes:
            banco = self._estado.banco_por_nome(ambiente) or {}
            porta = str(banco.get("port") or "").strip()
            copia = dict(base)
            if porta:
                copia["Url"] = f"http://127.0.0.1:{porta}/"
            detalhes = self._estado.detalhes_por_nome(ambiente)
            if detalhes.get("ok") and detalhes.get("ambiente_ini"):
                # A seção do appserver.ini do clone pode diferir da do original.
                copia["Environment"] = detalhes["ambiente_ini"]
            por_ambiente[ambiente] = config_tir.normalizar(copia)
        return por_ambiente

    def _preparar_paralelos(self, nome: str) -> dict:
        """Sobe os ambientes da corrida e devolve a lista, o PAI incluído.

        O pai é a primeira instância. Ele já existe, já tem banco e já tem
        porta: deixá-lo parado enquanto três clones rodavam desperdiçava um
        ambiente pronto e vários GB de disco. Pedir 3 instâncias agora significa
        o pai mais 2 clones.

        Não gera ambiente aqui: gerar é ação explícita do usuário, pelo botão.
        Executar em paralelo sem clone nenhum é erro, não motivo para clonar
        gigabytes sem avisar.
        """
        registrados = self._instancias.listar(nome)
        if not registrados:
            return {"ok": False,
                    "erro": "Nenhum ambiente paralelo gerado. Use “Gerar "
                            "paralelos” antes de executar em modo paralelo."}

        isolado = self._prefs.dbaccess_por_instancia
        if isolado:
            # Cada instância sobe o próprio DbAccess, dentro de
            # `subir_para_instancias`. O `dbaccess.ini` do clone já traz o
            # alias do banco dele — não há o que consolidar.
            self._fila.put({
                "kind": "log", "level": "INFO",
                "text": "DbAccess isolado por instância (cada uma na própria "
                        "porta).",
            })
        else:
            # Parar o ambiente principal levou o DbAccess dele junto, e sem
            # DbAccess o AppServer sobe mas não serve ninguém. Os aliases dos
            # clones vão junto: um DbAccess só, conhecendo todos os bancos.
            db = self._garantir_dbaccess(
                nome, [i["ambiente"] for i in registrados])
            if not db.get("ok"):
                return {"ok": False, "erro": f"DbAccess: {db.get('erro')}"}

            # Diagnóstico explícito: alias faltando é a causa do login pendurado.
            principal = self._estado.detalhes_por_nome(nome)
            if principal.get("ok"):
                ini_db = dbaccess_ini.caminho_do_ini(
                    (principal.get("banco") or {}).get("dbaccess_exe", ""))
                bancos = [(self._estado.banco_por_nome(i["ambiente"]) or {}).get("nome_banco", "")
                          for i in registrados]
                ausentes = dbaccess_ini.faltando(ini_db, bancos)
                if ausentes:
                    return {"ok": False,
                            "erro": "O DbAccess não conhece o(s) banco(s): "
                                    + ", ".join(ausentes)
                                    + ". Sem isso o login trava na tela inicial."}

        self._fila.put({"kind": "log", "level": "INFO",
                        "text": "[FASE] Subindo as instâncias paralelas "
                                "(a porta leva algum tempo para responder)"})
        subida = appservers.subir_para_instancias(
            registrados, self._instancias, self._estado.detalhes_por_nome,
            dbaccess_por_instancia=isolado)
        for erro in subida.get("erros", []):
            self._fila.put({"kind": "log", "level": "ERROR",
                            "text": f"{erro['ambiente']}: {erro['erro']}"})
        if not subida.get("subidos"):
            return {"ok": False,
                    "erro": "Nenhuma instância paralela subiu. Veja o log."}

        ambientes = [s["ambiente"] for s in subida["subidos"]]

        # O pai entra como PRIMEIRA instância. Ele foi parado logo antes, junto
        # com o AppServer do Gerenciador, então precisa subir de novo — pelo
        # NebulaTIR desta vez, que é quem sabe o PID e consegue derrubá-lo
        # depois sem tocar nas outras instâncias.
        principal = self._subir_principal(nome)
        if principal.get("ok"):
            ambientes.insert(0, nome)
        else:
            # Sem o pai a corrida continua — com uma instância a menos, e
            # dizendo isso em voz alta. Derrubar tudo porque o ambiente
            # original não subiu seria pior: os clones estão prontos.
            self._fila.put({
                "kind": "log", "level": "WARNING",
                "text": f"ATENÇÃO: {nome} (ambiente principal) não subiu — "
                        f"{principal.get('erro', '')}. A corrida segue só com "
                        f"as instâncias paralelas.",
            })

        self._fila.put({"kind": "log", "level": "INFO",
                        "text": f"{len(ambientes)} instância(s) no ar: "
                                f"{', '.join(ambientes)}"})

        # Rodar com menos instâncias do que o registrado é resultado
        # silencioso: a corrida anda, o paralelismo não. Aconteceu — uma
        # instância ficou de fora, o motivo passou como uma linha no meio do
        # log e o usuário só percebeu porque o AppServer dela não se mexeu.
        ausentes = [i["ambiente"] for i in registrados
                    if i["ambiente"] not in ambientes]
        if ausentes:
            self._fila.put({
                "kind": "log", "level": "WARNING",
                "text": f"ATENÇÃO: {len(ausentes)} instância(s) fora da "
                        f"corrida — {', '.join(ausentes)}. O trabalho vai ser "
                        f"dividido entre as {len(ambientes)} que subiram.",
            })
        return {"ok": True, "ambientes": ambientes, "ausentes": ausentes}

    def abortar_tir(self) -> dict:
        """Para os testes de todos os ambientes."""
        if self._execucao is None:
            return {"ok": False, "erro": "Não há execução em andamento."}
        resultado = self._execucao.abortar()
        log.warning("[TIR] Execução abortada pelo usuário.")
        return resultado

    def estado_execucao(self) -> dict:
        if self._execucao is None:
            return {"ok": True, "ativa": False, "rotinas": []}
        return {"ok": True, **self._execucao.instantaneo()}

    def limpar_execucao(self) -> dict:
        """Descarta o resultado da corrida anterior.

        Não apaga arquivo nenhum: log e PNG continuam no disco, e a seleção de
        rotinas continua onde está. O que sai é a memória de que aquilo já
        rodou — é o que devolve os casos ao cinza e libera a mesma seleção para
        rodar de novo.

        **Corrida em andamento não é descartada.** Soltar a referência com
        threads vivas escreveria numa `Execucao` que a tela não mostra mais: o
        resultado sumiria da interface enquanto o TIR continua rodando.
        """
        if self._execucao is not None and self._execucao.ativa:
            return {"ok": False,
                    "erro": "Há uma execução em andamento. Aborte antes de limpar."}
        self._execucao = None
        return {"ok": True}

    # ─────────────────────────────────────────────────────────
    # AMBIENTES PARALELOS
    # ─────────────────────────────────────────────────────────

    def listar_paralelos(self, nome: str = "") -> dict:
        """Instâncias criadas por este programa, com o estado real dos PIDs."""
        self._completar_caminhos(nome or None)
        itens = self._instancias.listar(nome or None)
        return {"ok": True, "instancias": itens,
                "total": len(itens),
                "rodando": sum(1 for i in itens if i["estado"] == "rodando")}

    def inventario_instancias(self) -> dict:
        """O que existe no disco e o que disso ainda faz sentido.

        Sem filtro por ambiente: instância cujo pai sumiu do Gerenciador não
        aparece em lista nenhuma, e é justamente a que ocupa disco à toa.
        """
        self._completar_caminhos()
        instantaneo = self._estado.instantaneo
        online = bool(instantaneo.get("online"))
        ambientes = set((instantaneo.get("ambientes") or {}).keys())
        return inventario.levantar(
            registradas=self._instancias.listar(),
            ambientes=ambientes, online=online,
            base_path=instantaneo.get("base_path", ""))

    def medir_instancia(self, ambiente: str) -> dict:
        """Espaço de uma instância. Uma por chamada: percorrer um ambiente
        Protheus inteiro leva segundos, e a tela não pode ficar parada em todas
        de uma vez."""
        alvo = next((i for i in self.inventario_instancias()["instancias"]
                     if i["ambiente"] == ambiente), None)
        if alvo is None:
            return {"ok": False, "erro": f"Instância desconhecida: {ambiente}"}
        return {**inventario.medir(alvo["caminhos"]), "ambiente": ambiente}

    def _caminhos_da_instancia(self, ambiente: str, origem: str) -> dict:
        """Onde a instância deixa coisa no disco, do ponto de vista do Gerenciador.

        Só o que dá para afirmar: a pasta do ambiente vem do próprio Gerenciador
        (`pd_destino`), e o temp do pai é a pasta neutra do dia, de onde saem o
        MDF/LDF e o RPO zerado. Sem o Gerenciador no ar não há o que preencher —
        e chutar caminho é pior que deixar em branco na hora de apagar.
        """
        instantaneo = self._estado.instantaneo
        if not instantaneo.get("online"):
            return {}

        base_path = instantaneo.get("base_path", "")
        caminhos = {"base_path": base_path}
        pasta_destino = instantaneo.get("pasta_destino", "")
        if pasta_destino and origem:
            caminhos["temp_pai"] = str(Path(pasta_destino) / origem / "temp")

        banco = self._estado.banco_por_nome(ambiente) or {}
        pasta = (banco.get("pd_destino") or "").strip()
        if not pasta:
            appserver = (banco.get("appserver_exe") or "").strip()
            # `<pasta>\Protheus\bin\appserver\appserver.exe`: quatro níveis.
            # Caminho mais curto que isso não é ambiente instalado — melhor
            # ficar sem o campo do que apontar para a raiz de um disco.
            partes = Path(appserver).parents if appserver else []
            if len(partes) > 3:
                pasta = str(partes[3])
        if pasta:
            caminhos["pasta"] = pasta

        workspace = self._workspace_do_banco(pasta, base_path, pasta_destino,
                                             ambiente)
        if workspace:
            caminhos["workspace_banco"] = workspace
        return caminhos

    @staticmethod
    def _workspace_do_banco(pasta: str, base_path: str, pasta_destino: str,
                            ambiente: str) -> str:
        """Onde ficam o MDF/LDF anexados daquela instância.

        Dois layouts convivem. As instâncias novas nascem em
        `<base>\\NebulaInstancia\\<amb>` e levam o banco em `banco\\` dentro da
        própria pasta — apagar a pasta leva o banco junto. As antigas deixaram
        o banco em `<pasta_destino>\\<amb>\\<data>`, e essa raiz é guardada
        como está: a pasta do dia é achada na hora de remover.
        """
        if pasta and base_path:
            raiz_nova = Path(base_path) / instancias_mod.PASTA_INSTANCIAS
            try:
                Path(pasta).relative_to(raiz_nova)
                return str(Path(pasta) / "banco")
            except ValueError:
                pass
        if pasta_destino and ambiente:
            return str(Path(pasta_destino) / ambiente)
        return ""

    def _completar_caminhos(self, nome: str | None = None) -> None:
        """Preenche o que falta nas instâncias já registradas.

        As criadas antes desta versão não têm caminho nenhum: o registro vivia
        ao lado do executável e só sabia nome, banco e portas. Sem isso, limpar
        uma instância cujo ambiente sumiu do Gerenciador viraria adivinhação.
        """
        for item in self._instancias.listar(nome):
            faltando = [c for c in ("pasta", "temp_pai", "base_path")
                        if not item.get(c)]
            if not faltando:
                continue
            caminhos = self._caminhos_da_instancia(item["ambiente"],
                                                   item.get("origem", ""))
            if caminhos:
                self._instancias.anotar_caminhos(item["ambiente"], caminhos)

    def gerar_paralelos(self, nome: str) -> dict:
        """Clona os ambientes paralelos e trata as portas.

        Precisa existir antes de executar em paralelo — sem ambiente próprio,
        os slots dividiriam o mesmo banco, que é o que não pode.
        """
        if not self._estado.online:
            return {"ok": False, "erro": MOTIVO_OFFLINE}
        if not self._importados.contem(nome):
            return {"ok": False, "erro": "Ambiente não está importado."}
        if self._estado.instantaneo["ocupado"]:
            return {"ok": False, "erro": "O Gerenciador está ocupado."}

        banco = self._estado.banco_por_nome(nome) or {}
        # O ambiente PAI é uma das instâncias — ele já existe, já tem banco e
        # já tem porta. Pedir 3 instâncias significa clonar 2.
        #
        # Antes clonava 3 e deixava o pai parado, ocioso: uma cópia de vários
        # GB a mais no disco, e um ambiente pronto sem uso.
        clones = max(0, self._prefs.max_instancias - 1)
        plano = self.plano_de_portas(nome)
        if not plano.get("ok"):
            return plano
        if not clones:
            return {"ok": True, "criados": [], "reaproveitados": [],
                    "mensagem": "Uma instância só: o próprio ambiente já "
                                "atende, nada a clonar.",
                    "quantidade_pedida": self._prefs.max_instancias}

        resultado = paralelos.gerar(
            origem=nome, banco_origem=banco.get("nome_banco", nome),
            quantidade=clones, estado_gerenciador=self._estado,
            registro=self._instancias, plano_portas=plano,
            existentes=self._instancias.nomes(nome),
            caminhos_de=self._caminhos_da_instancia)
        return {**resultado, "quantidade_pedida": self._prefs.max_instancias,
                "clones_pedidos": clones}

    def subir_paralelos(self, ambientes: list) -> dict:
        """Sobe DbAccess e AppServer das instâncias pedidas, e espera as portas.

        Existe para carregar os ambientes sem precisar disparar uma corrida —
        útil logo depois de gerar os paralelos, ou para inspecionar um deles
        no navegador.
        """
        if not self._estado.online:
            return {"ok": False, "erro": MOTIVO_OFFLINE}
        alvos = [a for a in (ambientes or []) if self._instancias.contem(a)]
        if not alvos:
            return {"ok": False, "erro": "Nenhuma instância paralela selecionada."}

        isolado = self._prefs.dbaccess_por_instancia
        if not isolado:
            db = self._garantir_dbaccess(self._ambiente_de_origem(alvos[0]), alvos)
            if not db.get("ok"):
                return {"ok": False, "erro": f"DbAccess: {db.get('erro')}"}

        itens = [i for i in self._instancias.listar() if i["ambiente"] in alvos]
        self._fila.put({"kind": "log", "level": "INFO",
                        "text": f"[FASE] Subindo {len(itens)} ambiente(s) "
                                f"(a porta leva algum tempo para responder)"})
        subida = appservers.subir_para_instancias(
            itens, self._instancias, self._estado.detalhes_por_nome,
            dbaccess_por_instancia=isolado)
        for erro in subida.get("erros", []):
            self._fila.put({"kind": "log", "level": "ERROR",
                            "text": f"{erro['ambiente']}: {erro['erro']}"})
        return subida

    def _ambiente_de_origem(self, paralelo: str) -> str:
        item = self._instancias.por_nome(paralelo) or {}
        return item.get("origem", "")

    def parar_paralelos(self, ambientes: list) -> dict:
        """Para só as instâncias marcadas — por PID, nunca por nome de imagem.

        `taskkill /IM appserver.exe` derrubaria também o ambiente que o
        usuário subiu à mão para outra coisa.

        **Parando todas, o DbAccess vai junto.** Ele é um só para todo mundo,
        então enquanto sobrar uma instância de pé ele precisa continuar. Sem
        nenhuma, ele fica órfão: some da tela, segue segurando arquivo e
        derruba a exclusão do ambiente depois, sem dizer por quê.
        """
        alvos = [a for a in (ambientes or []) if self._instancias.contem(a)]
        if not alvos:
            return {"ok": False, "erro": "Nenhuma instância paralela selecionada."}

        resultado = self._instancias.parar(alvos)

        restantes = [a for a in self._instancias.nomes() if a not in alvos]
        if not restantes:
            parou = appservers.parar_dbaccess()
            resultado["dbaccess_parado"] = parou
            self._fila.put({
                "kind": "log", "level": "INFO" if parou else "WARNING",
                "text": "DbAccess encerrado — nenhuma instância restou."
                        if parou else
                        "Não consegui encerrar o DbAccess; verifique à mão.",
            })
        return resultado

    def excluir_paralelos(self, ambientes: list) -> dict:
        """Remove os ambientes paralelos de verdade, pelo Gerenciador.

        Roda em thread e devolve na hora: cada remoção leva minutos (detach do
        banco, apagar pastas de GB, DSN), e a UI precisa mostrar qual está
        saindo em vez de congelar e despejar tudo no fim.

        Nunca toca no ambiente principal: só entra o que está no registro
        deste programa.
        """
        if not self._estado.online:
            return {"ok": False, "erro": MOTIVO_OFFLINE}
        if self._exclusao.get("ativa"):
            return {"ok": False, "erro": "Já há uma exclusão em andamento."}
        alvos = [a for a in (ambientes or []) if self._instancias.contem(a)]
        if not alvos:
            return {"ok": False, "erro": "Nenhuma instância paralela selecionada."}

        self._exclusao = {"ativa": True, "atual": "", "feitos": 0,
                          "total": len(alvos), "removidos": [], "erros": []}
        threading.Thread(target=self._rodar_exclusao, args=(alvos,),
                         name="nebula-exclusao", daemon=True).start()
        return {"ok": True, "iniciado": True, "total": len(alvos)}

    def estado_exclusao(self) -> dict:
        return {"ok": True, **self._exclusao}

    def _rodar_exclusao(self, alvos: list) -> None:
        self._instancias.parar(alvos)     # matar antes de apagar
        removidos, erros = [], []
        for ambiente in alvos:
            self._exclusao["atual"] = ambiente
            if self._estado.indice_por_nome(ambiente) is None:
                # Já não existe no Gerenciador: limpar o registro é o certo.
                self._concluir_um(ambiente, removidos)
                continue
            self._fila.put({"kind": "log", "level": "INFO",
                            "text": f"[FASE] Removendo {ambiente}"})
            resposta = self._estado.remover_ambiente(ambiente)
            if not resposta.get("ok"):
                erros.append({"ambiente": ambiente,
                              "erro": resposta.get("erro", "Falha na remoção.")})
                continue

            # A remoção roda em thread no Gerenciador e devolve na hora. Sem
            # esperar, o próximo pedido volta "Já existe uma operação em
            # andamento" — o mesmo defeito que travou a clonagem.
            espera = self._estado.esperar_ocioso()
            if not espera.get("ok"):
                erros.append({"ambiente": ambiente, "erro": espera.get("erro", "")})
                continue

            self._estado.atualizar()
            if self._estado.banco_por_nome(ambiente) is not None:
                erros.append({"ambiente": ambiente,
                              "erro": "A remoção terminou, mas o ambiente "
                                      "continua no Gerenciador."})
                continue

            preparacao.limpar_ambiente(ambiente)   # pasta tests\<ambiente>
            log.warning("[PARALELO] %s removido por completo.", ambiente)
            self._concluir_um(ambiente, removidos)

        self._exclusao.update({"ativa": False, "atual": "",
                               "removidos": removidos, "erros": erros})
        self._fila.put({
            "kind": "log",
            "level": "WARNING" if erros else "INFO",
            "text": f"Exclusão concluída: {len(removidos)} removido(s)"
                    + (f", {len(erros)} com erro" if erros else "") + ".",
        })

    def _concluir_um(self, ambiente: str, removidos: list) -> None:
        """Tira do registro assim que termina, para a lista encolher na tela
        no mesmo ritmo em que o Gerenciador apaga — e não tudo de uma vez."""
        removidos.append(ambiente)
        self._instancias.remover([ambiente])
        self._exclusao["feitos"] = len(removidos)
        self._exclusao["removidos"] = list(removidos)

    # ─────────────────────────────────────────────────────────
    # LIMPEZA DO QUE O GERENCIADOR NÃO ALCANÇA
    # ─────────────────────────────────────────────────────────

    def limpar_instancias(self, ambientes: list) -> dict:
        """Apaga de vez as instâncias que sobraram no disco.

        Só entra o que o inventário marcou como removível — órfã, fantasma ou
        pasta sem registro — e só com o Gerenciador no ar: cada instalação dele
        tem o próprio cadastro, e classificar sem o canal é o caminho para
        apagar o que estava em uso.

        Instância **ainda cadastrada** é removida pelo Gerenciador, não aqui:
        ele faz a remoção com rollback e continua sendo a única verdade sobre
        ambiente. A limpeza própria é o caminho de quem já saiu de lá.
        """
        if not self._estado.online:
            return {"ok": False, "erro": MOTIVO_OFFLINE}
        if self._exclusao.get("ativa") or self._limpeza.get("ativa"):
            return {"ok": False, "erro": "Já há uma remoção em andamento."}

        pedidos = set(ambientes or [])
        candidatas = [i for i in self.inventario_instancias()["instancias"]
                      if i["ambiente"] in pedidos]
        recusadas = [i["ambiente"] for i in candidatas if not i["removivel"]]
        alvos = [i for i in candidatas if i["removivel"]]
        if not alvos:
            return {"ok": False,
                    "erro": "Nada a limpar: nenhuma das selecionadas está "
                            "órfã ou sem cadastro no disco."}

        self._limpeza = {"ativa": True, "atual": "", "feitos": 0,
                         "total": len(alvos), "removidos": [], "erros": [],
                         "recusadas": recusadas}
        threading.Thread(target=self._rodar_limpeza, args=(alvos,),
                         name="nebula-limpeza", daemon=True).start()
        return {"ok": True, "iniciado": True, "total": len(alvos),
                "recusadas": recusadas}

    def estado_limpeza(self) -> dict:
        return {"ok": True, **self._limpeza}

    def _dados_da_conexao(self) -> dict:
        """Servidor e driver da conexão ativa do Gerenciador.

        A senha não vem pelo canal, por decisão — ela é a constante de toda
        base (`services/limpeza.py`), a mesma que o clone usa para o DSN.
        """
        instantaneo = self._estado.instantaneo
        ativa = instantaneo.get("conexao_ativa", "")
        conexoes = instantaneo.get("conexoes") or []
        escolhida = next((c for c in conexoes if c.get("nome") == ativa),
                         conexoes[0] if conexoes else {})
        return {"servidor": escolhida.get("sql_server", ""),
                "driver": escolhida.get("driver", "")}

    def _rodar_limpeza(self, alvos: list) -> None:
        conexao = self._dados_da_conexao()
        removidos, erros = [], []
        for item in alvos:
            ambiente = item["ambiente"]
            self._limpeza["atual"] = ambiente
            self._fila.put({"kind": "log", "level": "WARNING",
                            "text": f"[FASE] Limpando {ambiente}"})

            resultado = limpeza.remover(item, servidor=conexao["servidor"],
                                        driver=conexao["driver"])
            for passo in resultado["passos"]:
                if not passo.get("ok"):
                    self._fila.put({"kind": "log", "level": "ERROR",
                                    "text": f"{ambiente} — {passo['passo']}: "
                                            f"{passo.get('erro', '')}"})
            if resultado["erros"]:
                erros.append({"ambiente": ambiente,
                              "erro": "; ".join(resultado["erros"])})
            else:
                removidos.append(ambiente)

            # O registro sai mesmo com passo pendente: o que ficou para trás
            # está no log, e manter a linha viva faria a tela oferecer de novo
            # uma limpeza que já apagou o principal.
            self._instancias.remover([ambiente])
            preparacao.limpar_ambiente(ambiente)   # pasta tests\<ambiente>
            self._limpeza["feitos"] = len(removidos) + len(erros)
            self._limpeza["removidos"] = list(removidos)
            self._limpeza["erros"] = list(erros)

        self._limpeza.update({"ativa": False, "atual": ""})
        self._fila.put({
            "kind": "log",
            "level": "WARNING" if erros else "INFO",
            "text": f"Limpeza concluída: {len(removidos)} instância(s) "
                    f"removida(s) do disco"
                    + (f", {len(erros)} com pendência" if erros else "") + ".",
        })

    # ─────────────────────────────────────────────────────────
    # BANCO DE DADOS
    # ─────────────────────────────────────────────────────────

    def restaurar_banco(self, ambientes: list) -> dict:
        """Repõe a base congelada dos ambientes pedidos, um de cada vez.

        Delega ao `somente_banco` do Gerenciador, que sabe achar a base em
        `<pasta_destino>\\<ambiente>\\temp\\<data>` e faz o attach com o nome
        de banco **daquele** ambiente — o paralelo restaura o banco dele, não
        o do original.

        **Derruba todos os AppServer**: o pipeline encerra `appserver.exe` por
        nome de imagem para liberar os arquivos da base. Por isso a corrida
        precisa estar parada.
        """
        if not self._estado.online:
            return {"ok": False, "erro": MOTIVO_OFFLINE}
        if self._execucao is not None and self._execucao.ativa:
            return {"ok": False,
                    "erro": "Há uma execução em andamento. Restaurar o banco "
                            "encerra todos os AppServer — aborte antes."}

        alvos = [a for a in (ambientes or []) if self._estado.banco_por_nome(a)]
        if not alvos:
            return {"ok": False, "erro": "Nenhum ambiente válido selecionado."}

        restaurados, erros = [], []
        for ambiente in alvos:
            banco = (self._estado.banco_por_nome(ambiente) or {}).get("nome_banco", "")
            self._fila.put({"kind": "log", "level": "INFO",
                            "text": f"[FASE] Restaurando o banco de {ambiente} "
                                    f"({banco})"})
            pedido = self._estado.restaurar_banco(ambiente)
            if not pedido.get("ok"):
                erros.append({"ambiente": ambiente, "erro": pedido.get("erro", "")})
                continue
            espera = self._estado.esperar_ocioso()
            if espera.get("ok"):
                restaurados.append(ambiente)
            else:
                erros.append({"ambiente": ambiente, "erro": espera.get("erro", "")})

        # Os AppServer morreram junto: o registro precisa refletir isso.
        for ambiente in alvos:
            if self._instancias.contem(ambiente):
                self._instancias.parar([ambiente])
        return {"ok": not erros, "restaurados": restaurados, "erros": erros}

    # ─────────────────────────────────────────────────────────
    # RPO
    # ─────────────────────────────────────────────────────────

    def estado_rpo(self, nome: str) -> dict:
        return {"ok": True, "guardado": paralelos.tem_rpo_guardado(nome)}

    def guardar_rpo(self, nome: str) -> dict:
        """Copia o RPO atual, com pacote e fonte, para poder repor depois."""
        detalhes = self._estado.detalhes_por_nome(nome)
        if not detalhes.get("ok"):
            return detalhes
        return paralelos.guardar_rpo(
            nome, (detalhes.get("banco") or {}).get("rpo_destino", ""))

    def restaurar_rpo_ambiente(self, nome: str) -> dict:
        """Repõe o RPO como estava — com pacote e fonte aplicados."""
        detalhes = self._estado.detalhes_por_nome(nome)
        if not detalhes.get("ok"):
            return detalhes
        return paralelos.restaurar_rpo_do_ambiente(
            nome, (detalhes.get("banco") or {}).get("rpo_destino", ""))

    def restaurar_rpo_zerado(self, nome: str) -> dict:
        """Repõe o RPO baixado, sem pacote nem fonte — para comparação.

        Tenta primeiro copiar do `temp` do Gerenciador, que é onde o download
        já deixou o arquivo: é instantâneo e não ocupa o Gerenciador inteiro.
        Só cai no `somente_rpo` quando não há nada em disco.
        """
        if not self._estado.online:
            return {"ok": False, "erro": MOTIVO_OFFLINE}

        detalhes = self._estado.detalhes_por_nome(nome)
        banco = (detalhes.get("banco") or {}) if detalhes.get("ok") else {}
        local = paralelos.restaurar_rpo_zerado_local(
            self._estado.pasta_destino, nome, banco.get("rpo_destino", ""))
        if local.get("ok"):
            return {**local, "origem_da_copia": "temp"}

        log.info("[RPO] Sem RPO em disco (%s); pedindo o download ao "
                 "Gerenciador.", local.get("erro", ""))
        resposta = self._estado.restaurar_rpo(nome)
        return {**resposta, "origem_da_copia": "gerenciador"}

    # ─────────────────────────────────────────────────────────
    # PORTAS (execução paralela)
    # ─────────────────────────────────────────────────────────

    def plano_de_portas(self, nome: str = "") -> dict:
        """Portas que cada instância usaria, com o modo e o limite atuais.

        Em sequencial é uma instância só, que fica com as portas originais —
        nada muda no que já funciona. Em paralelo, a alocação soma 1 e pula o
        que estiver em uso, sem perguntar nada.
        """
        base = None
        if nome and self._importados.contem(nome):
            banco = self._estado.banco_por_nome(nome) or {}
            try:
                base = int(str(banco.get("port") or "").strip())
            except ValueError:
                base = None

        slots = self._prefs.max_instancias if self._prefs.paralelo else 1
        plano = portas.alocar(
            slots, base_webapp=base, checar=self._checar_porta,
            dbaccess_por_instancia=self._prefs.dbaccess_por_instancia)
        if plano.get("ok"):
            plano = {**plano, "instancias": self._marcar_criadas(
                plano["instancias"], nome)}
        return {**plano, "modo": self._prefs.modo, "slots": slots,
                "rotulos": portas.ROTULOS}

    def _marcar_criadas(self, instancias: list, nome: str) -> list:
        """Diz quais slots do plano já viraram instância no disco.

        A tela destaca em amarelo a porta que saiu do padrão, para avisar o que
        o `appserver.ini` daquele slot vai precisar sobrescrever. Depois de
        criada, o aviso perdeu a função: a porta está gravada e o destaque só
        chamava atenção para uma decisão que já foi tomada.
        """
        registradas = {i.get("slot"): i
                       for i in self._instancias.listar(nome or None)}
        marcadas = []
        for item in instancias:
            registro = registradas.get(item.get("slot")) or {}
            marcadas.append({**item,
                             "criada": bool(registro),
                             "ambiente": registro.get("ambiente", "")})
        return marcadas

    # ─────────────────────────────────────────────────────────
    # CONFIGURAÇÃO DO TIR (config.json)
    # ─────────────────────────────────────────────────────────

    def _config_do_ambiente(self, nome: str) -> dict:
        """Config do TIR já normalizada, com o idioma acertado pelo país.

        Ambientes importados antes desta correção guardam `pt-BR`, que era o
        padrão aplicado sem olhar a localização. Num ambiente MEX isso fazia o
        Protheus subir em português enquanto o testcase procurava rótulo em
        espanhol — toda a suite falhava em `SetValue`, e o mesmo teste passava
        quando rodado fora do NebulaTIR, com o config da pessoa.

        Só ajusta enquanto o usuário não tiver escolhido o idioma à mão.
        """
        # `POUILogin` é travado em `normalizar`, então ambiente gravado com
        # ele desligado volta a ligar sozinho na leitura.
        config = config_tir.normalizar(self._importados.configuracao(nome))
        if self._importados.idioma_manual(nome):
            return config
        esperado = config_tir.idioma_do_pais(self._estado.pais_do_ambiente(nome))
        if esperado and config.get("Language") != esperado:
            config["Language"] = esperado
            self._importados.corrigir_idioma(nome, esperado)
        return config

    def obter_configuracao(self, nome: str) -> dict:
        """Configuração do ambiente + o esquema que a UI usa para se montar."""
        if not self._importados.contem(nome):
            return {"ok": False, "erro": "Ambiente não está importado."}

        instalados = navegadores.listar()
        config = self._config_do_ambiente(nome)

        # Reconciliação com o Gerenciador: se a porta ou a seção do
        # appserver.ini mudaram lá, a configuração guardada aqui está velha. A
        # UI mostra a divergência em vez de sobrescrever o que o usuário editou.
        divergencias = []
        if self._estado.online:
            detalhes = self._estado.detalhes_por_nome(nome)
            if detalhes.get("ok"):
                atual = {
                    "Url": detalhes.get("url_ambiente", ""),
                    "Environment": detalhes.get("ambiente_ini", ""),
                }
                for chave, valor in atual.items():
                    if valor and config.get(chave) != valor:
                        divergencias.append({"chave": chave,
                                             "guardado": config.get(chave, ""),
                                             "gerenciador": valor})

        campos = []
        for campo in config_tir.CAMPOS:
            campo = dict(campo)
            if campo["chave"] == "Browser":
                campo["opcoes"] = instalados
            campos.append(campo)

        return {"ok": True, "nome": nome, "config": config, "campos": campos,
                "divergencias": divergencias,
                # Compartilha a tela da configuração, mas NÃO vai para o
                # config.json: a pasta de fontes é da máquina, não do TIR.
                "fontes": self._prefs.fontes}

    def salvar_configuracao(self, nome: str, config: dict) -> dict:
        """Normaliza (aplicando travas) e persiste. A UI não é fonte de verdade."""
        if not self._importados.contem(nome):
            return {"ok": False, "erro": "Ambiente não está importado."}
        base = self._config_do_ambiente(nome)
        novo = config_tir.normalizar(config, base=base)
        erro = config_tir.validar(novo)
        if erro:
            return {"ok": False, "erro": erro}
        # Salvar pela tela congela o idioma: a correção automática pelo país
        # não deve desfazer uma escolha deliberada.
        resultado = self._importados.salvar_configuracao(
            nome, novo, idioma_manual=True)
        if resultado.get("ok"):
            resultado["config"] = novo
        return resultado

    def escolher_pasta(self, inicial: str = "") -> dict:
        """Diálogo nativo de pasta, para o campo `LogFolder`."""
        if self._window is None:
            return {"ok": False, "erro": "Janela indisponível."}
        import webview
        escolhido = self._window.create_file_dialog(
            webview.FOLDER_DIALOG, directory=inicial or "")
        if not escolhido:
            return {"ok": False, "cancelado": True}
        caminho = escolhido[0] if isinstance(escolhido, (list, tuple)) else escolhido
        return {"ok": True, "caminho": str(caminho)}

    def listar_navegadores(self) -> dict:
        return {"ok": True, "navegadores": navegadores.listar()}

    # ─────────────────────────────────────────────────────────
    # FONTES DOS TESTES
    # ─────────────────────────────────────────────────────────
    # Divide a tela com a configuração do TIR, mas é preferência global e não
    # entra no `config.json` — o TIR não tem chave para isso, e a pasta é da
    # máquina, não do ambiente.

    def get_fontes(self) -> dict:
        return {"ok": True, "fontes": self._prefs.fontes}

    def salvar_raiz_testes(self, caminho: str) -> dict:
        """Aponta a pasta à mão. Vazio volta para a detecção automática."""
        caminho = (caminho or "").strip()
        if caminho and not Path(caminho).is_dir():
            return {"ok": False, "erro": f"Pasta não encontrada: {caminho}"}
        self._prefs.salvar({"raiz_testes": caminho})
        return {"ok": True, "fontes": self._prefs.fontes}

    def detectar_raiz_testes(self) -> dict:
        """Refaz a busca — a pasta pode ter aparecido depois da abertura."""
        achada = self._prefs.redetectar()
        if achada is None:
            return {"ok": False,
                    "erro": "Não encontrei a pasta “Testes/Automação Protheus” "
                            "nos lugares prováveis. Aponte à mão.",
                    "fontes": self._prefs.fontes}
        return {"ok": True, "fontes": self._prefs.fontes}

    # ─────────────────────────────────────────────────────────
    # UTILITÁRIOS
    # ─────────────────────────────────────────────────────────

    def abrir_arquivo(self, caminho: str) -> dict:
        """Abre o relatório no visualizador padrão do Windows.

        Restrito à pasta de execução do programa: a UI só pede caminhos que
        ela mesma recebeu, mas o método fica exposto ao JS e não custa fechar.
        """
        alvo = Path(caminho or "")
        try:
            dentro = alvo.resolve().is_relative_to(preparacao.raiz_execucao().resolve())
        except (OSError, ValueError):
            dentro = False
        if not dentro or not alvo.is_file():
            return {"ok": False, "erro": "Arquivo fora da pasta de execução."}
        os.startfile(str(alvo))  # noqa: S606 — caminho validado acima
        return {"ok": True}

    def abrir_url(self, url: str) -> dict:
        if not (url or "").startswith(("http://", "https://")):
            return {"ok": False, "erro": "URL inválida."}
        webbrowser.open(url)
        return {"ok": True}
