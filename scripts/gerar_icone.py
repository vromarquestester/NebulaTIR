"""Gera `resources/app.ico` com a mesma marca da topbar.

O desenho é o do `index.html`: um núcleo sólido com um anel elíptico inclinado
— violeta sobre o grafite do aplicativo. Feito por código para o ícone da
janela e o da barra de tarefas nunca divergirem da marca da interface.

    uv run python scripts/gerar_icone.py

Pillow é dependência **só desta geração** — está nos `excludes` do `.spec` e
não vai para o executável. O `.ico` fica versionado.
"""

from pathlib import Path

from PIL import Image, ImageDraw

RAIZ = Path(__file__).resolve().parent.parent
DESTINO = RAIZ / "resources" / "app.ico"

# Mesmos tokens do styles.css: --primary 265 70% 62% e --bg 265 22% 5%.
VIOLETA = (150, 92, 226, 255)
VIOLETA_SUAVE = (150, 92, 226, 190)
FUNDO = (13, 10, 20, 255)

TAMANHOS = [256, 128, 64, 48, 32, 16]
ESCALA = 8          # desenha grande e reduz: bordas suaves sem antialias manual


def desenhar(lado: int) -> Image.Image:
    grande = lado * ESCALA
    img = Image.new("RGBA", (grande, grande), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # Disco de fundo arredondado, para o ícone ter forma própria na barra.
    d.ellipse([0, 0, grande - 1, grande - 1], fill=FUNDO)

    centro = grande / 2
    # Anel elíptico inclinado −24°, como no SVG da topbar.
    anel = Image.new("RGBA", (grande, grande), (0, 0, 0, 0))
    da = ImageDraw.Draw(anel)
    rx, ry = grande * 0.42, grande * 0.18
    da.ellipse([centro - rx, centro - ry, centro + rx, centro + ry],
               outline=VIOLETA_SUAVE, width=max(2, int(grande * 0.045)))
    img.alpha_composite(anel.rotate(24, resample=Image.BICUBIC, center=(centro, centro)))

    # Núcleo por cima do anel, como no desenho original.
    r = grande * 0.19
    d.ellipse([centro - r, centro - r, centro + r, centro + r], fill=VIOLETA)

    return img.resize((lado, lado), Image.LANCZOS)


def main() -> None:
    DESTINO.parent.mkdir(parents=True, exist_ok=True)
    imagens = [desenhar(lado) for lado in TAMANHOS]
    # O primeiro carrega os demais tamanhos embutidos; o Windows escolhe o
    # certo para a janela (16/32) e para a barra de tarefas (32/48).
    imagens[0].save(DESTINO, format="ICO",
                    sizes=[(lado, lado) for lado in TAMANHOS])
    print(f"Ícone gerado: {DESTINO} ({DESTINO.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
