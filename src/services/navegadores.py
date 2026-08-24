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
