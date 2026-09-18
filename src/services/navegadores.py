"""Navegadores instalados na máquina, para o campo `Browser` do TIR.

Fonte: `SOFTWARE\\Clients\\StartMenuInternet` no registro — é onde o Windows
registra todo navegador que se declara como tal, em HKLM e HKCU (instalação
por usuário). Nada de varrer `Program Files`: caminho de instalação muda por
versão e por idioma do Windows.

O TIR aceita o nome do navegador em `config.json` (`"Browser": "Firefox"`), daí
a normalização para os nomes que ele conhece.
"""

from __future__ import annotations

import logging
import os

log = logging.getLogger(__name__)

# Chave do registro → nome como o TIR espera. A comparação é por trecho porque
# o registro traz variações ("Google Chrome", "Google Chrome Canary").
CONHECIDOS = [
    ("firefox", "Firefox"),
    ("chrome", "Chrome"),
    ("msedge", "Edge"),
    ("edge", "Edge"),
]

# Sem registro legível, o formulário não pode ficar sem opção nenhuma.
RESERVA = ["Chrome", "Firefox", "Edge"]

CHAVE_REGISTRO = r"SOFTWARE\Clients\StartMenuInternet"
SUBCHAVE_COMANDO = r"\shell\open\command"


def _nome_do_tir(bruto: str) -> str | None:
    alvo = (bruto or "").strip().lower()
    for trecho, nome in CONHECIDOS:
        if trecho in alvo:
            return nome
    return None


def listar() -> list[str]:
    """Navegadores instalados, em ordem estável. Nunca devolve lista vazia."""
    if os.name != "nt":
        return list(RESERVA)

    import winreg

    achados: list[str] = []
    for raiz in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            with winreg.OpenKey(raiz, r"SOFTWARE\Clients\StartMenuInternet") as chave:
                for i in range(winreg.QueryInfoKey(chave)[0]):
                    try:
                        bruto = winreg.EnumKey(chave, i)
                    except OSError:
                        continue
                    nome = _nome_do_tir(bruto)
                    if nome and nome not in achados:
                        achados.append(nome)
        except OSError:
            continue

    if not achados:
        log.warning("[NAVEGADORES] Nenhum navegador encontrado no registro; "
                    "usando a lista de reserva.")
        return list(RESERVA)
    return achados


def preferido(instalados: list[str] | None = None) -> str:
    """Sugestão inicial para um ambiente recém-importado.

    Firefox primeiro: é com ele que o TIR roda melhor hoje. O Chrome se
    atualiza sozinho e deixa o ChromeDriver para trás — foi o que derrubou uma
    corrida inteira aqui, com `session not created` no `setUpClass`.
    """
    # `None` é "não informado"; lista vazia é "nenhum instalado" — que não é a
    # mesma coisa e não deve disparar uma varredura do registro.
    if instalados is None:
        instalados = listar()
    for nome in ("Firefox", "Chrome", "Edge"):
        if nome in instalados:
            return nome
    return instalados[0] if instalados else ""


def _versao_do_exe(caminho: str) -> str:
    """`FileVersion` do executável, pela API do Windows. Vazio se não deu."""
    try:
        import ctypes
        import struct
        from ctypes import wintypes
        version = ctypes.WinDLL("version")
        tamanho = version.GetFileVersionInfoSizeW(caminho, None)
        if not tamanho:
            return ""
        dados = ctypes.create_string_buffer(tamanho)
        if not version.GetFileVersionInfoW(caminho, 0, tamanho, dados):
            return ""
        ponteiro = ctypes.c_void_p()
        comprimento = wintypes.UINT()
        if not version.VerQueryValueW(dados, "\\", ctypes.byref(ponteiro),
                                      ctypes.byref(comprimento)):
            return ""
        # VS_FIXEDFILEINFO: dwFileVersionMS/LS nos offsets 8 e 12.
        bruto = ctypes.string_at(ponteiro.value, comprimento.value)
        ms, ls = struct.unpack_from("<II", bruto, 8)
        return f"{ms >> 16}.{ms & 0xFFFF}.{ls >> 16}.{ls & 0xFFFF}"
    except Exception:
        return ""


def detalhar() -> list[dict]:
    """`[{"nome", "exe", "versao"}]` dos navegadores do registro — para o
    diagnóstico. A versão é o que decide se o driver serve; sem ela o
    pacote de suporte não diz nada sobre o navegador (2026-09-18)."""
    if os.name != "nt":
        return []
    import winreg

    achados: list[dict] = []
    vistos: set[str] = set()
    for raiz in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            with winreg.OpenKey(raiz, CHAVE_REGISTRO) as chave:
                for i in range(winreg.QueryInfoKey(chave)[0]):
                    try:
                        bruto = winreg.EnumKey(chave, i)
                        with winreg.OpenKey(chave, bruto + SUBCHAVE_COMANDO) as cmd:
                            exe = str(winreg.QueryValue(cmd, None) or "").strip().strip('"')
                    except OSError:
                        continue
                    nome = _nome_do_tir(bruto) or bruto
                    if exe.lower() in vistos:
                        continue
                    vistos.add(exe.lower())
                    achados.append({"nome": nome, "exe": exe,
                                    "versao": _versao_do_exe(exe) if exe else ""})
        except OSError:
            continue
    return achados
