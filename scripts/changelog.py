"""Leitura do `changelog_pendente.txt` — o que entra na release e no card.

O arquivo é um acumulador: quem fecha uma mudança escreve uma linha aqui, no
mesmo commit da mudança. Quando a versão é publicada, o texto vira o
`changelog` do `latest.json`, a seção "O que mudou" do corpo da release e o
card do Google Chat — e o arquivo **é zerado** pela própria CI.

Não guarda histórico. O histórico fica no corpo da release, que é permanente e
é onde a pessoa já está quando baixa.

Formato (texto simples, sem markdown):

    - Botao Banco/RPO executa os dois modos num clique.
    - Corrigido: a lampada acendia para ambiente de terceiro.

- linha começando com ``-`` é uma mudança;
- linha começando com ``#`` é comentário;
- linha em branco é ignorada;
- **qualquer outra linha é erro**, nunca omissão silenciosa. Texto solto que
  fosse ignorado sumiria do card sem ninguém notar, e a release já teria saído.

O card do Chat não aceita Markdown (só um punhado de tags HTML), e é por isso
que o arquivo é `.txt` puro e a conversão acontece no `para_html`.

Uso:
    uv run python scripts/changelog.py --validar
    uv run python scripts/changelog.py --corpo-release
    uv run python scripts/changelog.py --zerar
"""
from __future__ import annotations

import argparse
import html
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
ARQUIVO = RAIZ / "changelog_pendente.txt"

# Teto de itens no card. O limite documentado do Google Chat é de widgets (100
# por card), não de caracteres — e a lista inteira cabe em UM widget, então não
# é ele que aperta. Este corte existe para o leitor: card com trinta linhas
# ninguém lê. O que sobra continua inteiro no corpo da release.
MAX_ITENS_NO_CARD = 8


class ChangelogInvalido(ValueError):
    pass


def carregar(caminho: Path = ARQUIVO) -> list[str]:
    """Devolve as mudanças na ordem em que foram escritas."""
    caminho = Path(caminho)
    if not caminho.exists():
        return []

    itens: list[str] = []
    for numero, bruta in enumerate(caminho.read_text(encoding="utf-8").splitlines(), 1):
        linha = bruta.strip()
        if not linha or linha.startswith("#"):
            continue
        if not linha.startswith("-"):
            raise ChangelogInvalido(
                f"{caminho.name}, linha {numero}: {linha!r} não começa com '-'. "
                f"Toda mudança é uma linha iniciada por '-'; comentário começa "
                f"com '#'."
            )
        item = linha[1:].strip()
        if item:
            itens.append(item)
    return itens


def vazio(itens: list[str]) -> bool:
    return not itens


def para_html(itens: list[str], maximo: int = MAX_ITENS_NO_CARD) -> str:
    """Lista para o `textParagraph` do card.

    `<ul><li>` está entre as poucas tags que o card aceita. O texto é escapado
    porque vem de arquivo escrito à mão: um `<` solto quebraria o widget.
    """
    mostrados = itens[:maximo]
    corpo = "".join(f"<li>{html.escape(i)}</li>" for i in mostrados)
    sobra = len(itens) - len(mostrados)
    if sobra > 0:
        corpo += f"<li><i>e mais {sobra} — veja a release completa.</i></li>"
    return f"<ul>{corpo}</ul>"


def para_texto(itens: list[str]) -> str:
    """Bloco para o corpo da release. Markdown aqui vale — o GitHub renderiza."""
    if not itens:
        return ""
    return "\n".join(f"- {i}" for i in itens)


def zerar(caminho: Path = ARQUIVO) -> None:
    """Devolve o arquivo ao esqueleto: só o cabeçalho de instruções.

    Preserva as linhas `#` do topo e descarta o resto. Reescrever a partir de um
    modelo embutido aqui faria o cabeçalho viver em dois lugares, e o que fosse
    editado nas instruções se perderia a cada publicação.
    """
    caminho = Path(caminho)
    if not caminho.exists():
        return
    cabecalho: list[str] = []
    for linha in caminho.read_text(encoding="utf-8").splitlines():
        if linha.strip().startswith("#") or not linha.strip():
            cabecalho.append(linha)
            continue
        break
    while cabecalho and not cabecalho[-1].strip():
        cabecalho.pop()
    caminho.write_text("\n".join(cabecalho) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--arquivo", type=Path, default=ARQUIVO)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--validar", action="store_true")
    g.add_argument("--corpo-release", action="store_true")
    g.add_argument("--zerar", action="store_true")
    args = p.parse_args(argv)

    if args.zerar:
        zerar(args.arquivo)
        print(f"[changelog] {args.arquivo.name} zerado.")
        return 0

    try:
        itens = carregar(args.arquivo)
    except ChangelogInvalido as erro:
        print(f"[changelog] {erro}", file=sys.stderr)
        return 1

    if args.validar:
        if not itens:
            print(f"[changelog] {args.arquivo.name} sem mudanças — a release "
                  f"sai sem a seção 'O que mudou'.")
        else:
            print(f"[changelog] {len(itens)} mudança(s):")
            for i in itens:
                print(f"  - {i}")
        return 0

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass
    print(para_texto(itens))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
