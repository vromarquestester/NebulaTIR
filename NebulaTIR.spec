# -*- mode: python ; coding: utf-8 -*-
"""Empacotamento do NebulaTIR (interface web / pywebview).

Entry point: `main_web.py`. O executável leva junto:

    webui/web/    HTML, CSS e JS da interface (carregados de sys._MEIPASS)

Diferenças propositais em relação ao `.spec` do Gerenciador de Ambientes:

- **`uac_admin=True`**, como no Gerenciador. A decisão inicial foi não elevar,
  e ela caiu quando a execução do TIR entrou no escopo: o NebulaTIR passa a
  subir instâncias do AppServer, que exigem administrador — e um processo sem
  elevação não consegue criar filho elevado.
- **Sem `resources/protheus_base`.** Provisionar ambiente é trabalho do
  Gerenciador; aqui não se copia binário do Protheus.
- **`config/` não é empacotado.** `ambientes_importados.json` e
  `preferencias.json` nascem ao lado do executável na primeira execução
  (ver `services/importados.py` e `services/preferencias.py`, que resolvem
  `BASE_DIR` por `sys.executable` quando congelado).

Build:

    uv run pyinstaller NebulaTIR.spec --noconfirm
"""
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

RAIZ = Path(SPECPATH)

# Versão vem de `src/_version.py` — uma fonte só. Sem isso, o número do
# VS_VERSIONINFO e o da janela divergem na primeira distração.
#
# O template do VS_VERSIONINFO vivia inline aqui; saiu para
# `scripts/generate_version_info.py`, que tambem e chamado pelo
# `scripts/bump_version.py` e pelo workflow de release. Duas copias do mesmo
# template divergiriam na primeira alteracao de metadado.
sys.path.insert(0, str(RAIZ / "scripts"))
import generate_version_info  # noqa: E402

VERSAO = generate_version_info.read_version()
generate_version_info.main()

datas = [
    ('src/webui/web', 'webui/web'),
    # Lançador do TIR, o código do relatório (incorporado do LogNebula) e as
    # fontes do PNG: são COPIADOS para a pasta de cada rotina na hora de
    # executar, então precisam existir como arquivo.
    ('src/services/tir', 'services/tir'),
]
binaries = []
hiddenimports = []

# pywebview carrega o backend do WebView2 dinamicamente.
for pacote in ('webview',):
    tmp_ret = collect_all(pacote)
    datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]

hiddenimports += ['clr_loader', 'pythonnet']

# Ícone é opcional: `main_web.caminho_icone()` devolve None e a janela abre
# com o ícone padrão. Empacotar só se o arquivo existir.
_icone = RAIZ / "resources" / "app.ico"
if _icone.exists():
    datas.append(('resources/app.ico', 'resources'))


a = Analysis(
    ['main_web.py'],
    pathex=['.', 'src'],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Nada de UI nativa, imagem ou cálculo numérico: o NebulaTIR é HTML no
    # WebView2 e stdlib. Excluir corta dezenas de MB do executável.
    # `pyodbc` saiu da lista: a limpeza de instância derruba o banco no SQL
    # Server, e sem isso o MDF/LDF fica bloqueado e os GB não saem do disco.
    excludes=['customtkinter', 'tkinter', 'matplotlib', 'numpy', 'PIL',
              'pytest'],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='NebulaTIR',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version='version_info.txt',
    uac_admin=True,
    icon=str(_icone) if _icone.exists() else None,
)
