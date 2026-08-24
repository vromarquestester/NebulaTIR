"""Junta as parciais de uma rotina dividida num relatório único.

Quando os casos de um suite são distribuídos entre instâncias, cada uma grava
um `.json` com os próprios registros (`nebula_run --parcial`). Aqui eles viram
**um** log e **um** PNG, iguais aos de uma execução inteira — quem lê o
relatório não precisa saber que a rotina foi dividida.

A ordem é a do suite, não a de chegada: a instância que terminou primeiro não
deve aparecer primeiro no relatório.

    python nebula_merge.py <pasta_das_parciais> --ordem test_A,test_B [--out DIR]
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import tir_report


def _ler(pasta: Path) -> list:
    parciais = []
    for arquivo in sorted(pasta.glob("*.json")):
        try:
            parciais.append(json.loads(arquivo.read_text(encoding="utf-8")))
        except (OSError, ValueError) as e:
            print(f"[nebula_merge] Ignorando {arquivo.name}: {e}",
                  file=sys.stderr)
    return parciais


def _data(valor: str, reserva: datetime) -> datetime:
    try:
        return datetime.fromisoformat(valor)
    except (TypeError, ValueError):
        return reserva


def juntar(pasta: Path, ordem: list, out_dir: str | None = None) -> Path | None:
    parciais = _ler(pasta)
    if not parciais:
        print("[nebula_merge] Nenhuma parcial encontrada.", file=sys.stderr)
        return None

    registros = [r for p in parciais for r in p.get("registros", [])]
    if not registros:
        print("[nebula_merge] Parciais sem registros.", file=sys.stderr)
        return None

    if ordem:
        posicao = {nome: i for i, nome in enumerate(ordem)}
        registros.sort(key=lambda r: posicao.get(r.get("nome", ""), 10**6))

    agora = datetime.now().astimezone()
    inicio = min(_data(p.get("inicio", ""), agora) for p in parciais)
    fim = max(_data(p.get("fim", ""), agora) for p in parciais)
    suite = parciais[0].get("suite", pasta.name)

    log = tir_report.gravar(suite, registros, inicio, fim, out_dir)
    if not log:
        return None
    print(f"[nebula_merge] Log unificado: {log}")

    try:
        from nebula_exporter import export_png
        from nebula_parser import parse_log
        relatorio = parse_log(str(log))
        if relatorio is not None:
            print(f"[nebula_merge] Relatorio: "
                  f"{export_png(relatorio, str(Path(log).parent))}")
    except Exception as e:      # relatório é acessório; o log já está salvo
        print(f"[nebula_merge] PNG nao gerado: {e}", file=sys.stderr)
    return Path(log)


def main() -> int:
    p = argparse.ArgumentParser(prog="nebula_merge")
    p.add_argument("pasta", help="Pasta com os .json parciais.")
    p.add_argument("--ordem", default="",
                   help="Casos na ordem do suite, separados por vírgula.")
    p.add_argument("--out", metavar="DIR", help="Pasta do log final.")
    args = p.parse_args()

    ordem = [c.strip() for c in args.ordem.split(",") if c.strip()]
    return 0 if juntar(Path(args.pasta), ordem, args.out) else 1


if __name__ == "__main__":
    sys.exit(main())
