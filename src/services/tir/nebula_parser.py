"""Parseia arquivos .log de suites TOTVS/Protheus.

INCORPORADO DO LogNebula (`C:\\Dev\\Projetos\\LogNebula\\parser.py`) em
2026-08-14. Vieram só a leitura do log e a geração do PNG; a interface
CustomTkinter ficou lá. O módulo foi renomeado de `parser` para
`nebula_parser`: ele é copiado para dentro da pasta do teste, e ocupar o nome
`parser` no diretório de trabalho é convite a conflito de import.
"""
import re
from dataclasses import dataclass, field
from typing import List, Optional


RE_SUITE_START = re.compile(r"Inicio da suite:\s*(\S+)")
RE_SUITE_END = re.compile(r"Fim da suite:\s*(\S+)")
RE_TEST_CASE = re.compile(
    r"(?P<ts>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:[+-]\d{2}:\d{2})?)?"
    r"[^\n]*?Caso de teste '(?P<name>[^']+)':\s*(?P<status>passou|falhou)\.",
    re.IGNORECASE,
)
RE_TEST_TIME = re.compile(r"Tempo do Teste \(([^)]+)\)\s*foi\s*([0-9:]+)")
RE_TOTAL = re.compile(r"^\s*Total:\s*(\d+)", re.MULTILINE)
RE_PASSOU = re.compile(r"^\s*Passou:\s*(\d+)", re.MULTILINE)
RE_FALHOU = re.compile(r"^\s*Falhou:\s*(\d+)", re.MULTILINE)
# Linha gravada pelo tir_report.py para identificar a origem da execução.
RE_TOOL = re.compile(r"^\s*Ferramenta:\s*(?P<nome>\S+)(?:\s+(?P<versao>\S+))?\s*$", re.MULTILINE)


# Status canônico (usado pela UI e pelo exporter para escolher a cor)
STATUS_PASS = "Passou"
STATUS_FAIL = "Não passou"

# Marcador redondo antes do status. Glifo monocromático da DejaVu Sans: herda a cor
# do texto, então fica verde no "Passou" e vermelho no "Não passou".
ICON_STATUS = "●"  # U+25CF


@dataclass
class TestCase:
    name: str
    description: str
    status: str  # STATUS_PASS | STATUS_FAIL
    details: str

    @property
    def passed(self) -> bool:
        return self.status == STATUS_PASS

    @property
    def status_label(self) -> str:
        """Texto exibido na coluna de status: '● Passou' | '● Não passou'.

        A cor do círculo vem da cor do texto, definida na UI/exporter por `passed`.
        """
        return f"{ICON_STATUS} {self.status}"


@dataclass
class SuiteReport:
    suite_name: str          # nome cru do log (ex.: COMA222TESTSUITE)
    display_name: str        # sem TESTSUITE/SUITE (ex.: COMA222)
    success: bool
    total: int
    passou: int
    falhou: int
    cases: List[TestCase] = field(default_factory=list)
    tool: str = ""            # ferramenta de origem (ex.: "TIR"); vazio = log de console
    tool_version: str = ""

    @property
    def tool_label(self) -> str:
        """'TIR 2.12.0' | '' quando a origem não foi identificada."""
        return f"{self.tool} {self.tool_version}".strip()

    @property
    def title(self) -> str:
        """Título do relatório, usado tanto pela UI quanto pelo PNG."""
        origem = f" ({self.tool_label})" if self.tool_label else ""
        resultado = "Execução finalizada com sucesso" if self.success else "Execução falhou"
        return f"{self.display_name}{origem} - {resultado}"


def _strip_suite_suffix(name: str) -> str:
    """COMA222TESTSUITE -> COMA222 | MATA466NTESTSUITE -> MATA466N | MATA410SUITE -> MATA410."""
    return re.sub(r"(TEST)?SUITE$", "", name, flags=re.IGNORECASE)


def _derive_description(case_name: str) -> str:
    """M466NCT001 -> 'Caso de teste 001' | COMA222_003 -> 'Caso de teste 003'."""
    m = re.search(r"(\d+)$", case_name)
    if m:
        return f"Caso de teste {m.group(1)}"
    return "Caso de teste"


def _is_plumbing(case_name: str) -> bool:
    return ":SetUp" in case_name or ":TearDown" in case_name


def _format_timestamp(ts: Optional[str]) -> str:
    """'2026-07-09T11:42:05.420000-03:00' -> '09/07/2026 11:42:05'."""
    if not ts:
        return ""
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})", ts)
    if not m:
        return ""
    y, mo, d, hh, mm, ss = m.groups()
    return f"{d}/{mo}/{y} {hh}:{mm}:{ss}"


def is_tir_debug_log(raw: bytes) -> bool:
    """Identifica o trace DEBUG do TIR ("DebugLog": true) pelo cabeçalho do arquivo.

    Olha só o começo: esses arquivos passam de 1 MB e não vale decodificar tudo.
    """
    head = raw[:4096]
    return b"-DEBUG-root-" in head and b"logging_config" in head


def parse_log(path: str) -> Optional[SuiteReport]:
    """Lê o log e devolve a primeira suite encontrada. None se não achar suite.

    Aceita dois formatos e escolhe pelo conteúdo:
    - console do Protheus / saída do tir_report.py (blocos "Inicio da suite:");
    - trace DEBUG do próprio TIR (arquivos TIR_*.log gerados por "DebugLog": true).
    """
    with open(path, "rb") as f:
        raw = f.read()

    if is_tir_debug_log(raw):
        from nebula_parser_tir import parse_tir_debug_log
        return parse_tir_debug_log(path, raw)

    content = raw.decode("utf-8", errors="replace")

    m_start = RE_SUITE_START.search(content)
    if not m_start:
        return None
    suite_name = m_start.group(1)

    # Corta em [start_pos, end_pos] para não misturar suites nem preâmbulo
    start_pos = m_start.start()
    m_end = RE_SUITE_END.search(content, start_pos)
    end_pos = m_end.end() if m_end else len(content)
    block = content[start_pos:end_pos]

    # Coleta tempos primeiro (para casar com casos)
    times = {name: t for name, t in RE_TEST_TIME.findall(block)}

    cases: List[TestCase] = []
    for match in RE_TEST_CASE.finditer(block):
        case_name = match.group("name")
        if _is_plumbing(case_name):
            continue
        passed = match.group("status").lower() == "passou"
        exec_dt = _format_timestamp(match.group("ts"))

        # Extrai bloco de mensagens após esta linha até próxima linha "Caso de teste" ou "Total:"
        tail_start = match.end()
        next_case = RE_TEST_CASE.search(block, tail_start)
        next_total = block.find("Total:", tail_start)
        candidates = [p for p in (next_case.start() if next_case else -1, next_total) if p > 0]
        tail_end = min(candidates) if candidates else len(block)
        tail = block[tail_start:tail_end]

        # Extrai mensagens (linhas entre "Mensagens:" e "Tempo do Teste")
        msg = ""
        m_msg = re.search(r"Mensagens:\s*(.*?)(?=Tempo do Teste|\Z)", tail, re.DOTALL)
        if m_msg:
            msg = m_msg.group(1).strip()

        tempo = times.get(case_name, "")
        details_parts = []
        if exec_dt:
            details_parts.append(f"Data: {exec_dt}")
        if tempo:
            details_parts.append(f"Tempo: {tempo}")
        if msg:
            details_parts.append(msg)
        details = " | ".join(details_parts) if details_parts else "—"

        cases.append(TestCase(
            name=case_name,
            description=_derive_description(case_name),
            status=STATUS_PASS if passed else STATUS_FAIL,
            details=details,
        ))

    total = int(RE_TOTAL.search(block).group(1)) if RE_TOTAL.search(block) else len(cases)
    passou = int(RE_PASSOU.search(block).group(1)) if RE_PASSOU.search(block) else sum(1 for c in cases if c.passed)
    falhou = int(RE_FALHOU.search(block).group(1)) if RE_FALHOU.search(block) else sum(1 for c in cases if not c.passed)

    m_tool = RE_TOOL.search(block)

    return SuiteReport(
        suite_name=suite_name,
        display_name=_strip_suite_suffix(suite_name),
        success=(falhou == 0),
        total=total,
        passou=passou,
        falhou=falhou,
        cases=cases,
        tool=m_tool.group("nome") if m_tool else "",
        tool_version=(m_tool.group("versao") or "") if m_tool else "",
    )
