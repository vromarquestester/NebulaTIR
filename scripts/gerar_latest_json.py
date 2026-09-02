"""Monta o `latest.json` — o canal que o programa instalado consulta.

Uso:
    uv run python scripts/gerar_latest_json.py --tag v1.2.3
        --zip dist/NebulaTIR_v1.2.3.zip --saida dist/latest.json

Por que um arquivo em vez da API de releases:

- **Sem o teto de 60 requisições/hora por IP.** A equipe inteira atrás de um
  NAT corporativo estoura esse limite; `raw.githubusercontent.com` não tem
  esse custo.
- Payload de ~1 KB em vez da lista de releases inteira.
- O `sha256` viaja junto. Sem certificado de code signing, o hash publicado
  pelo pipeline é a única prova de origem do binário — sem ele, atualização
  automática seria execução de binário arbitrário baixado da internet.

O arquivo é publicado no branch da vitrine por `scripts/publicar_latest_json.py`
e lido pelo programa em:

    https://raw.githubusercontent.com/<VITRINE>/<BRANCH>/latest.json

⚠ `raw` tem cache de CDN de alguns minutos. Publicar e o programa não ver na
hora é comportamento normal, não defeito.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "scripts"))

import release_config as cfg           # noqa: E402
from changelog import carregar         # noqa: E402

ESQUEMA = 1


def sha256(caminho: Path) -> str:
    h = hashlib.sha256()
    with caminho.open("rb") as f:
        for bloco in iter(lambda: f.read(1024 * 1024), b""):
            h.update(bloco)
    return h.hexdigest()


def url_download(tag: str, nome_zip: str) -> str:
    """URL determinística do anexo da release pública.

    Não depende de consultar a API: o formato é fixo, e a vitrine é pública,
    então o download não precisa de autenticação.
    """
    return f"https://github.com/{cfg.VITRINE}/releases/download/{tag}/{nome_zip}"


def canal_da_versao(versao: str) -> str:
    """Versão com sufixo (`-beta1`, `-rc2`) não é oferecida ao canal estável."""
    if "-" in versao:
        return "beta"
    return "estavel"


def montar(tag: str, caminho_zip: Path, changelog: list[str] | None = None) -> dict:
    versao = tag.lstrip("vV")
    if changelog is None:
        changelog = carregar()
    return {
        "esquema": ESQUEMA,
        "ferramenta": cfg.FERRAMENTA,
        "versao": versao,
        "canal": canal_da_versao(versao),
        "publicado_em": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "obrigatoria": False,
        "minima_suportada": cfg.MINIMA_SUPORTADA,
        "arquivo": {
            "nome": caminho_zip.name,
            "exe": cfg.EXE,
            "url": url_download(tag, caminho_zip.name),
            "sha256": sha256(caminho_zip),
            "tamanho": caminho_zip.stat().st_size,
        },
        "changelog": changelog,
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--tag", required=True, help="tag da release, ex.: v1.2.3")
    p.add_argument("--zip", dest="zip_", type=Path, required=True)
    p.add_argument("--saida", type=Path, default=RAIZ / "dist" / "latest.json")
    args = p.parse_args(argv)

    if not args.zip_.exists():
        print(f"[latest.json] zip nao encontrado: {args.zip_}", file=sys.stderr)
        return 1

    dados = montar(args.tag, args.zip_)
    args.saida.parent.mkdir(parents=True, exist_ok=True)
    args.saida.write_text(
        json.dumps(dados, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"[latest.json] {args.saida} — v{dados['versao']} "
          f"({dados['canal']}), {len(dados['changelog'])} mudanca(s), "
          f"sha256 {dados['arquivo']['sha256'][:12]}...")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
