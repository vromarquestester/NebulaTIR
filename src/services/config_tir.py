"""Configuração do TIR por ambiente — o que vira `config.json`.

As **chaves são as do TIR** e não podem ser traduzidas: quem lê o arquivo é o
framework, não uma pessoa. Os rótulos em português vivem só na interface
(`CAMPOS`, consumido pelo `app.js`).

Referência das chaves: https://totvs.github.io/tir/configjson.html
`LogFolder` é a chave oficial para o diretório dos logs ("Folder to save log
files. Default: Script execution path").
"""

from __future__ import annotations

# Marcador do caso de teste no caminho padrão de log. A substituição real
# acontece na geração do `config.json`, quando o caso a executar for conhecido —
# hoje o NebulaTIR ainda não seleciona caso de teste.
MARCA_CASO = "<caso_de_teste>"
LOG_PADRAO = rf".\tests\{MARCA_CASO}\log"

# Os quatro que o TIR realmente conhece. A lista sai de `get_language_pack`
# em `tir/technologies/core/language.py`: qualquer outro valor cai no pacote
# padrão e o TIR passa a procurar botão em português numa tela em espanhol.
IDIOMAS = ["pt-BR", "es-ES", "en-US", "ru-RU"]

# Idioma por país do ambiente. É o campo que mais custou caro: um ambiente
# MEX rodava com `pt-BR`, o Protheus subia em português e o testcase — escrito
# em espanhol — falhava em todo `SetValue`, com "Element '¿Normal/Benef./
# Anticipo' not found!". Rodando fora do NebulaTIR o config era outro, e por
# isso o mesmo teste passava.
IDIOMA_POR_PAIS = {
    "brasil": "pt-BR", "bra": "pt-BR", "br": "pt-BR",
    "mexico": "es-ES", "mex": "es-ES",
    "argentina": "es-ES", "arg": "es-ES",
    "chile": "es-ES", "chi": "es-ES", "chl": "es-ES",
    "colombia": "es-ES", "col": "es-ES",
    "paraguai": "es-ES", "paraguay": "es-ES", "par": "es-ES", "pry": "es-ES",
    "uruguai": "es-ES", "uruguay": "es-ES", "uru": "es-ES", "ury": "es-ES",
    "peru": "es-ES", "per": "es-ES",
    "bolivia": "es-ES", "bol": "es-ES",
    "equador": "es-ES", "ecuador": "es-ES", "equ": "es-ES", "ecu": "es-ES",
    "costa rica": "es-ES", "cri": "es-ES", "cos": "es-ES",
    "eua": "en-US", "usa": "en-US", "estados unidos": "en-US", "ang": "en-US",
    "russia": "ru-RU", "rus": "ru-RU",
    "portugal": "pt-BR", "por": "pt-BR", "prt": "pt-BR",
}


def _sem_acento(texto: str) -> str:
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFD", texto or "")
                   if unicodedata.category(c) != "Mn")


def idioma_do_pais(pais: str) -> str:
    """Idioma do TIR para o país/localização do ambiente. Vazio se não souber.

    Aceita tanto a sigla da `localizacao` (`mex`) quanto o nome por extenso
    que a tabela do Gerenciador devolve (`México`).
    """
    chave = _sem_acento(str(pais or "")).strip().lower()
    return IDIOMA_POR_PAIS.get(chave, "")

# Ordem das chaves NO ARQUIVO gerado — `LogFolder` logo abaixo de `DebugLog`,
# que é o switch que liga aquele log. A ordem NA TELA é outra e mora em
# `CAMPOS`: lá o que manda é a simetria da grade de 2 colunas.
PADRAO = {
    "Url": "",
    "Browser": "",
    "Environment": "",
    "Language": "pt-BR",
    "User": "ADMIN",
    "Password": "1234",
    "Headless": True,
    # SEMPRE ligado, e travado. Regra dos ambientes que este programa atende:
    # eles sobem com a tela de entrada POUI, e sem esta chave o TIR procura o
    # campo de usuário do WebApp clássico, que não existe ali — a execução não
    # passa da tela de login. Verificado nos dois modos, no ambiente real.
    #
    # Ligada, `Setup()` fixa `poui_login = True` e o `driver_get` faz
    # `switch_to_iframe()` antes de procurar a tela: é esse caminho que
    # funciona aqui.
    "POUILogin": True,
    "DebugLog": True,
    "LogFolder": LOG_PADRAO,
    "TimeOut": 90,
    "CheckValue": True,
    "Coverage": False,
    # Chrome se atualiza sozinho e o driver que vem com o TIR fica para trás:
    # o erro é `session not created: This version of ChromeDriver only
    # supports Chrome version N` e derruba o setUpClass de todos os casos.
    # Com isto ligado o TIR baixa o driver da versão instalada.
    "ChromeDriverAutoInstall": True,
}

# Descrição de cada campo para a interface montar o formulário sozinha —
# assim rótulo, tipo e trava moram num lugar só, e não espalhados no HTML.
#   tipo:  texto | texto_fixo | combo | switch | numero | pasta
#   trava: valor imposto; o controle aparece travado e explicado
#   grupo: separa campo de identidade do ambiente de opção de execução; sem
#          isso a grade de 2 colunas pareia texto com switch e fica ilegível
#
# A ORDEM é a da tela, numa grade de 2 colunas, e é par de propósito: cada
# grupo fecha em linhas cheias. O único campo de linha inteira é a pasta dos
# logs, no fim — caminho não pode ser truncado, é o miolo dele que distingue
# um do outro.
CAMPOS = [
    # Identidade: o que o ambiente é, como ele abre, com quem entra.
    {"chave": "Url", "rotulo": "URL do ambiente", "tipo": "texto",
     "grupo": "Ambiente", "obrigatorio": True,
     "ajuda": "Vem do ambiente importado. Pode ser editada."},
    {"chave": "Environment", "rotulo": "Ambiente", "tipo": "texto_fixo",
     "grupo": "Ambiente", "obrigatorio": True,
     "ajuda": "Seção do appserver.ini, trazida na importação."},
    {"chave": "Browser", "rotulo": "Navegador", "tipo": "combo",
     "grupo": "Ambiente",
     "opcoes": [], "ajuda": "Navegadores instalados nesta máquina."},
    {"chave": "Language", "rotulo": "Idioma", "tipo": "combo",
     "grupo": "Ambiente", "opcoes": IDIOMAS},
    {"chave": "User", "rotulo": "Usuário", "tipo": "texto_fixo",
     "grupo": "Ambiente", "obrigatorio": True,
     "ajuda": "Fixo: o TIR entra sempre com este usuário."},
    {"chave": "Password", "rotulo": "Senha", "tipo": "texto_fixo",
     "grupo": "Ambiente", "obrigatorio": True},

    {"chave": "Headless", "rotulo": "Sem tela", "tipo": "switch",
     "grupo": "Execução",
     "ajuda": "Executa sem abrir a janela do navegador."},
    {"chave": "POUILogin", "rotulo": "Login POUI", "tipo": "switch",
     "grupo": "Execução", "trava": True,
     "ajuda": "Sempre ligado. Os ambientes atendidos aqui sobem com a tela de "
              "entrada POUI; desligado, o TIR procura o campo de usuário do "
              "WebApp clássico e a execução não passa do login."},
    {"chave": "DebugLog", "rotulo": "Log de depuração", "tipo": "switch",
     "grupo": "Execução", "trava": True},
    {"chave": "CheckValue", "rotulo": "Validar valores", "tipo": "switch",
     "grupo": "Execução"},
    {"chave": "TimeOut", "rotulo": "Tempo limite (s)", "tipo": "numero",
     "grupo": "Execução", "min": 1, "max": 3600, "obrigatorio": True,
     "ajuda": "Em segundos."},
    {"chave": "Coverage", "rotulo": "Cobertura de código", "tipo": "switch",
     "grupo": "Execução"},
    # Linha inteira: fecha o grupo com número par de campos de meia largura e
    # dá espaço para a explicação, que é longa e vale ser lida.
    {"chave": "ChromeDriverAutoInstall", "rotulo": "Baixar o ChromeDriver da "
                                                  "versão instalada",
     "tipo": "switch", "grupo": "Execução", "largo": True,
     "ajuda": "O Chrome se atualiza sozinho e o driver que acompanha o TIR "
              "fica para trás: o erro é “session not created — This version "
              "of ChromeDriver only supports Chrome version N” e derruba o "
              "setUpClass de todos os casos."},
    {"chave": "LogFolder", "rotulo": "Pasta dos logs", "tipo": "pasta",
     "grupo": "Execução", "obrigatorio": True, "largo": True,
     "ajuda": f"Padrão: {LOG_PADRAO} — relativo à pasta onde o executável roda. "
              f"{MARCA_CASO} é trocado pelo caso de teste na hora da execução."},
]

SWITCHES = [c["chave"] for c in CAMPOS if c["tipo"] == "switch"]
TRAVADOS = {c["chave"]: c["trava"] for c in CAMPOS if "trava" in c}
FIXOS = [c["chave"] for c in CAMPOS if c["tipo"] == "texto_fixo"]
OBRIGATORIOS = [c["chave"] for c in CAMPOS if c.get("obrigatorio")]


def padrao_para(*, url: str = "", ambiente_ini: str = "",
                navegador: str = "", pais: str = "") -> dict:
    """Configuração inicial de um ambiente recém-importado.

    O idioma vem do país do ambiente. Deixar `pt-BR` fixo aqui fazia todo
    ambiente de localização hispânica falhar em todo `SetValue`, porque o
    testcase procura o rótulo em espanhol numa tela que subiu em português.
    """
    config = dict(PADRAO)
    config["Url"] = url or PADRAO["Url"]
    config["Environment"] = ambiente_ini or PADRAO["Environment"]
    config["Browser"] = navegador or PADRAO["Browser"]
    config["Language"] = idioma_do_pais(pais) or PADRAO["Language"]
    return config


def normalizar(bruto: dict | None, base: dict | None = None) -> dict:
    """Aplica tipos, defaults e travas. Nunca confia no que veio da tela.

    Chave desconhecida é descartada: o `config.json` gerado tem que ser
    exatamente o contrato do TIR, sem sobra do que a UI mandou por engano.
    """
    bruto = bruto or {}
    config = dict(base or PADRAO)

    for chave in PADRAO:
        if chave not in bruto:
            continue
        valor = bruto[chave]
        if chave in SWITCHES:
            config[chave] = _para_bool(valor)
        elif chave == "TimeOut":
            config[chave] = _para_timeout(valor, config.get(chave, 90))
        else:
            config[chave] = str(valor if valor is not None else "").strip()

    # Campos fixos e travados são impostos aqui, não só desabilitados na tela:
    # a UI pode ser contornada, esta função não.
    config["User"] = PADRAO["User"]
    config["Password"] = PADRAO["Password"]
    for chave, valor in TRAVADOS.items():
        config[chave] = valor

    if config.get("Language") not in IDIOMAS:
        config["Language"] = PADRAO["Language"]

    return {chave: config[chave] for chave in PADRAO}


# Rótulo para a mensagem de campo obrigatório vazio.
_ROTULOS = {c["chave"]: c["rotulo"] for c in CAMPOS}


def validar(config: dict) -> str:
    """Mensagem de erro para a UI, ou string vazia quando está tudo certo.

    Campo de digitação é obrigatório: o `config.json` vai para o TIR, e chave
    vazia lá vira erro no meio da execução, longe da causa.
    """
    for chave in OBRIGATORIOS:
        if not str(config.get(chave) or "").strip():
            if chave == "Environment":
                return ("O ambiente não veio na importação — sincronize ou "
                        "importe o ambiente de novo.")
            return f"O campo “{_ROTULOS[chave]}” é obrigatório."
    if not str(config.get("Url")).startswith(("http://", "https://")):
        return "A URL precisa começar com http:// ou https://."
    if not str(config.get("Browser") or "").strip():
        return "Escolha um navegador."
    return ""


def _para_bool(valor) -> bool:
    if isinstance(valor, bool):
        return valor
    return str(valor).strip().lower() in ("1", "true", "on", "sim")


def _para_timeout(valor, atual: int) -> int:
    try:
        numero = int(str(valor).strip())
    except (TypeError, ValueError):
        return atual
    return max(1, min(numero, 3600))
