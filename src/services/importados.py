"""Ambientes importados para o NebulaTIR.

Guarda **referência por nome**, nunca cópia dos dados do ambiente. Porta,
caminhos e status são sempre lidos do Gerenciador no momento do uso; se o
usuário editar a porta lá, o NebulaTIR não fica com dado velho.

A exceção é a **configuração do TIR** (`config`), que é dado do NebulaTIR e não
espelho do Gerenciador: `Url` e `Environment` nascem da importação mas viram
propriedade daqui, porque o usuário pode editá-los sem que isso signifique
mudar nada no ambiente real.

O arquivo é `config/ambientes_importados.json`, irmão do executável — mesmo
lugar que o Gerenciador usa para a config dele.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).parent
else:
    BASE_DIR = Path(__file__).resolve().parent.parent.parent  # <repo>/

CONFIG_DIR = BASE_DIR / "config"
ARQUIVO = CONFIG_DIR / "ambientes_importados.json"


ORIGEM_FONTES = "fontes"
ORIGEM_LOCAL = "local"
ORIGENS = (ORIGEM_FONTES, ORIGEM_LOCAL)


def _origem(valor) -> str:
    return valor if valor in ORIGENS else ORIGEM_FONTES


def _locais(valor) -> dict:
    """`{"pasta": str, "selecao": [str]}`, tolerando arquivo antigo ou sujo."""
    if not isinstance(valor, dict):
        return {"pasta": "", "selecao": []}
    return {"pasta": str(valor.get("pasta") or "").strip(),
            "selecao": [str(r) for r in (valor.get("selecao") or []) if r]}


class RepositorioImportados:
    """Lista ordenada de nomes de ambiente, persistida em JSON."""

    def __init__(self, arquivo: Path | None = None):
        self._arquivo = arquivo or ARQUIVO
        self._itens: list[dict] = self._carregar()

    # ── persistência ──
    def _carregar(self) -> list[dict]:
        try:
            dados = json.loads(self._arquivo.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        itens = dados.get("ambientes") if isinstance(dados, dict) else dados
        if not isinstance(itens, list):
            return []
        # Tolera arquivo escrito por versão anterior (lista de strings).
        normalizados = []
        for item in itens:
            if isinstance(item, str):
                normalizados.append({"nome": item, "importado_em": "", "config": {}})
            elif isinstance(item, dict) and item.get("nome"):
                normalizados.append({
                    "nome": item["nome"],
                    "importado_em": item.get("importado_em", ""),
                    "config": item.get("config") or {},
                    # Rotinas escolhidas para executar (só os nomes; casos e
                    # caminhos são relidos do disco, que pode ter mudado).
                    "selecao": list(item.get("selecao") or []),
                    # Marca que o idioma foi escolhido à mão na tela de
                    # configuração. Enquanto for falso, o NebulaTIR pode
                    # corrigi-lo a partir do país do ambiente — o padrão
                    # `pt-BR` foi aplicado cego na importação e derrubava
                    # toda suite de localização hispânica.
                    "idioma_manual": bool(item.get("idioma_manual")),
                    # Aba "Casos de teste": de onde vêm os testes (`fontes` é
                    # o catálogo por país; `local` é uma pasta escolhida) e o
                    # que foi escolhido na pasta local. Seleções separadas:
                    # trocar de origem não pode apagar a outra.
                    "origem_testes": _origem(item.get("origem_testes")),
                    "testes_locais": _locais(item.get("testes_locais")),
                })
        return normalizados

    def _salvar(self) -> None:
        self._arquivo.parent.mkdir(parents=True, exist_ok=True)
        self._arquivo.write_text(
            json.dumps({"ambientes": self._itens}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    # ── leitura ──
    @property
    def nomes(self) -> list[str]:
        return [i["nome"] for i in self._itens]

    def contem(self, nome: str) -> bool:
        return nome in self.nomes

    def listar(self) -> list[dict]:
        return [dict(i) for i in self._itens]

    def configuracao(self, nome: str) -> dict | None:
        for item in self._itens:
            if item["nome"] == nome:
                return dict(item.get("config") or {})
        return None

    # ── escrita ──
    def importar(self, nome: str, config: dict | None = None) -> dict:
        nome = (nome or "").strip()
        if not nome:
            return {"ok": False, "erro": "Nome do ambiente vazio."}
        if self.contem(nome):
            return {"ok": False, "erro": f"'{nome}' já foi importado."}
        self._itens.append({
            "nome": nome,
            "importado_em": datetime.now().isoformat(timespec="seconds"),
            "config": dict(config or {}),
            "selecao": [],
            "idioma_manual": False,
            "origem_testes": ORIGEM_FONTES,
            "testes_locais": _locais(None),
        })
        self._salvar()
        log.info("[IMPORT] Ambiente '%s' importado para o NebulaTIR.", nome)
        return {"ok": True, "nome": nome}

    def selecao(self, nome: str) -> list[str]:
        for item in self._itens:
            if item["nome"] == nome:
                return list(item.get("selecao") or [])
        return []

    def salvar_selecao(self, nome: str, rotinas: list[str]) -> dict:
        for item in self._itens:
            if item["nome"] == nome:
                item["selecao"] = list(dict.fromkeys(rotinas or []))
                self._salvar()
                return {"ok": True, "selecao": item["selecao"]}
        return {"ok": False, "erro": "Ambiente não está importado."}

    # ── origem dos testes e pasta local ──
    def origem_testes(self, nome: str) -> str:
        for item in self._itens:
            if item["nome"] == nome:
                return _origem(item.get("origem_testes"))
        return ORIGEM_FONTES

    def salvar_origem_testes(self, nome: str, origem: str) -> dict:
        for item in self._itens:
            if item["nome"] == nome:
                item["origem_testes"] = _origem(origem)
                self._salvar()
                return {"ok": True, "origem": item["origem_testes"]}
        return {"ok": False, "erro": "Ambiente não está importado."}

    def testes_locais(self, nome: str) -> dict:
        for item in self._itens:
            if item["nome"] == nome:
                return _locais(item.get("testes_locais"))
        return _locais(None)

    def salvar_pasta_local(self, nome: str, pasta: str) -> dict:
        """Troca a pasta e zera a seleção: os testes de lá são outros."""
        for item in self._itens:
            if item["nome"] == nome:
                atual = _locais(item.get("testes_locais"))
                if atual["pasta"] != (pasta or "").strip():
                    atual = {"pasta": (pasta or "").strip(), "selecao": []}
                item["testes_locais"] = atual
                self._salvar()
                return {"ok": True, **atual}
        return {"ok": False, "erro": "Ambiente não está importado."}

    def salvar_selecao_local(self, nome: str, rotinas: list[str]) -> dict:
        for item in self._itens:
            if item["nome"] == nome:
                atual = _locais(item.get("testes_locais"))
                atual["selecao"] = list(dict.fromkeys(rotinas or []))
                item["testes_locais"] = atual
                self._salvar()
                return {"ok": True, "selecao": atual["selecao"]}
        return {"ok": False, "erro": "Ambiente não está importado."}

    def idioma_manual(self, nome: str) -> bool:
        for item in self._itens:
            if item["nome"] == nome:
                return bool(item.get("idioma_manual"))
        return False

    def corrigir_idioma(self, nome: str, idioma: str) -> None:
        """Ajusta o idioma herdado do padrão, sem marcá-lo como escolha do usuário."""
        for item in self._itens:
            if item["nome"] == nome and not item.get("idioma_manual"):
                item.setdefault("config", {})["Language"] = idioma
                self._salvar()
                log.info("[CONFIG] Idioma de '%s' ajustado para %s pelo país "
                         "do ambiente.", nome, idioma)
                return

    def salvar_configuracao(self, nome: str, config: dict,
                            idioma_manual: bool = False) -> dict:
        for item in self._itens:
            if item["nome"] == nome:
                # Salvar pela tela é escolha consciente: a partir daqui o
                # idioma não é mais ajustado sozinho.
                if idioma_manual:
                    item["idioma_manual"] = True
                item["config"] = dict(config)
                self._salvar()
                log.info("[CONFIG] Configuração do TIR salva para '%s'.", nome)
                return {"ok": True, "nome": nome}
        return {"ok": False, "erro": "Ambiente não está importado."}

    def remover(self, nome: str) -> dict:
        """Remove só a referência local — o ambiente real não é tocado."""
        if not self.contem(nome):
            return {"ok": False, "erro": "Ambiente não está importado."}
        self._itens = [i for i in self._itens if i["nome"] != nome]
        self._salvar()
        log.info("[IMPORT] Ambiente '%s' removido do NebulaTIR "
                 "(o ambiente no Gerenciador continua intacto).", nome)
        return {"ok": True, "nome": nome}
