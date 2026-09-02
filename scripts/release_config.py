"""Constantes do canal de release desta ferramenta.

Um arquivo só, importado pelos scripts de publicação, porque o nome da
vitrine aparece em quatro lugares diferentes (latest.json, URL de download,
card do Chat, publicação do manifesto) e escrito quatro vezes ele diverge na
primeira renomeação de repositório.

⚠ A vitrine é PÚBLICA e não tem workflow nem secret. Quem publica nela é a CI
deste repositório privado, com o `RELEASE_REPO_TOKEN` (fine-grained PAT com
`Contents: Read and write` APENAS nela).

Até 2026-09-02 o NebulaTIR não tinha vitrine: a distribuição saía só pelo
pacote da ToolBox. O pacote passa a ser apenas a porta de entrada, e cada
ferramenta se atualiza pelo próprio canal.
"""

# Nome legível — aparece no card do Chat e no latest.json.
FERRAMENTA = "NebulaTIR"

# Nome do executável dentro do zip. O atualizador usa isto para saber o que
# extrair e o que trocar.
EXE = "NebulaTIR.exe"

# Prefixo do zip da release: <PREFIXO_ZIP>_<tag>.zip
PREFIXO_ZIP = "NebulaTIR"

# Repositório público que serve o download e hospeda o latest.json.
VITRINE = "vromarquestester/NebulaTIR-Releases"

# Branch da vitrine onde o latest.json vive. É a URL que o programa lê:
#   https://raw.githubusercontent.com/<VITRINE>/<BRANCH_VITRINE>/latest.json
BRANCH_VITRINE = "main"

# Versão mínima que o atualizador aceita atualizar.
#
# ⚠ REGRA: este valor NUNCA pode ser maior que a versão contida no pacote de
# entrada vigente (o `ToolBox_*.zip` que todo usuário baixa antes de atualizar).
# Elevá-lo acima disso quebra a porta de entrada em silêncio: a pessoa instala
# o pacote e o atualizador recusa atualizá-la. Para elevar, publique um pacote
# novo primeiro.
MINIMA_SUPORTADA = "0.0.0"
