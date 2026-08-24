# NebulaTIR

Interface para as execuções do **TIR**, que hoje acontecem só por linha de
comando. Uso interno.

Roda como aplicação de janela: Python + pywebview sobre o WebView2 do Edge, com
a interface em HTML/CSS/JS vanilla — sem build step, sem CDN, sem
`node_modules`.

## Depende do Gerenciador de Ambientes

**O NebulaTIR não funciona sozinho.** Ele é um braço do
[Gerenciador de Ambientes](https://github.com/vromarquestester/Gerenciador-de-Ambientes),
na relação relógio ↔ telefone: o Gerenciador funciona sozinho, este não.

A dependência é deliberada, não uma etapa pendente. Cadastro de ambiente,
clonagem, geração do `appserver.ini`, validação de porta e checagem de VPN já
existem lá e são maduras. Reimplementar aqui criaria duas verdades sobre o mesmo
ambiente, e a divergência apareceria no pior momento — com um ambiente subindo
na porta errada.

A conversa acontece por um **canal loopback**: o Gerenciador sobe um servidor
HTTP em `127.0.0.1`, numa porta efêmera, e publica porta e token em
`%LOCALAPPDATA%/GerenciadorAmbientes/bridge.json`. O NebulaTIR relê esse arquivo
a cada ciclo.

Consequência prática: **com o Gerenciador fechado, o NebulaTIR aparece como
offline, e está certo** — não há canal para encontrar.

## Requisitos

- Windows com **WebView2** (já presente em instalações atuais do Windows 10/11).
- **Python 3.12**, só via [`uv`](https://docs.astral.sh/uv/). Este projeto não
  assume `python` no PATH.
- O Gerenciador de Ambientes rodando, para o canal existir.
- O TIR exige Python **3.12** — não roda em versão mais nova. O NebulaTIR em si
  roda em versões posteriores, mas a venv do TIR não.

## Rodando do código-fonte

```bash
uv venv --python 3.12
uv pip install -r requirements.txt
uv run python main_web.py
```

Para abrir o devtools do WebView2:

```bash
NEBULA_DEBUG=1 uv run python main_web.py
```

## Testes

```bash
uv run pytest
```

Os testes que mexem com subida de processo encurtam as esperas por fixture
(`sem_espera`, em `tests/test_appservers.py`) e usam dublês no lugar dos
binários do Protheus. Nenhum depende de porta de verdade nem de ambiente no ar.

## Build

```bash
uv run pyinstaller NebulaTIR.spec --noconfirm
```

Saída: `dist/NebulaTIR.exe`, arquivo único. Detalhes, e o que entra e o que
fica de fora do pacote, em [`docs/BUILD.md`](docs/BUILD.md).

## Versão e release

`src/_version.py` é a fonte única. Nunca editar à mão:

```bash
uv run python scripts/bump_version.py patch     # ou minor, major, prerelease
```

O script regenera o `version_info.txt` (metadados do `.exe`) e imprime os
próximos passos. A tag `vX.Y.Z` dispara o workflow que compila e publica a
release com o zip.

> O workflow de release **não roda os testes** — valida a versão e compila.
> Confira se o workflow `Tests` está verde antes de criar a tag.

## Configuração local

`config/` **não é versionado nem empacotado**: `ambientes_importados.json` e
`preferencias.json` nascem ao lado do executável na primeira execução.

Cada pasta onde o `.exe` estiver é uma instalação separada. Mover o executável
sozinho para outro lugar começa do zero.

## Aviso do Windows na primeira execução

O executável **não é assinado** (Authenticode). O `version_info.txt` preenche o
painel *Propriedades → Detalhes*, mas não substitui assinatura: o SmartScreen
vai alertar sobre editor desconhecido. Para executar: *Mais informações →
Executar assim mesmo*.

## Estrutura

```
main_web.py                        janela pywebview
src/
  webui/api.py                     js_api — tudo que o JS chama
  webui/log_bridge.py              logging → fila → console da UI
  webui/web/                       index.html, styles.css, app.js
  services/gerenciador_client.py   canal com o Gerenciador
  services/appservers.py           sobe os AppServer das instâncias
  services/paralelos.py            divisão dos casos entre instâncias
  services/tir/                    lançador e relatório do TIR
docs/ARQUITETURA.md                decisões de desenho e o canal em detalhe
docs/BUILD.md                      empacotamento e release
```

## Não confundir

**LogNebula** é outro projeto. O código de relatório do TIR foi incorporado aqui
em `services/tir/`, mas são repositórios distintos.
