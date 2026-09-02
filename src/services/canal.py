"""Onde o programa procura versão nova.

Isto é o lado do CLIENTE do canal de release. O lado de quem publica está em
`scripts/release_config.py`, e os dois precisam concordar — a vitrine aqui é a
mesma que a CI escreve.

⚠ A vitrine é PÚBLICA e o download não pede autenticação. É o que permite não
haver segredo nenhum dentro do executável: chave embutida em binário
distribuído é legível por quem tiver o arquivo, e o token que lê release
privada é o mesmo que lê o código-fonte.

O NebulaTIR se atualiza sozinho, independente do Gerenciador. Os dois vivem na
mesma pasta e cada um troca só o próprio binário — um pode estar aberto
enquanto o outro se atualiza.
"""

# Nome do executável desta ferramenta. É o arquivo que a atualização troca.
NOME_EXE = "NebulaTIR.exe"

VITRINE = "vromarquestester/NebulaTIR-Releases"

# O manifesto é lido de `raw`, e não da API de releases, porque `raw` não tem
# o teto de 60 requisições/hora por IP — a equipe inteira atrás de um NAT
# corporativo estoura esse limite.
#
# ⚠ `raw` tem cache de CDN de alguns minutos: publicar e o programa não ver na
# hora é normal, não defeito.
URL_MANIFESTO = f"https://raw.githubusercontent.com/{VITRINE}/main/latest.json"
