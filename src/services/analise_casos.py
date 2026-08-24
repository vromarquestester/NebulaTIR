"""Decide se os casos de uma rotina podem rodar em instâncias separadas.

Dividir o suite é seguro quando cada caso abre e fecha o próprio ciclo: liga o
parâmetro que precisa, faz o que tem de fazer, desliga no fim. É o padrão dos
scripts — os dados vêm da base congelada, cadastrados antes.

A exceção existe: um caso que **cria** o registro que o seguinte consome. É
raro, mas é possível em outro país ou outra equipe, e dividir nesse caso
produz falha que parece defeito do produto. Este módulo procura esse laço.

Dois sinais, do mais forte ao mais fraco:

1. **Estado compartilhado na classe.** Um caso escreve `self.algo` e outro lê.
   É prova, não indício: o `unittest` cria uma instância por caso, então isso
   só funciona se os dois rodarem no mesmo processo, na ordem certa.

2. **Dado criado e reaproveitado.** Um caso que inclui registro (`SetButton`
   com "Incluir") usa um valor literal que reaparece num caso posterior. É
   indício — o valor pode ser um cadastro prévio da base —, então a decisão é
   conservadora: na dúvida, não divide.

O resultado **não** é um sim ou não para a rotina inteira: é uma partição. Os
casos ligados por dependência formam um grupo, que roda inteiro e em ordem na
**mesma** instância — assim o banco já chega com o dado que o anterior criou.
Os independentes viram grupos de um e se espalham. Uma rotina com uma cadeia
de dois e mais quatro casos soltos vira cinco unidades, não uma.

Na dúvida, junta. Rodar junto custa tempo; rodar separado o que dependia custa
um resultado errado, que é pior.
"""

from __future__ import annotations

import ast
import logging
import re
from pathlib import Path

log = logging.getLogger(__name__)

# Botões que criam registro. O caso que inclui é o candidato a "produtor".
_ROTULOS_INCLUSAO = ("incluir", "incluír", "nova", "novo", "adicionar")

# Formato de chave de negócio. Rodado contra os scripts reais, comparar
# qualquer literal acusava dependência onde não havia: "Normal", "Crédito" e
# "0001" aparecem em metade dos casos por serem vocabulário de domínio, não
# dado criado. Duas formas passam:
#   letras + dígitos, 5+  → P23276, CLI9001, REM001
#   só dígitos,      10+  → 1705202500003 (número de documento)
# Ficam de fora palavra pura e número curto, que são tipo, filial ou sequência.
_RE_CHAVE = re.compile(r"^(?=.*[A-Za-z])(?=.*\d)[\w./-]{5,}$")
_RE_NUMERO_LONGO = re.compile(r"^\d{10,}$")


def _parece_chave(texto: str) -> bool:
    return bool(_RE_CHAVE.match(texto) or _RE_NUMERO_LONGO.match(texto))
_IGNORADOS = {
    "incluir", "alterar", "excluir", "visualizar", "cancelar", "confirmar",
    "fechar", "outras ações", "ok", "sim", "não", "no", "yes", "salvar",
    "gravar", "pesquisar", "true", "false", "none",
}


class Analise:
    """Resultado da leitura de um arquivo de casos de teste."""

    def __init__(self, divisivel: bool, motivo: str = "",
                 grupos: list | None = None):
        self.divisivel = divisivel
        self.motivo = motivo
        # Casos que precisam ficar juntos, na ordem. Quando divisível, cada
        # caso vira um grupo de um.
        self.grupos = grupos or []

    def como_dict(self) -> dict:
        return {"divisivel": self.divisivel, "motivo": self.motivo,
                "grupos": self.grupos}


def _metodos_de_teste(arvore: ast.AST) -> dict:
    metodos = {}
    for classe in ast.walk(arvore):
        if not isinstance(classe, ast.ClassDef):
            continue
        for item in classe.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                    and item.name.startswith("test"):
                metodos[item.name] = item
    return metodos


def _atributos(no: ast.AST) -> tuple[set, set]:
    """Atributos de `self`/`cls` escritos e lidos dentro do método."""
    escritos, lidos = set(), set()
    for filho in ast.walk(no):
        if isinstance(filho, ast.Attribute) and isinstance(filho.value, ast.Name) \
                and filho.value.id in ("self", "cls", "inst"):
            if isinstance(filho.ctx, ast.Store):
                escritos.add(filho.attr)
            else:
                lidos.add(filho.attr)
    return escritos, lidos


# Onde mora o VALOR em cada chamada do TIR. `SetValue("A1_COD", "CLI9001")`
# tem o campo na posição 0 e o dado na 1 — comparar a posição 0 acusaria
# dependência entre quaisquer dois casos que mexem no mesmo campo.
_POSICAO_DO_VALOR = {
    "SetValue": 1,
    "ClickBox": 1,
    "ClickGridCell": 1,
    "SearchBrowse": 0,
    "ClickFolder": None,
}


def _literais(no: ast.AST) -> set:
    """Valores de negócio escritos no caso — só a posição de valor.

    Nome de campo, rótulo de botão e título de tela ficam de fora: eles se
    repetem entre casos por natureza e não dizem nada sobre dependência.
    """
    achados = set()
    for filho in ast.walk(no):
        if not isinstance(filho, ast.Call):
            continue
        posicao = _POSICAO_DO_VALOR.get(getattr(filho.func, "attr", ""))
        if posicao is None or posicao >= len(filho.args):
            continue
        arg = filho.args[posicao]
        if not isinstance(arg, ast.Constant) or not isinstance(arg.value, str):
            continue
        texto = arg.value.strip()
        if texto.casefold() in _IGNORADOS:
            continue
        if _parece_chave(texto):
            achados.add(texto)
    return achados


def _inclui_registro(no: ast.AST) -> bool:
    for filho in ast.walk(no):
        if not isinstance(filho, ast.Call):
            continue
        alvo = getattr(filho.func, "attr", "")
        if alvo not in ("SetButton", "ClickMenuFunctional", "ClickIcon"):
            continue
        for arg in filho.args:
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str) \
                    and arg.value.strip().casefold() in _ROTULOS_INCLUSAO:
                return True
    return False


def analisar(case_path: str | Path, casos: list[str]) -> Analise:
    """Diz se `casos` podem ser distribuídos entre instâncias.

    `casos` vem do TESTSUITE, na ordem em que ele os executa — a ordem importa
    justamente porque a dependência, quando existe, é do anterior para o
    seguinte.
    """
    caminho = Path(case_path)
    ordenados = [c for c in (casos or [])]
    if len(ordenados) < 2:
        return Analise(False, "Rotina com um caso só — nada a dividir.",
                       [list(ordenados)])

    try:
        fonte = caminho.read_text(encoding="utf-8", errors="replace")
        arvore = ast.parse(fonte)
    except (OSError, SyntaxError) as e:
        return Analise(False, f"Não consegui ler o arquivo de casos ({e}).",
                       [list(ordenados)])

    metodos = _metodos_de_teste(arvore)
    presentes = [c for c in ordenados if c in metodos]
    if len(presentes) != len(ordenados):
        faltando = [c for c in ordenados if c not in metodos]
        return Analise(False,
                       "O suite executa casos que não estão no arquivo "
                       f"({', '.join(faltando[:3])}) — não dá para analisar.",
                       [list(ordenados)])

    escritos, lidos, literais, produtores = {}, {}, {}, set()
    for nome in ordenados:
        e, l = _atributos(metodos[nome])
        escritos[nome], lidos[nome] = e, l
        literais[nome] = _literais(metodos[nome])
        if _inclui_registro(metodos[nome]):
            produtores.add(nome)

    # Liga o caso ao anterior de quem ele depende. A dependência é sempre do
    # anterior para o seguinte: é o anterior que cria o dado.
    ligacoes, motivos = {}, []
    for i, atual in enumerate(ordenados):
        for anterior in reversed(ordenados[:i]):
            # 1) Estado compartilhado na classe: prova.
            comuns = escritos[anterior] & lidos[atual]
            if comuns:
                ligacoes[atual] = anterior
                motivos.append(f"{atual} lê `self.{sorted(comuns)[0]}` definido "
                               f"por {anterior}")
                break
            # 2) Dado incluído por um e reaproveitado pelo outro: indício.
            if anterior in produtores:
                comuns = literais[anterior] & literais[atual]
                if comuns:
                    ligacoes[atual] = anterior
                    motivos.append(f"{anterior} inclui “{sorted(comuns)[0]}”, "
                                   f"que reaparece em {atual}")
                    break

    grupos = _agrupar(ordenados, ligacoes)
    if len(grupos) == 1:
        return Analise(False,
                       "Todos os casos formam uma cadeia: "
                       + "; ".join(motivos[:2]),
                       grupos)

    if motivos:
        return Analise(
            True,
            f"{len(grupos)} unidades — casos dependentes ficam juntos na mesma "
            f"instância ({'; '.join(motivos[:2])}).",
            grupos)
    return Analise(True, "Cada caso abre e fecha o próprio ciclo.", grupos)


def _agrupar(ordenados: list, ligacoes: dict) -> list:
    """Junta em cadeias os casos ligados por dependência, mantendo a ordem.

    Quem depende roda **na mesma instância** e depois do produtor: assim o
    banco já chega com o dado que o caso anterior criou. Os demais viram
    grupos de um e se espalham entre as instâncias.
    """
    raiz = {}

    def achar(nome: str) -> str:
        while ligacoes.get(nome):
            nome = ligacoes[nome]
        return nome

    for nome in ordenados:
        raiz[nome] = achar(nome)

    grupos, vistos = [], set()
    for nome in ordenados:
        chefe = raiz[nome]
        if chefe in vistos:
            continue
        vistos.add(chefe)
        grupos.append([c for c in ordenados if raiz[c] == chefe])
    return grupos


def unidades(rotina: dict, ativo: bool) -> tuple[list, Analise | None]:
    """Como a rotina será distribuída: lista de listas de casos.

    Com a divisão desligada devolve uma unidade só, com todos os casos — o
    comportamento de sempre.
    """
    casos = list(rotina.get("casos") or [])
    if not ativo or len(casos) < 2:
        return [casos], None

    analise = analisar(rotina.get("case", ""), casos)
    if len(analise.grupos) == 1:
        log.info("[DIVISAO] %s fica inteira: %s", rotina.get("rotina"),
                 analise.motivo)
    else:
        log.info("[DIVISAO] %s em %d unidades: %s", rotina.get("rotina"),
                 len(analise.grupos), analise.motivo)
    return analise.grupos, analise
