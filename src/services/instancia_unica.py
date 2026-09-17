"""Uma instância por programa.

Abrir o executável duas vezes subia duas janelas iguais, cada uma com os
próprios monitores, canal e atualizador — duas verdades sobre o mesmo estado,
brigando pelos mesmos arquivos. A segunda abertura agora acha a janela que já
existe, traz para a frente e sai.

A trava é um **mutex nomeado do Windows** (`Global\\<nome>`): o sistema o
solta sozinho quando o processo morre, então crash não deixa trava órfã — ao
contrário de arquivo de lock. `Global\\` cobre sessão e elevação diferentes;
sem permissão para o namespace global (raro fora de serviço), cai em
`Local\\`.

**Relançamento não é segunda instância.** O atualizador (`atualizacao.py`)
sobe o executável novo e deixa este terminar; por alguns instantes os dois
existem. O filho recebe `PYINSTALLER_RESET_ENVIRONMENT` no ambiente, e é por
essa marca que ele espera a trava do pai soltar em vez de desistir.

Cópia idêntica em todas as ferramentas da família (Gerenciador de Ambientes,
NebulaTIR). Programa novo copia o módulo, não reescreve.
"""

from __future__ import annotations

import logging
import os
import time

log = logging.getLogger(__name__)

ERROR_ALREADY_EXISTS = 183
ERROR_ACCESS_DENIED = 5

# Quanto o relançado espera o pai soltar a trava. O pai só tem de terminar o
# processo — leva décimos de segundo; 10 s é folga para máquina lenta.
ESPERA_RELANCAMENTO_SEG = 10.0
INTERVALO_SEG = 0.25

# Marca que o atualizador põe no ambiente do processo relançado.
VARIAVEL_RELANCAMENTO = "PYINSTALLER_RESET_ENVIRONMENT"

# Handle do mutex, guardado para a vida do processo: fechar soltaria a trava.
_mutex = None
_nome_atual = ""


def _kernel32():
    import ctypes
    return ctypes.WinDLL("kernel32", use_last_error=True)


def _criar_mutex(nome: str):
    """`(handle, ja_existia)`; handle None se nem deu para criar."""
    import ctypes
    k32 = _kernel32()
    k32.CreateMutexW.restype = ctypes.c_void_p
    k32.CreateMutexW.argtypes = (ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p)
    for prefixo in ("Global\\", "Local\\"):
        handle = k32.CreateMutexW(None, False, prefixo + nome)
        erro = ctypes.get_last_error()
        if handle:
            return handle, erro == ERROR_ALREADY_EXISTS
        if erro != ERROR_ACCESS_DENIED:
            break
    return None, False


def _fechar(handle) -> None:
    if handle:
        _kernel32().CloseHandle(handle)


def adquirir(nome: str, espera_seg: float = 0.0) -> bool:
    """Tenta ficar com a trava `nome`. True = este processo é o único.

    `espera_seg` > 0 insiste até esse tempo — é o caso do relançamento, em
    que o pai ainda está terminando. Fora do Windows sempre True: não há mutex
    nomeado, e o programa só é distribuído para Windows.
    """
    global _mutex, _nome_atual
    if os.name != "nt":
        return True
    if _mutex is not None and _nome_atual == nome:
        return True

    fim = time.monotonic() + max(0.0, espera_seg)
    while True:
        handle, ja_existia = _criar_mutex(nome)
        if handle is None:
            # Sem conseguir criar o mutex, não há como saber — e travar a
            # abertura por isso seria pior do que deixar abrir.
            log.warning("[UNICA] não foi possível criar a trava %s; seguindo.", nome)
            return True
        if not ja_existia:
            _mutex, _nome_atual = handle, nome
            return True
        _fechar(handle)
        if time.monotonic() >= fim:
            return False
        time.sleep(INTERVALO_SEG)


def liberar() -> None:
    """Solta a trava (o sistema faria isso na saída; serve para teste)."""
    global _mutex, _nome_atual
    if _mutex is not None:
        _fechar(_mutex)
    _mutex, _nome_atual = None, ""


def e_relancamento(ambiente: dict | None = None) -> bool:
    base = os.environ if ambiente is None else ambiente
    return bool(base.get(VARIAVEL_RELANCAMENTO))


# ─────────────────────────────────────────────────────────────
# JANELA QUE JÁ EXISTE
# ─────────────────────────────────────────────────────────────

def _janelas_com_titulo(prefixo: str) -> list[int]:
    """Handles das janelas visíveis cujo título começa com `prefixo`."""
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    achadas: list[int] = []
    proto = ctypes.WINFUNCTYPE(ctypes.c_int, wintypes.HWND, wintypes.LPARAM)

    def olhar(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        tamanho = user32.GetWindowTextLengthW(hwnd)
        if tamanho <= 0:
            return True
        buffer = ctypes.create_unicode_buffer(tamanho + 1)
        user32.GetWindowTextW(hwnd, buffer, tamanho + 1)
        if buffer.value.startswith(prefixo):
            achadas.append(hwnd)
        return True

    user32.EnumWindows(proto(olhar), 0)
    return achadas


def trazer_para_frente(titulo: str) -> bool:
    """Restaura e foca a janela cujo título começa com `titulo`."""
    if os.name != "nt":
        return False
    import ctypes
    janelas = _janelas_com_titulo(titulo)
    if not janelas:
        return False
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    hwnd = janelas[0]
    SW_RESTORE = 9
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
    # O Windows recusa `SetForegroundWindow` de um processo que não está em
    # primeiro plano. Simular um toque no Alt é o contorno documentado: o
    # processo passa a "ter entrada recente" e a chamada é aceita.
    if not user32.SetForegroundWindow(hwnd):
        VK_MENU, KEYEVENTF_KEYUP = 0x12, 0x0002
        user32.keybd_event(VK_MENU, 0, 0, 0)
        user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)
        user32.SetForegroundWindow(hwnd)
    return True


def _avisar(titulo: str) -> None:
    if os.name != "nt":
        return
    import ctypes
    MB_ICONINFORMATION = 0x40
    ctypes.WinDLL("user32").MessageBoxW(
        None, f"O {titulo} já está aberto.", titulo, MB_ICONINFORMATION)


def garantir(nome: str, titulo: str) -> bool:
    """Chamar no início do programa. True = seguir; False = já há outro.

    `nome` é o da trava (sem espaço: `NebulaTIR`); `titulo` é o começo do
    título da janela, para achar a instância aberta e trazê-la para a frente.
    Com False, quem chama só precisa sair: a janela existente já está na
    frente, ou, se não foi achada, o usuário viu o aviso.
    """
    espera = ESPERA_RELANCAMENTO_SEG if e_relancamento() else 0.0
    if adquirir(nome, espera):
        return True
    log.info("[UNICA] %s já está aberto; trazendo a janela para a frente.", nome)
    if not trazer_para_frente(titulo):
        _avisar(titulo)
    return False
