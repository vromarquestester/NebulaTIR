"""Memória da máquina antes de uma corrida — aviso, não trava.

Medido em 2026-08-18: 15,7 GB de RAM, e uma instância (AppServer + DbAccess
+ Firefox do TIR) custa ~1,2 GB. A corrida paralela das 14:12 de 2026-09-18
rodou com 90 % em uso (TIR: `MEMORY AVAILABLE: 9.8%`) e as duas instâncias
pararam no login sem erro em log nenhum. Página quente em disco não degrada
suave — vira thrashing. Decisão do usuário: dizer o teto sugerido no log e
seguir; quem decide é ele.
"""

from __future__ import annotations

import ctypes
import logging
import os

log = logging.getLogger(__name__)

GB = 1024 ** 3
# Por instância: AppServer (~550 MB) + Firefox enxuto (~450 MB) + DbAccess e
# navegador do TIR. Arredondado para cima de propósito.
CUSTO_POR_INSTANCIA_BYTES = int(1.2 * GB)
# Abaixo disto o Windows já está comprimindo e paginando o resto.
FOLGA_MINIMA_BYTES = int(1.5 * GB)


def memoria() -> dict:
    """`{"total", "livre"}` em bytes via `GlobalMemoryStatusEx`. Vazio fora do Windows."""
    if os.name != "nt":
        return {}

    class _Status(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
    estado = _Status()
    estado.dwLength = ctypes.sizeof(_Status)
    try:
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(estado)):
            return {}
    except Exception:
        return {}
    return {"total": int(estado.ullTotalPhys), "livre": int(estado.ullAvailPhys),
            "carga": int(estado.dwMemoryLoad)}


def avaliar_memoria(instancias: int, medida: dict | None = None) -> dict:
    """Quantas instâncias cabem na memória livre, e uma linha para o log."""
    m = medida if medida is not None else memoria()
    if not m:
        return {}
    livre = m["livre"]
    cabem = max(0, (livre - FOLGA_MINIMA_BYTES) // CUSTO_POR_INSTANCIA_BYTES)
    apertada = instancias > cabem
    texto = (f"Memória livre: {livre / GB:.1f} GB de {m['total'] / GB:.1f} GB "
             f"({m.get('carga', 0)} % em uso) — cabem ~{cabem} instância(s) "
             f"com folga; pedidas: {instancias}.")
    if apertada:
        texto += (" Acima disso a máquina pagina e o login do Protheus fica "
                  "pendurado sem erro. Feche navegador/editor ou reduza as "
                  "instâncias.")
    return {"livre": livre, "total": m["total"], "cabem": int(cabem),
            "apertada": apertada, "texto": texto}
