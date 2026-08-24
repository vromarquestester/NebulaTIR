"""
Gera `version_info.txt` (VS_VERSIONINFO embutido no EXE pelo PyInstaller)
a partir de `src/_version.py`.

Uso:
    uv run python scripts/generate_version_info.py

Saída:
    Sobrescreve `version_info.txt` na raiz do projeto.

Quem chama:
    - scripts/bump_version.py (apos atualizar __version__)
    - NebulaTIR.spec (no inicio do build)
    - .github/workflows/build_exe.yml (antes de `pyinstaller`)

O template vivia inline no `.spec`. Saiu de la para nao existirem duas
versoes do mesmo VS_VERSIONINFO — o `.spec` importa este modulo.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERSION_FILE = ROOT / "src" / "_version.py"
OUTPUT = ROOT / "version_info.txt"

_VERSION_LINE_RE = re.compile(r'^__version__\s*=\s*"([^"]+)"\s*$', re.MULTILINE)

# Regex SemVer 2.0.0 simplificado: MAJOR.MINOR.PATCH(-prerelease)?
_SEMVER_RE = re.compile(
    r"^(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)"
    r"(?:-(?P<pre>[0-9A-Za-z.-]+))?$"
)


def read_version() -> str:
    """Le __version__ direto do texto de _version.py (sem importar -> evita pycache)."""
    text = VERSION_FILE.read_text(encoding="utf-8")
    m = _VERSION_LINE_RE.search(text)
    if not m:
        raise SystemExit(f"__version__ nao localizado em {VERSION_FILE}")
    return m.group(1)


def parse_semver(v: str) -> tuple[int, int, int, str]:
    m = _SEMVER_RE.match(v)
    if not m:
        raise ValueError(
            f"Versao invalida em src/_version.py: {v!r}. "
            "Use MAJOR.MINOR.PATCH ou MAJOR.MINOR.PATCH-prerelease."
        )
    return (
        int(m["major"]),
        int(m["minor"]),
        int(m["patch"]),
        m["pre"] or "",
    )


def render(major: int, minor: int, patch: int, pre: str, version_str: str) -> str:
    # FILEVERSION/PRODUCTVERSION precisa ser tupla de 4 inteiros.
    # Usamos build = 0; pre-release nao cabe nesse campo (ele aparece so no
    # StringStruct 'FileVersion'/'ProductVersion').
    return f"""# UTF-8
#
# Arquivo GERADO automaticamente por scripts/generate_version_info.py.
# NAO EDITAR MANUALMENTE — alteracoes serao sobrescritas.
# Fonte unica da verdade: src/_version.py (__version__).
#
# Lido pelo PyInstaller via `version='version_info.txt'` em NebulaTIR.spec.
# Esses metadados nao substituem assinatura Authenticode — apenas preenchem o
# painel "Propriedades > Detalhes" do EXE.

VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({major}, {minor}, {patch}, 0),
    prodvers=({major}, {minor}, {patch}, 0),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0),
  ),
  kids=[
    StringFileInfo(
      [
        StringTable(
          u'040904B0',
          [
            StringStruct(u'CompanyName', u'TOTVS'),
            StringStruct(u'FileDescription', u'NebulaTIR - interface de execucao do TIR'),
            StringStruct(u'FileVersion', u'{version_str}'),
            StringStruct(u'InternalName', u'NebulaTIR'),
            StringStruct(u'LegalCopyright', u'(c) TOTVS - Uso interno'),
            StringStruct(u'OriginalFilename', u'NebulaTIR.exe'),
            StringStruct(u'ProductName', u'NebulaTIR'),
            StringStruct(u'ProductVersion', u'{version_str}'),
          ]
        )
      ]
    ),
    VarFileInfo([VarStruct(u'Translation', [1033, 1200])])
  ]
)
"""


def main() -> int:
    version = read_version()
    major, minor, patch, pre = parse_semver(version)
    OUTPUT.write_text(render(major, minor, patch, pre, version), encoding="utf-8")
    print(f"[generate_version_info] {OUTPUT} atualizado para v{version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
