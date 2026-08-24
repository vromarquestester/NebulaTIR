"""Parseia o trace DEBUG do TIR (arquivos TIR_*.log de "DebugLog": true).

Formato de contingência, para os logs que já existem em disco. Para execuções novas
prefira o tir_report.py, que grava o nome real de cada caso de teste — informação que
o TIR não registra quando a execução passa.

Três particularidades do arquivo, todas confirmadas em logs reais do TIR 2.12.0:

1. É cp1252, não UTF-8 (o FileHandler do TIR é criado sem `encoding`).
2. Todo registro aparece duas vezes: em `logging_config.py` o `debug_file_handler` é
   anexado ao root e também é o `target` do `memory_handler`, que está no root. Cada
   registro é gravado na hora e de novo no flush do buffer.
3. O resultado de cada caso sai numa linha `Json log_data: {...}` (log.py, método
   `generate_log`), emitida tanto em sucesso quanto em falha — mas sem o nome do caso.
"""
import ast
import os
import re
from typing import List, Optional

from nebula_parser import (
    STATUS_PASS,
    STATUS_FAIL,
    SuiteReport,
    TestCase,
    _strip_suite_suffix,
)

RE_JSON_LOG = re.compile(r"Json log_data:\s*(\{.*?\})\s*$", re.MULTILINE)
RE_TIR_VERSION = re.compile(r"TIR Version:\s*(\S+)")
# TIR_MATA465NTESTSUITE_20260804140929007.log -> MATA465NTESTSUITE
RE_FILENAME = re.compile(r"^TIR_(?P<suite>.+)_\d{17}$", re.IGNORECASE)


def _suite_from_filename(path: str) -> str:
    base = os.path.splitext(os.path.basename(path))[0]
    m = RE_FILENAME.match(base)
    return m.group("suite") if m else base


def _format_dt(valor: str) -> str:
    """'04/08/2026 14:11:50' já vem pronto; devolve vazio se vier fora do padrão."""
    return valor.strip() if re.match(r"\d{2}/\d{2}/\d{4}", valor.strip() or "") else ""


def _duracao(segundos) -> str:
    try:
        total = int(round(float(segundos)))
    except (TypeError, ValueError):
        return ""
    return f"{total // 3600:02d}:{(total % 3600) // 60:02d}:{total % 60:02d}"


def _entradas(content: str) -> List[dict]:
    """Extrai os dicionários de resultado, sem as repetições do arquivo.

    A deduplicação usa `cLogSequen`, que o TIR gera por caso de teste — mais seguro
    que comparar a linha inteira, que pode se repetir de forma legítima.
    """
    vistos = set()
    entradas = []
    for m in RE_JSON_LOG.finditer(content):
        try:
            dado = ast.literal_eval(m.group(1))
        except (ValueError, SyntaxError):
            continue
        if not isinstance(dado, dict):
            continue
        chave = dado.get("cLogSequen") or len(entradas)
        if chave in vistos:
            continue
        vistos.add(chave)
        entradas.append(dado)
    return entradas


def parse_tir_debug_log(path: str, raw: Optional[bytes] = None) -> Optional[SuiteReport]:
    """Monta o SuiteReport a partir do trace DEBUG. None se não houver resultado."""
    if raw is None:
        with open(path, "rb") as f:
            raw = f.read()

    content = raw.decode("cp1252", errors="replace")

    entradas = _entradas(content)
    if not entradas:
        return None

    m_ver = RE_TIR_VERSION.search(content)
    suite_name = _suite_from_filename(path)

    cases: List[TestCase] = []
    passou = falhou = 0
    for i, dado in enumerate(entradas, start=1):
        nok = dado.get("nLogCtsNok", 0)
        ok = not nok
        if ok:
            passou += 1
        else:
            falhou += 1

        programa = str(dado.get("cLogProgra", "") or _strip_suite_suffix(suite_name))

        partes = []
        data = _format_dt(str(dado.get("cLogDtExec", "")))
        if data:
            partes.append(f"Data: {data}")
        tempo = _duracao(dado.get("nLogCtsSeg"))
        if tempo:
            partes.append(f"Tempo: {tempo}")
        mensagem = str(dado.get("nLogCtsErr", "") or "").strip()
        if mensagem:
            partes.append(mensagem)

        cases.append(TestCase(
            name=f"{programa} #{i}",
            # O TIR não grava o nome do caso quando a execução passa; a sequência é o
            # único identificador real disponível por caso.
            description=f"Sequência {dado.get('cLogSequen', '')}".strip(),
            status=STATUS_PASS if ok else STATUS_FAIL,
            details=" | ".join(partes) if partes else "—",
        ))

    return SuiteReport(
        suite_name=suite_name,
        display_name=_strip_suite_suffix(suite_name),
        success=(falhou == 0),
        total=len(cases),
        passou=passou,
        falhou=falhou,
        cases=cases,
        tool="TIR",
        tool_version=m_ver.group(1) if m_ver else "",
    )
