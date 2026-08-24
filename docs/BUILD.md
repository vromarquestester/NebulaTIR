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

## Release pela CI

`.github/workflows/build_exe.yml` dispara em tag `v*`: valida a tag contra
`src/_version.py`, compila, confere que `dist/NebulaTIR.exe` existe, zipa como
`NebulaTIR_vX.Y.Z.zip` e publica release. Exercitado de ponta a ponta na
`v0.1.0`.

O ciclo completo:

```bash
uv run pytest
uv run python scripts/bump_version.py patch
git add src/_version.py version_info.txt
git commit -m "chore(vX.Y.Z): bump version"
git tag vX.Y.Z
git push && git push origin vX.Y.Z
```

⚠ **O `build_exe.yml` não roda os testes** — valida versão e compila, nada
mais. Uma tag publica release mesmo com a suíte vermelha; quem roda pytest é o
workflow `Tests`, no push para `main`. Conferir que ele está verde antes de
taguear.

Sem espelho público e sem notificação, ao contrário do Gerenciador: a
distribuição do NebulaTIR sai pelo pacote da ToolBox, quando ela existir. Por
isso o zip leva só o executável — não há `docs/USER_GUIDE.md` para acompanhar.

## Diferenças em relação ao Gerenciador de Ambientes

- **Sem `resources/protheus_base`.** Provisionar ambiente é trabalho do
  Gerenciador.
- **`uac_admin=True` nos dois.** A decisão inicial aqui foi não elevar, e caiu
  quando a execução do TIR entrou no escopo: o NebulaTIR sobe instâncias do
  AppServer, que exigem administrador, e processo sem elevação não cria filho
  elevado.

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

- [x] ~~Ícone próprio.~~ `resources/app.ico` existe e o `.spec` o inclui.
- [x] ~~Pipeline de build.~~ Ver "Release pela CI" acima.
- [ ] Assinatura de código: como no Gerenciador, o `version_info.txt` embute o
      `VS_VERSIONINFO` mas **não** substitui Authenticode. O Windows vai
      alertar sobre editor desconhecido na primeira execução.
- [ ] Documentação de usuário (`docs/USER_GUIDE.md`), para o zip da release
      levar o guia junto como o do Gerenciador.
