# NebulaTIR — arquitetura

## O que é

Interface para as execuções do TIR, que hoje acontecem só por linha de comando.

O NebulaTIR é um **braço** do Gerenciador de Ambientes
(`C:\Dev\Projetos\projeto_atualiza_banco`), na relação Apple Watch ↔ iPhone:

- o Gerenciador funciona sozinho;
- o NebulaTIR **não funciona** sem ele.

A dependência é deliberada. Cadastro de ambiente, clonagem (`services/clone.py`),
geração do `appserver.ini` (`services/appserver_ini.py`), validação de porta e
checagem de VPN já existem no Gerenciador e são maduras. Reimplementar aqui
criaria duas verdades sobre o mesmo ambiente — e a divergência apareceria no pior
momento, com um ambiente subindo com a porta errada.

## Stack

Espelha o Gerenciador: **Python + pywebview (WebView2) + HTML/CSS/JS vanilla**.
Sem build step, sem CDN, sem `node_modules`. A ponte JS↔Python é o
`window.pywebview.api`, ligado ao objeto `webui.api.Api`.

```
main_web.py                     janela pywebview
src/
  webui/api.py                  js_api — tudo que o JS chama
  webui/log_bridge.py           logging → fila → console da UI
  webui/web/                    index.html, styles.css, app.js
  services/gerenciador_client.py  canal com o Gerenciador
  services/importados.py        persistência local (só nomes)
config/ambientes_importados.json
```

## O canal com o Gerenciador

O Gerenciador não expunha nada: a UI dele roda em `file://` e a ponte é
in-process. Foi adicionado lá um servidor loopback,
`src/webui/bridge_server.py`, que sobe junto com a `Api`.

```
Gerenciador                                   NebulaTIR
  Api.__init__
    └─ bridge_server.iniciar()                  EstadoGerenciador (thread 2 s)
         ├─ ThreadingHTTPServer 127.0.0.1:0  ←──── GET /health
         └─ escreve bridge.json              ←──── GET /ambientes
              %LOCALAPPDATA%/GerenciadorAmbientes/
```

| Rota | Devolve |
|---|---|
| `GET /health` | app, versão, pid, vpn, config_valida |
| `GET /ambientes` | ambientes sanitizados + estado de execução |
| `GET /ambientes/<i>` | detalhes do ambiente |

**Porta efêmera** (`bind` em 0), para nunca colidir com as portas do Protheus
(4321 / 8881 / 8080 / 21021). Porta e token vão para o `bridge.json`, que o
NebulaTIR relê a cada ciclo — cachear a porta deixaria o app falando com uma
porta morta depois que o Gerenciador reabrisse.

**Token** aleatório por sessão, no header `X-Nebula-Token`. O PID no registro
desempata o caso do arquivo órfão deixado por um crash: sem ele, o NebulaTIR
mandaria o token para qualquer processo que tivesse herdado aquela porta.

**Nenhuma senha atravessa o canal.** O payload passa por allowlist
(`CAMPOS_BANCO` / `CAMPOS_CONEXAO`); campo novo no INI não vaza por padrão.
Há teste travando isso dos dois lados.

## O gate

Regra única que governa a interface inteira:

```
link    = Gerenciador online          ← gate mestre
pronto  = link && conexão SQL válida
livre   = nenhuma operação em curso
```

Com `link === false` **tudo** desabilita, o corpo ganha `.sem-link` e o painel de
dependência aparece. O polling de 2 s reavalia a cada ciclo, então a queda do
Gerenciador no meio da sessão trava a interface sozinha, sem recarregar.

O backend não confia na UI: toda ação da `Api` revalida o link antes de agir.

## Decisões que não são óbvias

**VPN e SQL não são medidos aqui.** Vêm do `/health`. Medir de novo dobraria o
ping e poderia mostrar estados divergentes nas duas janelas abertas lado a lado.
Sem link, os chips vão para `—` — que é a verdade: o NebulaTIR não sabe.

**Importação guarda só o nome.** Porta, caminhos e status são lidos do
Gerenciador na hora do uso. Uma edição lá aparece aqui no ciclo seguinte, sem
sincronização manual. É também por isso que o cliente procura ambiente por nome,
nunca por índice: remover um ambiente no Gerenciador desloca os índices.

**Excluir não apaga nada.** Remove só a referência local — o ambiente continua
no Gerenciador. Um ambiente importado que sumir de lá é marcado como órfão na
lista, em vez de desaparecer em silêncio.

**Cor.** Layout e tokens são os do Gerenciador; a cor de marca desloca para
violeta. As duas janelas ficam abertas ao mesmo tempo o dia inteiro e precisam
ser distinguíveis num relance. `--ok` continua verde nos dois: "rodando" não
pode mudar de cor entre as janelas.

**`_pid_vivo` usa Win32, não `tasklist`.** O utilitário leva ~2 s nesta máquina
e a primeira leitura de estado é síncrona no boot — seria a janela abrindo
travada. O resultado ainda é cacheado por sessão do Gerenciador.

**Todo estado da `Api` é privado.** O pywebview varre `dir(js_api)` a cada load
e desce recursivamente em atributo público não-chamável; um atributo público
apontando para a Window trava o boot por minutos. Há teste travando a regra.

## Estado atual

Pronto: canal, descoberta, gate, importar/excluir/sincronizar, interface.

Pendente: execução do TIR, clonagem pelo NebulaTIR, edição do `appserver.ini`
daqui, rotas `POST` no bridge. A interface já reserva os lugares — o card TIR, o
botão `#btn-executar-tir` e o 4º slot do grid lateral (`#btn-slot-futuro`,
`hidden`).

## Rodar

```bash
uv run python main_web.py              # precisa do Gerenciador aberto
NEBULA_DEBUG=1 uv run python main_web.py   # devtools do WebView2
uv run pytest
```

Empacotamento em [BUILD.md](BUILD.md) — `uv run pyinstaller NebulaTIR.spec`.
