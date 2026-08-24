# Build do NebulaTIR

```bash
uv venv --python 3.12
uv pip install -r requirements.txt
uv run pytest
uv run pyinstaller NebulaTIR.spec --noconfirm
```

Saída: `dist/NebulaTIR.exe` (~13 MB, arquivo único).

## O que vai dentro

| Item | Origem |
|---|---|
| Interface (HTML/CSS/JS) | `src/webui/web` → `sys._MEIPASS/webui/web` |
| Backend do WebView2 | `collect_all('webview')` + `clr_loader`, `pythonnet` |
| Versão do executável | `version_info.txt`, **regerado** pelo `.spec` a partir de `src/_version.py` |

`tkinter`, `PIL`, `numpy`, `matplotlib`, `pyodbc` e `pytest` são excluídos: o
NebulaTIR é HTML no WebView2 e biblioteca padrão. Sem isso o executável
carregaria dezenas de MB que nunca são usados.

## O que NÃO vai dentro

**`config/` não é empacotado.** `ambientes_importados.json` e
`preferencias.json` nascem ao lado do executável na primeira execução —
`services/importados.py` e `services/preferencias.py` resolvem `BASE_DIR` por
`sys.executable` quando congelado. Distribuir o `config/` junto levaria os
ambientes importados de outra máquina.

Consequência prática: cada pasta onde o `.exe` estiver é uma instalação. Mover
o executável sozinho para outro lugar começa do zero.

## Diferenças em relação ao Gerenciador de Ambientes

- **Sem `uac_admin`.** O NebulaTIR não eleva. Ele não sobe processo do Protheus
  nem mexe em serviço; pedir administrador à toa treina o usuário a aceitar
  elevação no automático.
- **Sem `resources/protheus_base`.** Provisionar ambiente é trabalho do
  Gerenciador.
- **Ícone opcional.** Não há `resources/app.ico` ainda, e `caminho_icone()`
  devolve `None` sem quebrar — o `.exe` sai com o ícone padrão do PyInstaller.
  Basta colocar o arquivo em `resources/app.ico` para o `.spec` incluí-lo.

## Rodar o Gerenciador do código-fonte sem console

O `pythonw.exe` de uma venv criada pelo `uv` é um **trampolim**: ele cria o
interpretador real como processo filho e, sendo aplicação de console, nasce
com um `conhost` visível. Iniciar com a janela oculta resolve:

```powershell
Start-Process -FilePath "C:\Dev\Projetos\projeto_atualiza_banco\.venv\Scripts\pythonw.exe" `
              -ArgumentList 'main_web.py' `
              -WorkingDirectory "C:\Dev\Projetos\projeto_atualiza_banco" `
              -WindowStyle Hidden
```

Não afeta o usuário final: o executável empacotado é `console=False`.

## Pendências

- [ ] Ícone próprio (`resources/app.ico`).
- [ ] Assinatura de código: como no Gerenciador, o `version_info.txt` embute o
      `VS_VERSIONINFO` mas **não** substitui Authenticode. O Windows vai
      alertar sobre editor desconhecido na primeira execução.
- [ ] Pipeline de build, se o executável passar a ser distribuído.
