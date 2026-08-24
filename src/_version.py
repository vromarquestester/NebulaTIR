"""
Single Source of Truth da versao da aplicacao.

NAO editar manualmente este arquivo em casos normais — use:

    uv run python scripts/bump_version.py patch        # 0.1.0 -> 0.1.1
    uv run python scripts/bump_version.py minor        # 0.1.0 -> 0.2.0
    uv run python scripts/bump_version.py major        # 0.1.0 -> 1.0.0
    uv run python scripts/bump_version.py prerelease   # 0.1.0 -> 0.1.0-beta1
    uv run python scripts/bump_version.py set 1.2.3    # define explicitamente

O script regenera automaticamente `version_info.txt` (metadados do EXE).

Quem consome esta variavel:
- NebulaTIR.spec     — chama scripts/generate_version_info.py no build
- version_info.txt   — VS_VERSIONINFO embutido no EXE (gerado)
- .github/workflows/build_exe.yml — confere git tag vs. __version__

Formato: SemVer 2.0.0 — MAJOR.MINOR.PATCH[-PRERELEASE]
    MAJOR: mudancas incompativeis
    MINOR: novas features compativeis
    PATCH: correcoes compativeis
    PRERELEASE: alpha, beta, rc seguidos de numero (ex.: -beta1, -rc2)
"""

__version__ = "0.1.1"