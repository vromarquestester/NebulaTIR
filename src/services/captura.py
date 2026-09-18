"""Captura da janela do aplicativo, para o pacote de diagnóstico.

Sem dependência nova: `pywebview` 6.2.1 não tem `save_screenshot`, e nem
Pillow nem pywin32 estão no projeto — puxar qualquer um só para tirar um print
engordaria o executável empacotado. Aqui é GDI puro via `ctypes`, gravando um
BMP (formato simples de escrever à mão; o DEFLATE do zip cuida do tamanho).

Captura a **região da janela do app**, não a tela inteira: o resto do monitor
é assunto do usuário, não do suporte.
"""

import ctypes
import logging
import struct
from ctypes import wintypes
from pathlib import Path

log = logging.getLogger(__name__)

SRCCOPY = 0x00CC0020
DIB_RGB_COLORS = 0

_user32 = ctypes.windll.user32
_gdi32 = ctypes.windll.gdi32


def _janelas_visiveis() -> list:
    """[(hwnd, título)] das janelas visíveis com título."""
    achadas = []
    proto = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def _callback(hwnd, _lparam):
        if not _user32.IsWindowVisible(hwnd):
            return True
        tamanho = _user32.GetWindowTextLengthW(hwnd)
        if tamanho <= 0:
            return True
        buffer = ctypes.create_unicode_buffer(tamanho + 1)
        _user32.GetWindowTextW(hwnd, buffer, tamanho + 1)
        achadas.append((hwnd, buffer.value))
        return True

    _user32.EnumWindows(proto(_callback), 0)
    return achadas


def localizar_janela(trecho_do_titulo: str):
    """HWND da primeira janela cujo título contém `trecho`. None se não achar."""
    alvo = (trecho_do_titulo or "").strip().lower()
    if not alvo:
        return None
    for hwnd, titulo in _janelas_visiveis():
        if alvo in titulo.lower():
            return hwnd
    return None


def montar_bmp(largura: int, altura: int, pixels_bgra: bytes) -> bytes:
    """Empacota pixels BGRA (top-down) num BMP de 32 bits.

    Altura negativa no cabeçalho = varredura de cima para baixo, que é como o
    GDI entrega quando pedimos assim — evita a imagem sair de cabeça para
    baixo, o clássico de quem escreve BMP na mão.
    """
    tamanho_dados = len(pixels_bgra)
    tamanho_arquivo = 14 + 40 + tamanho_dados
    cabecalho = struct.pack("<2sIHHI", b"BM", tamanho_arquivo, 0, 0, 14 + 40)
    info = struct.pack("<IiiHHIIiiII", 40, largura, -altura, 1, 32, 0,
                       tamanho_dados, 2835, 2835, 0, 0)
    return cabecalho + info + pixels_bgra


def capturar_janela(trecho_do_titulo: str, destino: Path) -> Path | None:
    """Salva um BMP com a área da janela. None quando não foi possível."""
    hwnd = localizar_janela(trecho_do_titulo)
    if not hwnd:
        log.debug(f"  [PRINT] Janela contendo {trecho_do_titulo!r} não encontrada.")
        return None

    rect = wintypes.RECT()
    if not _user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return None
    largura = rect.right - rect.left
    altura = rect.bottom - rect.top
    if largura <= 0 or altura <= 0:
        return None

    dc_tela = _user32.GetDC(0)
    dc_memoria = _gdi32.CreateCompatibleDC(dc_tela)
    bitmap = _gdi32.CreateCompatibleBitmap(dc_tela, largura, altura)
    anterior = _gdi32.SelectObject(dc_memoria, bitmap)
    try:
        if not _gdi32.BitBlt(dc_memoria, 0, 0, largura, altura,
                             dc_tela, rect.left, rect.top, SRCCOPY):
            return None

        buffer = ctypes.create_string_buffer(largura * altura * 4)
        info = struct.pack("<IiiHHIIiiII", 40, largura, -altura, 1, 32, 0,
                           0, 0, 0, 0, 0)
        info_buffer = ctypes.create_string_buffer(info, len(info))
        copiadas = _gdi32.GetDIBits(dc_memoria, bitmap, 0, altura, buffer,
                                    info_buffer, DIB_RGB_COLORS)
        if not copiadas:
            return None

        destino = Path(destino)
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_bytes(montar_bmp(largura, altura, buffer.raw))
        log.debug(f"  [PRINT] Janela capturada em {destino} ({largura}x{altura}).")
        return destino
    except Exception as e:
        log.debug(f"  [PRINT] Falha ao capturar a janela: {e}")
        return None
    finally:
        _gdi32.SelectObject(dc_memoria, anterior)
        _gdi32.DeleteObject(bitmap)
        _gdi32.DeleteDC(dc_memoria)
        _user32.ReleaseDC(0, dc_tela)
