<#
.SYNOPSIS
  Anexa um arquivo a uma release do GitHub insistindo enquanto o upload falhar.

.DESCRIPTION
  Em 2026-09-17 o endpoint de upload de assets (uploads.github.com) passou a
  responder HTTP 500 de forma intermitente — "Error saving asset" e "Error
  creating asset temp dir" — para arquivos de ~15 MB, minuto sim, minuto não,
  enquanto arquivos de bytes passavam. Cinco tentativas seguidas da CI caíram
  nisso, e o mesmo upload feito à mão passou na segunda insistência.

  Nem a action de release nem o `gh` repetem em 5xx. Este script repete:
  `gh release upload --clobber` até dar certo, apagando o asset parcial que
  uma tentativa quebrada pode deixar para trás, com pausa entre tentativas.

  Cópia idêntica em todas as ferramentas da família (Gerenciador de
  Ambientes, NebulaTIR). Programa novo copia o script, não reescreve.

.PARAMETER Repo      dono/repositório, ex.: vromarquestester/NebulaTIR
.PARAMETER Tag       tag da release, ex.: v0.4.0
.PARAMETER Arquivo   caminho do arquivo a anexar
.PARAMETER Tentativas  quantas vezes insistir (padrão 10)
.PARAMETER PausaSeg  segundos entre tentativas (padrão 30)

  Requer GH_TOKEN no ambiente com escrita em Contents no repositório.
#>
param(
  [Parameter(Mandatory)] [string] $Repo,
  [Parameter(Mandatory)] [string] $Tag,
  [Parameter(Mandatory)] [string] $Arquivo,
  [int] $Tentativas = 10,
  [int] $PausaSeg = 30
)

$ErrorActionPreference = "Continue"

if (-not (Test-Path $Arquivo)) {
  Write-Error "Arquivo não encontrado: $Arquivo"
  exit 1
}
$nome = Split-Path $Arquivo -Leaf

for ($i = 1; $i -le $Tentativas; $i++) {
  Write-Host "[$Repo] $nome — tentativa $i de $Tentativas"
  gh release upload $Tag $Arquivo --repo $Repo --clobber 2>&1 | ForEach-Object { Write-Host "  $_" }
  if ($LASTEXITCODE -eq 0) {
    # `--clobber` já cobre o asset parcial; conferir que ele está lá inteiro
    # é o que separa "o gh não reclamou" de "o arquivo subiu".
    $tamanhoLocal = (Get-Item $Arquivo).Length
    $tamanhoRemoto = gh release view $Tag --repo $Repo --json assets `
      --jq ".assets[] | select(.name == `"$nome`") | .size"
    if ("$tamanhoRemoto" -eq "$tamanhoLocal") {
      Write-Host "[$Repo] $nome anexado ($tamanhoLocal bytes)."
      exit 0
    }
    Write-Host "[$Repo] tamanho remoto ($tamanhoRemoto) difere do local ($tamanhoLocal); repetindo."
  }
  if ($i -lt $Tentativas) {
    Write-Host "[$Repo] upload falhou; aguardando $PausaSeg s."
    Start-Sleep -Seconds $PausaSeg
  }
}

Write-Error "[$Repo] $nome não subiu em $Tentativas tentativas."
exit 1
