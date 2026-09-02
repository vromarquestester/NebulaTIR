"""Avisa no Google Chat que saiu uma versão nova desta ferramenta.

Uso:
    GCHAT_WEBHOOK_URL=... uv run python scripts/notificar_chat.py --tag v1.2.3

    uv run python scripts/notificar_chat.py --tag v1.2.3 --simular
        # imprime o payload, nao envia

Sem `GCHAT_WEBHOOK_URL` no ambiente, não envia e **não falha**: a release já
saiu quando isto roda, e aviso é acessório — derrubar o pipeline por causa de
um webhook faria perder uma publicação boa.

Publicar release na vitrine não dispara nada por si: a vitrine é um depósito,
sem workflow. Quem avisa é quem publica, e por isso este passo vive aqui.

⚠ O card do Google Chat aceita **100 widgets**, e uma seção que estoure esse
total é descartada junto com todas as seguintes — em silêncio, com HTTP 200 na
resposta. Por isso o changelog inteiro sai em UM `textParagraph`, com a lista
em `<ul><li>` dentro, em vez de um widget por linha. O card fica em ~4 widgets
para sempre. Markdown não vale aqui; só um punhado de tags HTML.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "scripts"))

import release_config as cfg                        # noqa: E402
from changelog import carregar, para_html, vazio    # noqa: E402


def url_download(tag: str) -> str:
    return (f"https://github.com/{cfg.VITRINE}/releases/download/"
            f"{tag}/{cfg.PREFIXO_ZIP}_{tag}.zip")


def montar_payload(tag: str, itens: list[str] | None = None) -> dict:
    versao = tag.lstrip("vV")
    corpo = [{
        "widgets": [
            {"textParagraph": {
                "text": f"Nova versão do {cfg.FERRAMENTA}. Quem já tem o "
                        f"programa instalado recebe esta atualização pelo "
                        f"próprio {cfg.FERRAMENTA} — o link abaixo é para "
                        f"quem for instalar do zero."}},
            {"buttonList": {"buttons": [{
                "text": f"⬇️ Baixar {tag}",
                "onClick": {"openLink": {"url": url_download(tag)}},
            }]}},
        ]
    }]
    # Sem changelog o card sai sem a seção, em vez de exibir "O que mudou" vazio.
    if itens and not vazio(itens):
        corpo.append({
            "header": "O que mudou",
            "collapsible": False,
            "widgets": [{"textParagraph": {"text": para_html(itens)}}],
        })

    return {
        "cardsV2": [{
            "cardId": f"{cfg.PREFIXO_ZIP.lower()}-{versao}",
            "card": {
                "header": {
                    "title": "🚀 Nova versão disponível!",
                    "subtitle": f"{cfg.FERRAMENTA} — {tag}",
                    "imageUrl": "https://github.githubassets.com/images/"
                                "modules/logos_page/GitHub-Mark.png",
                    "imageType": "CIRCLE",
                },
                "sections": corpo,
            },
        }]
    }


def enviar(webhook: str, payload: dict) -> bool:
    req = urllib.request.Request(
        webhook,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=UTF-8"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            print(f"[chat] enviado (HTTP {r.status}).")
            return True
    except (urllib.error.URLError, TimeoutError) as e:
        print(f"[chat] falhou: {e}", file=sys.stderr)
        return False


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--tag", required=True, help="tag da release, ex.: v1.2.3")
    p.add_argument("--simular", action="store_true",
                   help="imprime o payload em vez de enviar")
    p.add_argument("--changelog", type=Path,
                   default=RAIZ / "changelog_pendente.txt")
    args = p.parse_args(argv)

    itens = carregar(args.changelog)
    if vazio(itens):
        print(f"[chat] {args.changelog.name} sem mudanças — card vai sem "
              f"a seção do changelog.")

    payload = montar_payload(args.tag, itens)

    if args.simular:
        # O console do Windows costuma vir em cp1252, que não codifica emoji.
        # Sem isto o `--simular` quebra na própria impressão, mesmo com o
        # payload correto.
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except (AttributeError, OSError):
            pass
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    webhook = os.environ.get("GCHAT_WEBHOOK_URL", "").strip()
    if not webhook:
        print("[chat] GCHAT_WEBHOOK_URL ausente — nada enviado.")
        return 0

    enviar(webhook, payload)
    return 0        # nunca derruba o pipeline: a release ja saiu


if __name__ == "__main__":
    raise SystemExit(main())
