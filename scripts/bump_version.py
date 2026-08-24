"""
Incrementa a versao em `src/_version.py` e regenera `version_info.txt`.

Uso:
    uv run python scripts/bump_version.py patch
    uv run python scripts/bump_version.py minor
    uv run python scripts/bump_version.py major
    uv run python scripts/bump_version.py prerelease           # 0.1.0 -> 0.1.0-beta1
    uv run python scripts/bump_version.py prerelease alpha     # 0.1.0 -> 0.1.0-alpha1
    uv run python scripts/bump_version.py prerelease rc        # 0.1.0 -> 0.1.0-rc1
    uv run python scripts/bump_version.py set 1.0.0
    uv run python scripts/bump_version.py set 1.0.0-beta1

Apos rodar, FAZER COMMIT do par (src/_version.py + version_info.txt) e
criar a tag `vX.Y.Z` correspondente para disparar o workflow de release.

Runtime: neste projeto o Python vem SO via `uv` (venv propria, 3.12). Nao
assumir `python` no PATH.

Politica SemVer 2.0.0:
    MAJOR  : breaking changes / API incompativel
    MINOR  : features novas, compativel para tras
    PATCH  : bugfix, compativel para tras
    PRERELEASE: -alpha1 / -beta1 / -rc1 (incrementa numero)

Convencao de mensagem de commit (manual):
    feat(vX.Y.Z): ...
    fix(vX.Y.Z): ...
    refactor(vX.Y.Z): ...
    docs(vX.Y.Z): ...
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERSION_FILE = ROOT / "src" / "_version.py"

_LINE_RE = re.compile(r'^__version__\s*=\s*"([^"]+)"\s*$', re.MULTILINE)
_SEMVER_RE = re.compile(
    r"^(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)"
    r"(?:-(?P<pretag>[a-zA-Z]+)(?P<prenum>\d+)?)?$"
)

VALID_PRETAGS = ("alpha", "beta", "rc")


def read_current() -> str:
    text = VERSION_FILE.read_text(encoding="utf-8")
    m = _LINE_RE.search(text)
    if not m:
        sys.exit(f"Nao foi possivel localizar __version__ em {VERSION_FILE}")
    return m.group(1)


def write_new(new_version: str) -> None:
    text = VERSION_FILE.read_text(encoding="utf-8")
    new_text, n = _LINE_RE.subn(f'__version__ = "{new_version}"', text)
    if n != 1:
        sys.exit("Falha ao reescrever __version__ (regex nao casou exatamente uma vez).")
    VERSION_FILE.write_text(new_text, encoding="utf-8")


def parse(v: str):
    m = _SEMVER_RE.match(v)
    if not m:
        sys.exit(f"Versao atual invalida: {v!r}")
    return (
        int(m["major"]),
        int(m["minor"]),
        int(m["patch"]),
        m["pretag"],
        int(m["prenum"]) if m["prenum"] else None,
    )


def bump(current: str, kind: str, pretag: str | None = None) -> str:
    major, minor, patch, cur_pretag, cur_prenum = parse(current)

    if kind == "major":
        return f"{major + 1}.0.0"
    if kind == "minor":
        return f"{major}.{minor + 1}.0"
    if kind == "patch":
        return f"{major}.{minor}.{patch + 1}"
    if kind == "prerelease":
        tag = pretag or cur_pretag or "beta"
        if tag not in VALID_PRETAGS:
            sys.exit(f"Pre-release tag invalida: {tag!r}. Use: {VALID_PRETAGS}")
        # mesma tag -> incrementa numero; tag diferente ou primeira vez -> tag1
        if cur_pretag == tag and cur_prenum is not None:
            new_num = cur_prenum + 1
        else:
            new_num = 1
        return f"{major}.{minor}.{patch}-{tag}{new_num}"

    sys.exit(f"Tipo de bump desconhecido: {kind!r}")


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 1

    cmd = argv[1]
    current = read_current()

    if cmd == "set":
        if len(argv) < 3:
            sys.exit("Uso: python scripts/bump_version.py set X.Y.Z[-preN]")
        new = argv[2].lstrip("vV")
        parse(new)  # validacao
    elif cmd in ("major", "minor", "patch"):
        new = bump(current, cmd)
    elif cmd == "prerelease":
        pretag = argv[2] if len(argv) >= 3 else None
        new = bump(current, "prerelease", pretag)
    else:
        sys.exit(f"Comando desconhecido: {cmd}")

    if new == current:
        sys.exit(f"Versao ja eh {current}, nada a fazer.")

    write_new(new)
    print(f"[bump_version] {current} -> {new}")

    # Regenera version_info.txt usando o gerador existente.
    rc = subprocess.call([sys.executable, str(Path(__file__).parent / "generate_version_info.py")])
    if rc != 0:
        sys.exit("Falha ao regenerar version_info.txt")

    print()
    print("PROXIMOS PASSOS:")
    print(f"  git add src/_version.py version_info.txt")
    print(f'  git commit -m "chore(v{new}): bump version"')
    print(f"  git tag v{new}")
    print(f"  git push --tags    # dispara o workflow de release")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
