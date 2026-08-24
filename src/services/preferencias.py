"""Preferências globais do NebulaTIR.

Globais de propósito, não por ambiente: o teto de instâncias paralelas é da
**máquina** — CPU, memória e portas livres. Guardado por ambiente, três
ambientes com limite 3 poderiam disparar nove processos, que é justamente o
que o limite existe para impedir.

Sem teto máximo: máquina mais potente aguenta mais, e quem sabe disso é o
usuário. O piso é 1, porque zero instância não executa nada.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

from services.catalogo_testes import RAIZ_PADRAO, descobrir_raiz

log = logging.getLogger(__name__)

if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).parent
else:
    BASE_DIR = Path(__file__).resolve().parent.parent.parent

ARQUIVO = BASE_DIR / "config" / "preferencias.json"

MODOS = ("sequencial", "paralelo")

PADRAO = {
    # Vazio = detectar sozinho. Preenchido pelo usuário vence a detecção: os
    # fontes costumam cair na pasta de quem baixou, mas nada impede de moverem.
    "raiz_testes": "",
    "max_instancias": 3,
    "modo": "sequencial",
    # Painel de log fixado: governa só o fechamento automático ao clicar fora.
    # O painel nasce contraído em toda abertura, fixado ou não.
    "log_fixado": False,
    # Distribuir os casos de uma rotina entre as instâncias, em vez de mandar
    # o suite inteiro para uma só. Casos com dependência entre si continuam
    # juntos e na mesma instância — ver `services/analise_casos.py`.
    "dividir_casos": False,
    # Um DbAccess por instância paralela, cada um na sua porta, em vez de um
    # processo atendendo todas.
    #
    # Hipótese em teste: em corrida paralela as instâncias travavam sem causa
    # visível em nenhum log — nem no console do AppServer, nem no do DbAccess —
    # e o processo compartilhado era o único ponto que todas dividiam. A TOTVS
    # documenta várias instâncias na mesma máquina, cada uma em outra porta.
    #
    # Voltando para falso, o desenho antigo volta inteiro: DBPort fixo em 7890
    # e um só processo, com os aliases consolidados.
    "dbaccess_por_instancia": True,
}


def _para_bool(valor) -> bool:
    if isinstance(valor, bool):
        return valor
    return str(valor).strip().lower() in ("1", "true", "on", "sim")


class Preferencias:
    def __init__(self, arquivo: Path | None = None):
        self._arquivo = arquivo or ARQUIVO
        self._raiz_detectada: Path | None = None
        self._dados = self._carregar()

    def _carregar(self) -> dict:
        dados = dict(PADRAO)
        try:
            lido = json.loads(self._arquivo.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return dados
        if isinstance(lido, dict):
            dados.update({k: v for k, v in lido.items() if k in PADRAO})
        return self._normalizar(dados)

    def _salvar(self) -> None:
        self._arquivo.parent.mkdir(parents=True, exist_ok=True)
        self._arquivo.write_text(
            json.dumps(self._dados, ensure_ascii=False, indent=2),
            encoding="utf-8")

    @staticmethod
    def _normalizar(dados: dict) -> dict:
        # Só chave conhecida entra: o arquivo é contrato, não depósito do que
        # a tela mandar.
        limpo = dict(PADRAO)
        limpo.update({k: v for k, v in (dados or {}).items() if k in PADRAO})

        try:
            maximo = int(str(limpo["max_instancias"]).strip())
        except (TypeError, ValueError):
            maximo = PADRAO["max_instancias"]
        limpo["max_instancias"] = max(1, maximo)   # sem teto; piso em 1

        if limpo.get("modo") not in MODOS:
            limpo["modo"] = PADRAO["modo"]

        limpo["log_fixado"] = _para_bool(limpo.get("log_fixado"))
        limpo["dividir_casos"] = _para_bool(limpo.get("dividir_casos"))
        limpo["dbaccess_por_instancia"] = _para_bool(
            limpo.get("dbaccess_por_instancia"))

        limpo["raiz_testes"] = str(limpo.get("raiz_testes") or "").strip()
        return limpo

    # ── leitura ──
    @property
    def tudo(self) -> dict:
        return dict(self._dados)

    @property
    def raiz_testes(self) -> Path:
        """Raiz efetiva: a apontada à mão, ou a detectada.

        A detecção é cacheada na instância — varrer as pastas prováveis a cada
        leitura custaria caro, e a Api consulta isso a cada listagem de testes.
        """
        apontada = self._dados["raiz_testes"]
        if apontada:
            return Path(apontada)
        if self._raiz_detectada is None:
            self._raiz_detectada = descobrir_raiz() or RAIZ_PADRAO
        return self._raiz_detectada

    def redetectar(self) -> Path | None:
        """Refaz a busca (a pasta pode ter aparecido depois do boot)."""
        self._raiz_detectada = descobrir_raiz()
        return self._raiz_detectada

    @property
    def fontes(self) -> dict:
        """O que a tela precisa mostrar sobre a origem dos fontes."""
        raiz = self.raiz_testes
        return {
            "apontada": self._dados["raiz_testes"],
            "efetiva": str(raiz),
            "detectada": not self._dados["raiz_testes"],
            "existe": raiz.is_dir(),
        }

    @property
    def max_instancias(self) -> int:
        return self._dados["max_instancias"]

    @property
    def modo(self) -> str:
        return self._dados["modo"]

    @property
    def paralelo(self) -> bool:
        return self._dados["modo"] == "paralelo"

    @property
    def dividir_casos(self) -> bool:
        """Só faz sentido em paralelo: com uma instância, dividir a rotina em
        fatias sequenciais só acrescentaria um login por caso."""
        return self.paralelo and self._dados["dividir_casos"]

    @property
    def dbaccess_por_instancia(self) -> bool:
        return bool(self._dados["dbaccess_por_instancia"])

    # ── escrita ──
    def salvar(self, novo: dict) -> dict:
        anterior = self._dados.get("raiz_testes")
        self._dados = self._normalizar({**self._dados, **(novo or {})})
        if self._dados["raiz_testes"] != anterior:
            self._raiz_detectada = None   # limpar o campo volta a detectar
        self._salvar()
        log.info("[PREFS] modo=%s  instâncias=%d  raiz=%s",
                 self._dados["modo"], self._dados["max_instancias"],
                 self._dados["raiz_testes"])
        return {"ok": True, "preferencias": self.tudo}
