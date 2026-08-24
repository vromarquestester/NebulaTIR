"""Front (`webui/web`): coerência estática, sem navegador.

O app precisa abrir offline e sem build step. Estes testes travam as duas
regras que quebram isso silenciosamente: recurso externo e `id` fantasma.
"""

import re
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parent.parent / "src" / "webui" / "web"
HTML = (WEB / "index.html").read_text(encoding="utf-8")
CSS = (WEB / "styles.css").read_text(encoding="utf-8")
JS = (WEB / "app.js").read_text(encoding="utf-8")


# ── Zero dependência externa ────────────────────────────────

ARQUIVOS = {"index.html": HTML, "styles.css": CSS, "app.js": JS}


# Parametriza pelo NOME, não pelo conteúdo: o pytest põe o id do caso no
# PYTEST_CURRENT_TEST, e o CSS inteiro estoura o limite de 32767 caracteres
# de variável de ambiente no Windows.
@pytest.mark.parametrize("nome", list(ARQUIVOS))
def test_sem_recurso_externo(nome):
    """CDN, fonte remota ou script externo quebram o app numa máquina sem rede."""
    conteudo = ARQUIVOS[nome]
    externos = re.findall(r"""(?:src|href)\s*=\s*["']https?://""", conteudo)
    externos += re.findall(r"@import\s+url\(\s*['\"]?https?://", conteudo)
    assert not externos, f"{nome} referencia recurso externo"


def test_sem_import_de_modulo_ou_build(HTML=HTML):
    assert "type=\"module\"" not in HTML
    assert "node_modules" not in HTML


# ── Coerência entre JS e HTML ───────────────────────────────

def _ids_do_html():
    return set(re.findall(r'id="([^"]+)"', HTML))


def test_todo_id_usado_no_js_existe_no_html():
    ids_html = _ids_do_html()
    # O `\)` no fim descarta id montado por concatenação
    # (`getElementById('cfg-' + chave)`), que só existe em tempo de execução.
    usados = set(re.findall(r"""\$\(\s*['"]#([A-Za-z0-9_-]+)['"]\s*[,)]""", JS))
    usados |= set(re.findall(r"""getElementById\(\s*['"]([A-Za-z0-9_-]+)['"]\s*\)""", JS))
    faltando = usados - ids_html
    assert not faltando, f"ids referenciados no JS e ausentes no HTML: {sorted(faltando)}"


def test_botoes_do_gate_existem():
    """Se um destes sumir do HTML, `atualizarBotoes` quebra silenciosamente."""
    ids = _ids_do_html()
    for botao in ("btn-importar", "btn-sincronizar", "btn-configuracao",
                  "btn-excluir", "btn-executar-tir", "chip-link", "gate"):
        assert botao in ids


def test_log_nasce_contraido_e_fora_do_layout():
    """Contraído no boot, e fora do `<main class="layout">` — dentro dele
    roubaria altura dos três painéis, que agora vão até o rodapé."""
    assert 'id="panel-log" data-aberto="false"' in HTML
    assert HTML.index("</main>") < HTML.index('id="panel-log"')
    assert 'aria-expanded="false"' in HTML


def test_fixar_comeca_escondido():
    """Só faz sentido com o painel aberto."""
    assert re.search(r'id="campo-fixar"[^>]*hidden', HTML)


def test_barra_de_processos_foi_removida():
    """Deixou de fazer sentido quando o NebulaTIR passou a subir e derrubar os
    próprios AppServer: o estado que importa é o das instâncias paralelas."""
    assert "proc-bar" not in HTML
    assert "proc-bar" not in JS


def test_botoes_de_subir_instancias_existem():
    ids = _ids_do_html()
    assert {"btn-subir-todos", "btn-subir-selecionados"} <= ids


def test_instancia_mostra_appserver_e_dbaccess_separados():
    """Um estado só escondia a instância com AppServer no ar e DbAccess caído
    — a que não atende e não parece parada."""
    assert 'data-papel="appserver"' in JS
    assert 'data-papel="dbaccess"' in JS
    assert ".item-paralelo .processo" in CSS


def test_status_de_processo_nao_depende_so_da_cor():
    """O ponto verde carrega a leitura rápida, mas o texto vai junto."""
    assert "'no ar' : 'parado'" in JS


def test_inventario_nao_depende_de_ambiente_selecionado():
    """Instância órfã não pertence a ambiente nenhum: o cartão fica fora do
    gate de modo e é montado já no boot."""
    assert 'id="card-inventario"' in HTML
    assert 'id="card-inventario" aria-labelledby="tit-inventario" hidden' not in HTML
    assert "renderInventario()" in JS


def test_limpeza_pede_confirmacao_com_os_caminhos():
    """Apagar dezenas de GB não se desfaz: a confirmação lista o que sai."""
    ids = _ids_do_html()
    assert {"overlay-limpeza", "lista-limpeza", "btn-confirmar-limpeza"} <= ids
    assert "abrirConfirmacaoLimpeza" in JS
    assert "$('.caminhos', li).textContent = caminhos" in JS


def test_instancia_em_uso_nao_pode_ser_marcada():
    """A caixa só existe para o que o inventário marcou como removível."""
    assert "inst.removivel ? '' : 'disabled'" in JS


def test_porta_de_instancia_criada_perde_o_amarelo():
    """Amarelo avisa o que ainda vai mudar; criada já está no appserver.ini."""
    assert 'tr[data-criada="true"] td[data-deslocada="true"]' in CSS
    assert "linha.dataset.criada" in JS


# ── Acessibilidade estrutural ───────────────────────────────

def test_um_unico_h1():
    assert len(re.findall(r"<h1[ >]", HTML)) == 1


def test_modais_tem_semantica_de_dialogo():
    # `modal` exato ou seguido de espaço — não pega `modal-head`/`modal-body`.
    modais = re.findall(r'<div class="modal(?: [^"]*)?"([^>]*)>', HTML)
    assert modais
    for atributos in modais:
        assert 'role="dialog"' in atributos
        assert 'aria-modal="true"' in atributos
        assert "aria-labelledby" in atributos


def test_botao_de_icone_tem_rotulo():
    for atributos in re.findall(r'<button[^>]*class="icon-btn"([^>]*)>', HTML):
        assert "aria-label" in atributos


def test_focus_visible_nunca_e_removido_sem_substituto():
    assert "outline: 2px solid hsl(var(--ring))" in CSS


def test_movimento_reduzido_respeitado():
    assert "prefers-reduced-motion" in CSS


# ── Tokens ──────────────────────────────────────────────────

def test_sem_hex_solto_nos_componentes():
    """Paleta é token HSL; hex solto sinaliza cor fora do sistema."""
    hexes = re.findall(r"#[0-9a-fA-F]{3,8}\b", CSS)
    assert not hexes, f"cores fora dos tokens: {hexes}"


def test_ok_continua_verde():
    """Semântica compartilhada com o Gerenciador: 'rodando' é verde nos dois."""
    assert re.search(r"--ok:\s*152 72% 50%", CSS)


# ── Nomes no escopo do módulo ───────────────────────────────

def test_nenhuma_funcao_e_declarada_duas_vezes():
    """Redeclarar `function f` no mesmo escopo não é erro em JS: a última
    silenciosamente vence.

    Foi assim que a árvore de execução matou a da seleção confirmada — as duas
    se chamavam `renderArvore`, e o botão Confirmar passou a não carregar mais
    os casos, travado em "Nenhuma rotina confirmada". Nada no console, nada no
    lint: só o comportamento sumindo.
    """
    nomes = re.findall(r"^(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(",
                       JS, re.MULTILINE)
    repetidos = sorted({n for n in nomes if nomes.count(n) > 1})
    assert not repetidos, f"função declarada mais de uma vez: {repetidos}"


def test_toda_funcao_chamada_no_app_existe():
    """Pega chamada a nome que ninguém declara — o outro lado do mesmo erro."""
    declaradas = set(re.findall(r"^(?:async\s+)?function\s+([A-Za-z_$][\w$]*)",
                                JS, re.MULTILINE))
    declaradas |= set(re.findall(r"(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*"
                                 r"(?:async\s*)?\(", JS))
    # Só conferimos as nossas: `render*`, que é onde a duplicidade nasceu.
    chamadas = set(re.findall(r"\b(render[A-Z][\w$]*)\s*\(", JS))
    assert chamadas <= declaradas, \
        f"chamada sem declaração: {sorted(chamadas - declaradas)}"
