"""As ferramentas que se atualizam juntas.

Gerenciador de Ambientes, NebulaTIR e o que vier depois vivem na mesma pasta e
são distribuídos pelo mesmo desenho: um repositório privado com a CI, uma
vitrine pública com o `latest.json`. Este catálogo é o que faz cada uma delas
conhecer as outras.

A regra que ele sustenta: **quem verifica atualização verifica para todas.**
Uma ferramenta aberta confere o manifesto das irmãs instaladas ao lado, e o
que estiver desatualizado é baixado em espera (`update/<exe>.new`). A troca do
binário continua sendo de cada uma, na própria partida — ninguém mexe no
executável que o outro programa está rodando.

Este arquivo é **cópia idêntica** em cada ferramenta, como o
`services/atualizacao.py`. Ferramenta nova entra assim:

1. copia `atualizacao.py` e `familia.py`;
2. acrescenta a linha dela em `FERRAMENTAS`, aqui e nas cópias das outras;
3. publica release das outras, para elas passarem a conhecê-la.

Até a release do passo 3 a assimetria é aceitável: a ferramenta nova já
atualiza as antigas; as antigas ainda não sabem dela.

⚠ Vitrine pública, sem segredo: o `latest.json` é lido de `raw`, que não tem o
teto de 60 requisições/hora da API. Ver `services/canal.py`.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Ferramenta:
    """Uma ferramenta da família: nome legível, executável e vitrine."""

    nome: str
    exe: str
    vitrine: str

    @property
    def url_manifesto(self) -> str:
        return f"https://raw.githubusercontent.com/{self.vitrine}/main/latest.json"


FERRAMENTAS: tuple[Ferramenta, ...] = (
    Ferramenta("Gerenciador de Ambientes", "GerenciadorAmbientes.exe",
               "vromarquestester/Gerenciador-de-Ambientes-Releases"),
    Ferramenta("NebulaTIR", "NebulaTIR.exe",
               "vromarquestester/NebulaTIR-Releases"),
    # Cypher entra quando sair da fase de testes — decisão de 2026-09-14.
)


def por_exe(nome_exe: str) -> Ferramenta | None:
    """A ferramenta cujo executável tem este nome (sem diferenciar caixa)."""
    alvo = (nome_exe or "").lower()
    for ferramenta in FERRAMENTAS:
        if ferramenta.exe.lower() == alvo:
            return ferramenta
    return None


def irmas(nome_exe: str) -> tuple[Ferramenta, ...]:
    """Todas as ferramentas menos a que tem este executável."""
    alvo = (nome_exe or "").lower()
    return tuple(f for f in FERRAMENTAS if f.exe.lower() != alvo)
