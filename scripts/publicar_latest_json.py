"""Grava o `latest.json` no branch da vitrine pública (Contents API).

Uso:
    RELEASE_REPO_TOKEN=... uv run python scripts/publicar_latest_json.py
        --arquivo dist/latest.json --tag v1.2.3

Precisa do PAT fine-grained com `Contents: Read and write` APENAS na vitrine.
O `GITHUB_TOKEN` padrão do Actions não serve: ele só vale neste repositório.

Faz um PUT em `/repos/<vitrine>/contents/latest.json`. Se o arquivo já existe,
manda o `sha` do blob atual — sem ele o GitHub recusa a sobrescrita. É o
controle de concorrência da API, e é ele que impede duas publicações
simultâneas de se atropelarem em silêncio.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "scripts"))

import release_config as cfg           # noqa: E402

API = "https://api.github.com"
CAMINHO_REMOTO = "latest.json"


def _requisitar(url: str, token: str, metodo: str = "GET", corpo: dict | None = None):
    dados = None
    if corpo is not None:
        dados = json.dumps(corpo).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=dados,
        method=metodo,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
            "User-Agent": f"{cfg.FERRAMENTA}-release",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8") or "{}")


def sha_atual(token: str) -> str | None:
    """`sha` do blob que já está lá, ou None se o arquivo ainda não existe."""
    url = (f"{API}/repos/{cfg.VITRINE}/contents/{CAMINHO_REMOTO}"
           f"?ref={cfg.BRANCH_VITRINE}")
    try:
        return _requisitar(url, token).get("sha")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def publicar(arquivo: Path, tag: str, token: str) -> None:
    conteudo = base64.b64encode(arquivo.read_bytes()).decode("ascii")
    corpo = {
        "message": f"chore: latest.json -> {tag}",
        "content": conteudo,
        "branch": cfg.BRANCH_VITRINE,
    }
    sha = sha_atual(token)
    if sha:
        corpo["sha"] = sha

    url = f"{API}/repos/{cfg.VITRINE}/contents/{CAMINHO_REMOTO}"
    _requisitar(url, token, metodo="PUT", corpo=corpo)
    estado = "atualizado" if sha else "criado"
    print(f"[latest.json] publicado em {cfg.VITRINE}@{cfg.BRANCH_VITRINE} ({estado}).")
    print(f"[latest.json] https://raw.githubusercontent.com/{cfg.VITRINE}/"
          f"{cfg.BRANCH_VITRINE}/{CAMINHO_REMOTO}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--arquivo", type=Path, default=RAIZ / "dist" / "latest.json")
    p.add_argument("--tag", required=True)
    args = p.parse_args(argv)

    token = os.environ.get("RELEASE_REPO_TOKEN", "").strip()
    if not token:
        print("[latest.json] RELEASE_REPO_TOKEN ausente — nada publicado.",
              file=sys.stderr)
        return 1
    if not args.arquivo.exists():
        print(f"[latest.json] arquivo nao encontrado: {args.arquivo}",
              file=sys.stderr)
        return 1

    try:
        publicar(args.arquivo, args.tag, token)
    except urllib.error.HTTPError as e:
        detalhe = e.read().decode("utf-8", "replace")[:400]
        # 401 e token invalido, revogado ou colado incompleto — regenerar um PAT
        # invalida o valor anterior na hora, e o secret segue guardando o velho.
        # 403 e 404 sao escopo errado no PAT, ou a vitrine nao selecionada nele.
        print(f"[latest.json] HTTP {e.code} ao publicar: {detalhe}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
