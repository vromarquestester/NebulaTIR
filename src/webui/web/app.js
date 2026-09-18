/* ============================================================
   NebulaTIR — camada de interação.
   Sem framework e sem build: pywebview injeta `window.pywebview.api`,
   que é o objeto Python `webui.api.Api`.

   O eixo desta camada é o GATE. O NebulaTIR é um braço do Gerenciador de
   Ambientes: com o Gerenciador fechado, nenhuma ação é ofertada. O estado do
   link chega pelo polling de 2 s e reavalia os botões a cada ciclo, sem
   recarregar a página — inclusive quando o Gerenciador cai no meio da sessão.
   ============================================================ */

const $ = (sel, raiz = document) => raiz.querySelector(sel);
const $$ = (sel, raiz = document) => [...raiz.querySelectorAll(sel)];

const state = {
  importados: [],          // nomes, na ordem de importação
  selecionado: null,       // nome do ambiente selecionado
  statusAmbientes: {},
  link: null,              // null = ainda verificando
  linkMotivo: '',
  ocupado: false,
  configValida: null,
  vpn: null,
  disponiveis: [],         // preenchido ao abrir o modal de importação
  disponivelSel: null,
  configCampos: [],        // esquema do formulário, vindo do Python
  configAtual: {},
  configDivergencias: [],
  preferencias: {},        // globais: modo, limite, raiz dos testes
  buscaTestes: '',
  selecaoTestes: new Set(),
  origemTestes: 'fontes',            // 'fontes' (catálogo) ou 'local' (pasta)
  pastaLocal: '',
  selecaoLocal: new Set(),           // seleção da pasta local, separada
  configLocal: null,                 // situação do config.json da pasta
  abaAtiva: '#aba-config',           // aba do painel "Ambiente selecionado"
  execucao: { ativa: false, rotinas: [] },
  paralelosDesmarcados: new Set(),   // padrão é tudo marcado
  andamento: {},                     // fase em curso no Gerenciador
  exclusao: { ativa: false },        // exclusão de paralelos em andamento
  inventario: [],                    // instâncias no disco, com situação
  limpeza: { ativa: false },         // remoção do que o Gerenciador não alcança
  atualizacao: null,                // último estado de `services.atualizacao`
  podeExecutar: false,
  motivoExecutar: '',
  fontes: {},
  primeiraPintura: true,
};

let api = null;

/* ── Boot ──────────────────────────────────────────────── */

window.addEventListener('pywebviewready', iniciar);

async function iniciar() {
  api = window.pywebview.api;
  const b = await api.get_bootstrap();
  state.importados = b.importados.map(i => i.nome);
  state.preferencias = b.preferencias || {};
  $('#app-version').textContent = 'v' + b.versao;
  // Contraído no boot, sempre. `log_fixado` só governa o fechamento automático.
  $('#chk-fixar').checked = state.preferencias.log_fixado === true;
  abrirLog(false);
  renderModo();
  renderLista();
  // Sem depender de seleção: instância cujo pai sumiu não pertence a ambiente
  // nenhum, e é a que interessa ver logo ao abrir.
  renderInventario();
  ligarEventos();
  loopLogs();
  loopStatus();
}

/* ── Lista de ambientes importados ─────────────────────── */

function renderLista() {
  const lista = $('#env-list');
  lista.innerHTML = '';
  $('#badge-count').textContent = state.importados.length;
  $('#env-empty').hidden = state.importados.length > 0;

  if (state.primeiraPintura && state.importados.length) {
    lista.classList.add('first-paint');
    setTimeout(() => lista.classList.remove('first-paint'), 600);
    state.primeiraPintura = false;
  }

  for (const nome of state.importados) {
    const info = state.statusAmbientes[nome] || {};
    const li = document.createElement('li');
    li.className = 'env-item';
    li.dataset.nome = nome;
    li.setAttribute('role', 'option');
    li.tabIndex = -1;
    aplicarStatusNoItem(li, info);
    li.setAttribute('aria-selected', String(nome === state.selecionado));
    li.innerHTML = '<span class="name"></span>'
                 + '<span class="port" data-numeric></span>'
                 + '<span class="led" aria-hidden="true"></span>';
    $('.name', li).textContent = nome;
    $('.port', li).textContent = info.port ? ':' + info.port : '';
    li.addEventListener('click', () => selecionar(nome));
    lista.appendChild(li);
  }
}

/** LED e marca de órfão. Sem link, o status é "desconhecido" — não "parado":
    afirmar que está parado seria inventar uma informação que não temos. */
function aplicarStatusNoItem(li, info) {
  li.dataset.status = state.link === true ? (info.estado || 'stopped') : 'desconhecido';
  li.dataset.orfao = String(state.link === true && info.existe_no_gerenciador === false);
  if (li.dataset.orfao === 'true') {
    li.title = 'Este ambiente não existe mais no Gerenciador de Ambientes.';
  } else {
    li.removeAttribute('title');
  }
}

async function selecionar(nome) {
  state.selecionado = nome;
  // Seleção de testes é por ambiente: trocar de ambiente não pode carregar as
  // rotinas marcadas do anterior. `renderTestes` repõe a partir do disco.
  state.selecaoTestes = new Set();
  state.selecaoLocal = new Set();
  state.buscaTestes = '';
  $('#busca-testes').value = '';
  abrirCombo(false);
  $$('#env-list .env-item').forEach(li => {
    li.setAttribute('aria-selected', String(li.dataset.nome === nome));
  });
  $('#detalhe-ambiente').textContent = nome || '—';
  await renderDetalhes();
  atualizarBotoes();
}

/* ── Detalhes ──────────────────────────────────────────── */

async function renderDetalhes() {
  const corpo = $('#detail-body');
  const vazio = $('#detail-empty');
  if (!state.selecionado || state.link !== true) {
    corpo.hidden = true;
    vazio.hidden = state.link !== true;   // o gate já ocupa a área
    return;
  }

  const d = await api.detalhes_importado(state.selecionado);
  if (!d.ok) {
    corpo.hidden = true;
    vazio.hidden = false;
    vazio.textContent = d.erro;
    return;
  }

  vazio.hidden = true;
  corpo.hidden = false;

  // A origem decide o que `renderTestes` e `renderArvore` mostram.
  await carregarOrigemTestes();
  await Promise.all([renderPortas(), renderTestes(), renderArvore(),
                     renderParalelos(), renderInventario()]);
}

/* ── Modo, limite e portas ─────────────────────────────── */

function renderModo() {
  const modo = state.preferencias.modo || 'sequencial';
  for (const botao of $$('.seg[data-modo]')) {
    botao.setAttribute('aria-checked', String(botao.dataset.modo === modo));
  }
  $('#max-instancias').value = state.preferencias.max_instancias ?? 3;
  // O limite só tem efeito em paralelo — em sequencial o campo sai da tela em
  // vez de ficar ali pedindo um número que não muda nada.
  // A linha de campos só existe em paralelo: nem o número de instâncias nem a
  // divisão de casos mudam alguma coisa em sequencial.
  $('#modo-campos').hidden = modo !== 'paralelo';
  $('#chk-dividir-casos').checked = state.preferencias.dividir_casos === true;
  $('#chk-restaurar-banco').checked = state.preferencias.restaurar_banco !== false;
  $('#chk-restaurar-banco-local').checked = state.preferencias.restaurar_banco_local === true;

  $('#modo-explicacao').textContent = modo === 'paralelo'
    ? 'Cada instância sobe um AppServer próprio, com portas e banco próprios — '
    + 'os testes fixam grupo e filial no código, então dois slots no mesmo banco colidiriam.'
    : 'Um teste por vez: executa, gera log e PNG, roda o Somente Banco do '
    + 'Gerenciador, espera terminar e vai para o próximo.';
}

async function renderPortas() {
  const r = await api.plano_de_portas(state.selecionado || '');
  const erro = $('#portas-erro');
  erro.hidden = r.ok;
  if (!r.ok) {
    erro.textContent = r.erro;
    $('#portas-corpo').innerHTML = '';
    return;
  }

  const chaves = Object.keys(r.instancias[0].portas);
  const cabecalho = $('#portas-cabecalho');
  cabecalho.innerHTML = '<th>Instância</th>'
    + chaves.map(c => `<th>${r.rotulos[c] || c}</th>`).join('');

  const corpo = $('#portas-corpo');
  corpo.innerHTML = '';
  for (const inst of r.instancias) {
    const linha = document.createElement('tr');
    const deslocadas = new Set(inst.deslocadas);
    // Instância já criada não recebe destaque: a porta dela já está gravada
    // no appserver.ini, e o amarelo existe para avisar do que ainda vai mudar.
    linha.dataset.criada = String(!!inst.criada);
    if (inst.ambiente) linha.title = inst.ambiente;
    linha.innerHTML = `<td class="slot">${inst.slot}</td>`
      + chaves.map(c => `<td data-deslocada="${deslocadas.has(c)}">`
                      + `${inst.portas[c]}</td>`).join('');
    corpo.appendChild(linha);
  }

  const fixas = Object.entries(r.imutaveis)
    .map(([nome, porta]) => `${nome} ${porta}`).join('  ·  ');
  $('#portas-fixas').textContent =
    `Fixas, fora da alocação: ${fixas}. Em amarelo, as portas deslocadas do `
    + 'padrão que ainda vão ser criadas; instância já criada aparece em branco.';
}

/* ── Ambientes paralelos ───────────────────────────────── */

/* O cartão só aparece em paralelo: em sequencial não há instância para gerar,
   parar ou excluir, e um cartão inerte só ocupa espaço. */
async function renderParalelos() {
  const card = $('#card-paralelos');
  card.hidden = state.preferencias.modo !== 'paralelo';
  if (card.hidden || !state.selecionado) return;

  const r = await api.listar_paralelos(state.selecionado);
  const lista = $('#lista-paralelos');
  lista.innerHTML = '';
  $('#paralelos-vazio').hidden = r.instancias.length > 0;

  for (const inst of r.instancias) {
    const li = document.createElement('li');
    li.className = 'item-paralelo';
    li.dataset.estado = inst.estado;
    li.innerHTML = `<input type="checkbox" data-ambiente="${inst.ambiente}">
      <span class="nome"></span><span class="portas"></span>
      <span class="processo" data-papel="appserver"></span>
      <span class="processo" data-papel="dbaccess"></span>
      <span class="estado"></span>`;
    $('.nome', li).textContent = inst.ambiente;
    $('.portas', li).textContent = inst.portas && inst.portas.webapp
      ? ':' + inst.portas.webapp : '';
    // Um por processo: o estado único escondia a instância com AppServer no ar
    // e DbAccess caído, que é justamente a que não atende e não parece parada.
    for (const [papel, rotulo] of [['appserver', 'AppServer'],
                                   ['dbaccess', 'DbAccess']]) {
      const vivo = !!(inst.vivos && inst.vivos[papel]);
      const cel = $(`.processo[data-papel="${papel}"]`, li);
      cel.dataset.vivo = String(vivo);
      cel.textContent = `${rotulo} ${vivo ? 'no ar' : 'parado'}`;
    }
    $('.estado', li).textContent = inst.estado;
    // Padrão pedido: tudo marcado; quem não quer parar, desmarca.
    const marcado = !state.paralelosDesmarcados.has(inst.ambiente);
    $('input', li).checked = marcado;
    $('input', li).addEventListener('change', ev => {
      if (ev.target.checked) state.paralelosDesmarcados.delete(inst.ambiente);
      else state.paralelosDesmarcados.add(inst.ambiente);
      sincronizarTodos();
    });
    lista.appendChild(li);
  }
  sincronizarTodos();

  const rpo = await api.estado_rpo(state.selecionado);
  $('#rpo-situacao').textContent = rpo.guardado
    ? 'Há um RPO guardado deste ambiente.'
    : 'Nenhum RPO guardado — “Restaurar RPO do ambiente” fica indisponível.';
  $('#btn-rpo-ambiente').disabled = !rpo.guardado;

  renderAvisoExclusao();
  if (travarPorExclusao()) {
    for (const caixa of $$('#lista-paralelos input[type="checkbox"]')) {
      caixa.disabled = true;
    }
  }
}

/** Aviso de exclusão em curso: diz qual ambiente está saindo e quantos faltam.
    Sem barra de progresso: não há como estimar um detach de banco. */
function renderAvisoExclusao() {
  const aviso = $('#aviso-exclusao');
  const e = state.exclusao || {};
  aviso.hidden = !e.ativa;
  if (!e.ativa) return;
  const posicao = `${(e.feitos || 0) + 1} de ${e.total || 0}`;
  aviso.textContent = e.atual
    ? `Excluindo ${e.atual} (${posicao}) — aguarde, não feche o programa.`
    : 'Preparando a exclusão — aguarde.';
}

/** Comandos que mexem em ambiente ficam travados durante a exclusão. */
function travarPorExclusao() {
  const travado = state.exclusao && state.exclusao.ativa;
  for (const sel of ['#btn-gerar-paralelos', '#btn-subir-todos',
                     '#btn-subir-selecionados', '#btn-parar-paralelos',
                     '#btn-excluir-paralelos', '#btn-restaurar-banco',
                     '#btn-restaurar-banco-paralelos', '#chk-todos-paralelos']) {
    const el = $(sel);
    if (el) el.disabled = travado;
  }
  return travado;
}

/** Deixa o "selecionar todos" coerente com os itens (inclusive parcial). */
function sincronizarTodos() {
  const caixas = $$('#lista-paralelos input[type="checkbox"]');
  const todos = $('#chk-todos-paralelos');
  const marcadas = caixas.filter(c => c.checked).length;
  todos.checked = caixas.length > 0 && marcadas === caixas.length;
  todos.indeterminate = marcadas > 0 && marcadas < caixas.length;
}

function paralelosSelecionados() {
  return $$('#lista-paralelos input[type="checkbox"]')
    .filter(c => c.checked).map(c => c.dataset.ambiente);
}

function mostrarErroParalelos(mensagem) {
  const erro = $('#paralelos-erro');
  erro.hidden = !mensagem;
  erro.textContent = mensagem || '';
}

/* ── Instâncias no disco ───────────────────────────────── */

const ROTULO_SITUACAO = {
  ok: 'em uso',
  orfa: 'órfã',
  sem_cadastro: 'sem cadastro',
  fantasma: 'fantasma',
  nao_registrada: 'não registrada',
  indefinida: 'indefinida',
};

/** Bytes em algo legível. Instância é ordem de GB; sem isso a linha vira um
    número de 11 dígitos que ninguém compara de relance. */
function tamanhoLegivel(bytes) {
  if (!bytes) return '0 B';
  const unidades = ['B', 'KB', 'MB', 'GB', 'TB'];
  let valor = bytes, i = 0;
  while (valor >= 1024 && i < unidades.length - 1) { valor /= 1024; i++; }
  return `${valor.toFixed(valor >= 10 || i === 0 ? 0 : 1)} ${unidades[i]}`;
}

async function renderInventario() {
  const r = await api.inventario_instancias();
  const lista = $('#lista-inventario');
  lista.innerHTML = '';
  $('#inventario-vazio').hidden = r.instancias.length > 0;
  // Medir percorre árvores de GB: só sob pedido, e o resultado da rodada
  // anterior não vale mais depois de uma exclusão.
  $('#btn-medir-inventario').disabled = r.instancias.length === 0;

  for (const inst of r.instancias) {
    const li = document.createElement('li');
    li.className = 'item-inventario';
    li.dataset.situacao = inst.situacao;
    li.dataset.ambiente = inst.ambiente;
    li.dataset.removivel = String(inst.removivel);
    // Só o removível ganha caixa. Instância em uso não pode ser marcada por
    // engano num clique de "selecionar tudo".
    li.innerHTML = `<input type="checkbox" data-ambiente="${inst.ambiente}"
                           ${inst.removivel ? '' : 'disabled'}
                           aria-label="Selecionar ${inst.ambiente}">
      <span class="nome"></span>
      <span class="situacao"></span>
      <span class="tamanho">—</span>
      <span class="motivo"></span>`;
    $('.nome', li).textContent = inst.ambiente;
    $('.situacao', li).textContent = ROTULO_SITUACAO[inst.situacao] || inst.situacao;
    $('.motivo', li).textContent = inst.motivo;
    li.title = inst.caminhos.join('\n') || 'Sem pasta no disco.';
    $('input', li).addEventListener('change', sincronizarLimpeza);
    lista.appendChild(li);
  }
  state.inventario = r.instancias;
  sincronizarLimpeza();
}

/** O botão de apagar só acorda com algo marcado. */
function sincronizarLimpeza() {
  $('#btn-limpar-inventario').disabled =
    inventarioSelecionado().length === 0 || (state.limpeza || {}).ativa === true;
}

function inventarioSelecionado() {
  return $$('#lista-inventario input[type="checkbox"]')
    .filter(c => c.checked && !c.disabled).map(c => c.dataset.ambiente);
}

/** Confirmação: lista caminho a caminho o que vai sair. Apagar dezenas de GB
    não se desfaz, e o nome do ambiente sozinho não diz o que está em jogo. */
function abrirConfirmacaoLimpeza() {
  const alvos = new Set(inventarioSelecionado());
  const lista = $('#lista-limpeza');
  lista.innerHTML = '';
  for (const inst of (state.inventario || []).filter(i => alvos.has(i.ambiente))) {
    const li = document.createElement('li');
    li.className = 'item-limpeza';
    const caminhos = inst.caminhos.length
      ? inst.caminhos.join('\n') : 'Sem pasta no disco.';
    li.innerHTML = '<strong class="nome"></strong><span class="banco"></span>'
      + '<pre class="caminhos"></pre>';
    $('.nome', li).textContent = inst.ambiente;
    $('.banco', li).textContent = inst.banco
      ? `banco ${inst.banco} · DSN ${inst.banco}` : 'sem banco registrado';
    $('.caminhos', li).textContent = caminhos;
    lista.appendChild(li);
  }
  $('#limpeza-erro').hidden = true;
  $('#overlay-limpeza').hidden = false;
}

async function confirmarLimpeza() {
  const alvos = inventarioSelecionado();
  const r = await api.limpar_instancias(alvos);
  if (!r.ok) {
    const erro = $('#limpeza-erro');
    erro.hidden = false;
    erro.textContent = r.erro || 'Falha ao iniciar a limpeza.';
    return;
  }
  $('#overlay-limpeza').hidden = true;
  abrirLog(true);
  state.limpeza = { ativa: true };
  sincronizarLimpeza();
  acompanharLimpeza();
}

/** Cada remoção leva minutos (drop de banco, pastas de GB). Sem isto a tela
    parece congelada — mesmo motivo do aviso da exclusão. */
async function acompanharLimpeza() {
  const aviso = $('#aviso-limpeza');
  while (true) {
    const e = await api.estado_limpeza();
    state.limpeza = e;
    aviso.hidden = !e.ativa;
    if (e.ativa) {
      const posicao = `${(e.feitos || 0) + 1} de ${e.total || 0}`;
      aviso.textContent = e.atual
        ? `Apagando ${e.atual} (${posicao}) — aguarde, não feche o programa.`
        : 'Preparando a limpeza — aguarde.';
    } else {
      await renderInventario();
      await renderParalelos();
      return;
    }
    await new Promise(r => setTimeout(r, 700));
  }
}

/** Mede uma de cada vez: percorrer todas juntas deixaria a janela parada por
    minutos sem nada aparecer na tela. */
async function medirInventario() {
  const botao = $('#btn-medir-inventario');
  botao.disabled = true;
  try {
    for (const li of $$('#lista-inventario .item-inventario')) {
      $('.tamanho', li).textContent = '…';
      const r = await api.medir_instancia(li.dataset.ambiente);
      $('.tamanho', li).textContent = r.ok ? tamanhoLegivel(r.bytes) : '—';
    }
  } finally {
    botao.disabled = false;
  }
}

/* ── Catálogo de testes ────────────────────────────────── */

/** Seleção em uso: a dos fontes ou a da pasta local. São conjuntos
    separados — trocar de origem não pode apagar o que foi marcado na outra. */
function selecaoAtiva() {
  return state.origemTestes === 'local' ? state.selecaoLocal : state.selecaoTestes;
}

async function carregarOrigemTestes() {
  const r = await api.get_origem_testes(state.selecionado);
  state.origemTestes = r.ok ? r.origem : 'fontes';
  state.pastaLocal = r.ok ? (r.pasta || '') : '';
  renderOrigemTestes();
}

function renderOrigemTestes() {
  const local = state.origemTestes === 'local';
  for (const botao of $$('.seg[data-origem]')) {
    botao.setAttribute('aria-checked', String(botao.dataset.origem === state.origemTestes));
  }
  $('#pasta-local').hidden = !local;
  $('#pasta-local-campo').value = state.pastaLocal || '';
  if (!local) {
    $('#config-local-status').hidden = true;
    state.configLocal = null;
  }
}

async function trocarOrigemTestes(origem) {
  if (origem === state.origemTestes) return;
  const r = await api.salvar_origem_testes(state.selecionado, origem);
  if (!r.ok) return;
  state.origemTestes = r.origem;
  state.buscaTestes = '';
  $('#busca-testes').value = '';
  abrirCombo(false);
  renderOrigemTestes();
  await Promise.all([renderTestes(), renderArvore()]);
  atualizarBotoes();
}

/** Situação do config.json da pasta, logo abaixo do caminho. */
function renderConfigLocalStatus(config) {
  const p = $('#config-local-status');
  state.configLocal = config || null;
  if (state.origemTestes !== 'local' || !config) { p.hidden = true; return; }
  p.hidden = false;
  if (!config.existe) {
    p.dataset.estado = 'erro';
    p.textContent = 'config.json não encontrado na pasta — a execução precisa dele.';
    return;
  }
  if (config.erro) {
    p.dataset.estado = 'erro';
    p.textContent = config.erro;
    return;
  }
  const divs = config.divergencias || [];
  if (!divs.length) {
    p.dataset.estado = 'ok';
    p.textContent = 'config.json conferido: idioma, login POUI, log de depuração e navegador como esperado.';
    return;
  }
  const erros = divs.filter(d => d.nivel === 'erro').length;
  p.dataset.estado = erros ? 'erro' : 'aviso';
  p.textContent = `config.json com ${divs.length} campo${divs.length > 1 ? 's' : ''} `
    + `fora do esperado (${divs.map(d => d.rotulo).join(', ')}). `
    + 'Ao confirmar, você decide se ajusta.';
}

async function renderTestes() {
  const local = state.origemTestes === 'local';
  const r = local
    ? await api.listar_testes_locais(state.selecionado, state.buscaTestes || '')
    : await api.listar_testes(state.selecionado, state.buscaTestes || '');
  const lista = $('#lista-testes');
  const vazio = $('#testes-vazio');
  const selecao = selecaoAtiva();
  lista.innerHTML = '';

  if (!r.ok) {
    vazio.hidden = false;
    vazio.textContent = r.erro;
    $('#testes-origem').textContent = local && !state.pastaLocal
      ? 'Escolha a pasta com os testes.' : '—';
    renderConfigLocalStatus(null);
    atualizarResumoCombo();
    return;
  }

  if (local) {
    $('#testes-origem').textContent =
      `${r.total} teste${r.total === 1 ? '' : 's'} em ${r.pasta}`;
    renderConfigLocalStatus(r.config);
  } else {
    $('#testes-origem').textContent =
      `${r.pais} · ${r.total} rotinas em ${r.raiz}`;
  }

  if (!r.rotinas.length) {
    vazio.hidden = false;
    vazio.textContent = state.buscaTestes
      ? 'Nenhuma rotina com esse trecho no nome.'
      : (local ? 'Nenhum par TESTSUITE/TESTCASE nesta pasta.'
               : 'Nenhuma rotina para este país.');
    atualizarResumoCombo();
    return;
  }

  vazio.hidden = true;
  for (const rotina of r.rotinas) {
    if (rotina.selecionada) selecao.add(rotina.rotina);
    const li = document.createElement('li');
    li.className = 'combo-item';
    li.setAttribute('role', 'option');
    li.dataset.semCase = String(!rotina.tem_case);
    li.setAttribute('aria-selected', String(selecao.has(rotina.rotina)));
    li.innerHTML = `<input type="checkbox" tabindex="-1" aria-hidden="true">
      <span class="rotina"></span><span class="modulo"></span>`;
    $('.rotina', li).textContent = rotina.rotina;
    // Local: a subpasta faz o papel do módulo; na raiz da pasta só a contagem.
    $('.modulo', li).textContent = rotina.modulo
      ? `${rotina.modulo} · ${rotina.casos.length} casos`
      : `${rotina.casos.length} casos`;
    $('input', li).checked = selecao.has(rotina.rotina);
    if (!rotina.tem_case) {
      li.title = 'Sem o arquivo TESTCASE — o suite quebra no import.';
    }
    li.tabIndex = 0;
    const alternar = () => alternarRotina(rotina.rotina, li);
    li.addEventListener('click', alternar);
    li.addEventListener('keydown', ev => {
      if (ev.key === ' ' || ev.key === 'Enter') { ev.preventDefault(); alternar(); }
    });
    lista.appendChild(li);
  }
  atualizarResumoCombo();
}

function alternarRotina(nome, li) {
  const selecao = selecaoAtiva();
  if (selecao.has(nome)) selecao.delete(nome);
  else selecao.add(nome);
  const marcado = selecao.has(nome);
  $('input', li).checked = marcado;
  li.setAttribute('aria-selected', String(marcado));
  atualizarResumoCombo();
}

function atualizarResumoCombo() {
  const total = selecaoAtiva().size;
  const local = state.origemTestes === 'local';
  const nome = local ? 'teste' : 'rotina';
  const marcado = local ? 'marcado' : 'marcada';
  $('#combo-resumo').textContent = total
    ? `${total} ${nome}${total > 1 ? 's' : ''} ${marcado}${total > 1 ? 's' : ''}`
    : `Selecionar ${nome}s…`;
}

/* ── Pasta local ────────────────────────────────────────── */

async function escolherPastaLocal() {
  const r = await api.escolher_pasta_local(state.selecionado);
  if (!r.ok && r.cancelado) return;
  await aplicarPastaLocal(r);
}

async function aplicarPastaLocalDigitada(caminho) {
  caminho = (caminho || '').trim();
  if (caminho === state.pastaLocal) return;
  const r = await api.salvar_pasta_local(state.selecionado, caminho);
  await aplicarPastaLocal(r);
}

async function aplicarPastaLocal(r) {
  // Trocar a pasta zera a seleção no Python; aqui também, senão a lista nova
  // nasce com marcas da pasta antiga.
  state.selecaoLocal = new Set();
  state.pastaLocal = r.pasta || $('#pasta-local-campo').value.trim();
  $('#pasta-local-campo').value = state.pastaLocal;
  await Promise.all([renderTestes(), renderArvore()]);
  atualizarBotoes();
}

/* ── config.json local: pergunta antes de ajustar ───────── */

function abrirModalConfigLocal(conferido) {
  const total = conferido.divergencias.length;
  $('#config-local-intro').textContent =
    `${conferido.caminho} — ${total} campo${total > 1 ? 's' : ''} fora do esperado `
    + `para este ambiente${conferido.idioma ? ` (idioma ${conferido.idioma})` : ''}.`;
  const linhas = $('#div-linhas');
  linhas.innerHTML = '';
  for (const d of conferido.divergencias) {
    const linha = document.createElement('div');
    linha.className = 'div-linha';
    linha.setAttribute('role', 'row');
    linha.dataset.nivel = d.nivel;
    for (const [classe, texto] of [
      ['campo-nome', d.rotulo], ['encontrado', d.encontrado],
      ['esperado', d.esperado], ['motivo', d.motivo],
    ]) {
      const celula = document.createElement('span');
      celula.className = classe;
      celula.setAttribute('role', 'cell');
      celula.textContent = texto;
      if (classe === 'campo-nome') {
        const chave = document.createElement('code');
        chave.className = 'campo-chave mono';
        chave.textContent = d.chave;
        celula.appendChild(chave);
      }
      linha.appendChild(celula);
    }
    linhas.appendChild(linha);
  }
  $('#config-local-erro').hidden = true;
  abrirModal('overlay-config-local');
}

async function ajustarConfigLocal() {
  const r = await api.corrigir_config_local(state.selecionado);
  if (!r.ok) {
    const erro = $('#config-local-erro');
    erro.hidden = false;
    erro.textContent = r.erro || 'Não foi possível gravar o config.json.';
    return;
  }
  fecharModal('overlay-config-local');
  escreverLinha({ level: 'INFO',
                  text: `[LOCAL] config.json ajustado: ${(r.alteradas || []).join(', ') || 'nada a mudar'}.` });
  await renderTestes();
}

function abrirCombo(abrir) {
  $('#combo-painel').hidden = !abrir;
  $('#btn-combo').setAttribute('aria-expanded', String(abrir));
  if (abrir) $('#busca-testes').focus();
}

/* ── Abas do ambiente selecionado ───────────────────────── */

const ABAS = [
  { aba: '#aba-config', painel: '#painel-config' },
  { aba: '#aba-casos',  painel: '#painel-casos'  },
];

function trocarAba(alvo, focar = true) {
  for (const { aba, painel } of ABAS) {
    const ativa = aba === alvo;
    const botao = $(aba);
    botao.setAttribute('aria-selected', String(ativa));
    // Só a aba ativa fica no Tab: a seta é quem anda entre elas, que é o
    // padrão de tablist e evita o Tab passar por painel escondido.
    botao.tabIndex = ativa ? 0 : -1;
    $(painel).hidden = !ativa;
    if (ativa && focar) botao.focus();
  }
  state.abaAtiva = alvo;
}

function ligarAbas() {
  for (const { aba } of ABAS) {
    $(aba).addEventListener('click', () => trocarAba(aba, false));
  }
  $('.abas').addEventListener('keydown', ev => {
    const passo = ev.key === 'ArrowRight' ? 1 : ev.key === 'ArrowLeft' ? -1 : 0;
    if (!passo) return;
    ev.preventDefault();
    const atual = ABAS.findIndex(a => a.aba === state.abaAtiva);
    trocarAba(ABAS[(atual + passo + ABAS.length) % ABAS.length].aba);
  });
}

/* ── Árvore da seleção confirmada ──────────────────────── */

/* Resultado por rotina da corrida atual, indexado pelo nome.

   Vem de `estado_execucao`, não da seleção: a seleção diz o que foi escolhido,
   a execução diz como foi. Enquanto não houver corrida o mapa é vazio e tudo
   fica cinza — que é o certo, "não executado". */
function situacaoDaCorrida() {
  const mapa = new Map();
  for (const r of (state.execucao && state.execucao.rotinas) || []) {
    mapa.set(r.rotina, r);
  }
  return mapa;
}

/* Estado da rotina para efeito de cor.

   `erro` vence: uma suite com um caso falho é uma suite vermelha, mesmo que os
   outros vinte tenham passado. Foi a regra pedida, e é a mesma que o backend
   já aplica ao fechar a rotina. */
function estadoDaRotina(situacao) {
  if (!situacao) return 'fila';
  if (['ok', 'erro', 'abortado'].includes(situacao.estado)) return situacao.estado;
  const resultados = Object.values(situacao.resultados || {});
  if (resultados.includes('erro')) return 'erro';
  if (situacao.estado === 'rodando') return 'rodando';
  return 'fila';
}

/* Botão que abre um artefato da rotina. Só existe quando há caminho: rotina
   que não chegou a gerar relatório não ganha botão morto. */
function botaoArtefato(caminho, rotulo, simbolo, titulo) {
  if (!caminho) return null;
  const b = document.createElement('button');
  b.type = 'button';
  b.className = 'btn-artefato';
  b.title = titulo;
  const marca = document.createElement('span');
  marca.setAttribute('aria-hidden', 'true');
  marca.textContent = simbolo;
  const texto = document.createElement('span');
  texto.className = 'rotulo';
  texto.textContent = rotulo;
  b.append(marca, texto);
  b.addEventListener('click', async ev => {
    // O summary abre e fecha o details no clique; sem isto, ver a evidência
    // recolhe a rotina junto.
    ev.preventDefault();
    ev.stopPropagation();
    const r = await api.abrir_arquivo(caminho);
    if (!r.ok) {
      escreverLinha({ level: 'ERROR',
                      text: `[EVIDÊNCIA] ${r.erro || 'não foi possível abrir'}: ${caminho}` });
    }
  });
  return b;
}

async function renderArvore() {
  const r = await api.get_selecao(state.selecionado);
  const caixa = $('#arvore-testes');
  caixa.innerHTML = '';
  const badge = $('#aba-casos-badge');
  if (!r.ok || !r.arvore.length) {
    $('#arvore-vazia').hidden = false;
    badge.hidden = true;
    $('#arvore-acoes').hidden = true;
    $('#btn-limpar-resultado').hidden = true;
    return;
  }
  $('#arvore-vazia').hidden = true;
  badge.hidden = false;
  badge.textContent = r.arvore.length;
  $('#arvore-acoes').hidden = false;

  const situacoes = situacaoDaCorrida();
  const temResultado = [...situacoes.values()].some(
    s => ['ok', 'erro', 'abortado'].includes(s.estado));
  $('#btn-limpar-resultado').hidden = !temResultado || state.execucao.ativa === true;

  for (const rotina of r.arvore) {
    const situacao = situacoes.get(rotina.rotina);
    const estado = estadoDaRotina(situacao);
    const resultados = (situacao && situacao.resultados) || {};

    const bloco = document.createElement('details');
    bloco.className = 'arvore-rotina';
    bloco.dataset.ausente = String(rotina.ausente);
    bloco.dataset.estado = estado;
    // Contraída, exceto a que está rodando: durante a corrida, ter que abrir a
    // rotina para ver onde ela está anula o motivo de a cor existir.
    bloco.open = estado === 'rodando';

    const resumo = document.createElement('summary');
    // Estado nunca vai só na cor — quem não distingue verde de vermelho lê o
    // mesmo no title, como já acontece na árvore de execução.
    resumo.title = `${rotina.rotina}: ${TITULO_CASO[estado] || estado}`;
    for (const [classe, valor] of [
      ['marca', MARCA_CASO[estado] || '·'],
      ['nome', rotina.rotina],
      ['modulo', rotina.ausente ? 'não encontrada no disco' : rotina.modulo],
      ['contagem', String(rotina.casos.length)],
    ]) {
      const span = document.createElement('span');
      span.className = classe;
      if (classe === 'marca') span.setAttribute('aria-hidden', 'true');
      span.textContent = valor;
      resumo.appendChild(span);
    }

    // Os artefatos existem quando a rotina TERMINOU: durante a corrida o
    // relatório ainda está sendo escrito, e abrir um png pela metade mostra um
    // resultado que não é o final.
    const artefatos = document.createElement('span');
    artefatos.className = 'artefatos';
    if (situacao && ['ok', 'erro', 'abortado'].includes(situacao.estado)) {
      // A imagem mostra QUE falhou; o log mostra POR QUE.
      const evidencia = botaoArtefato(situacao.png, 'Evidência', '\u{1F5BC}',
                                      'Abrir o relatório em imagem');
      const registro = botaoArtefato(situacao.log, 'Log', '\u{1F4C4}',
                                     'Abrir o log da execução');
      if (evidencia) artefatos.appendChild(evidencia);
      if (registro) artefatos.appendChild(registro);
    }
    resumo.appendChild(artefatos);
    bloco.appendChild(resumo);

    const casos = document.createElement('ul');
    casos.className = 'arvore-casos';
    for (const caso of rotina.casos) {
      const li = document.createElement('li');
      const estadoCaso = resultados[caso] || 'fila';
      li.dataset.estado = estadoCaso;
      li.title = `${caso}: ${TITULO_CASO[estadoCaso] || estadoCaso}`;
      const marca = document.createElement('span');
      marca.className = 'marca';
      marca.setAttribute('aria-hidden', 'true');
      marca.textContent = MARCA_CASO[estadoCaso] || '·';
      const texto = document.createElement('span');
      texto.className = 'texto';
      texto.textContent = caso;
      li.append(marca, texto);
      casos.appendChild(li);
    }
    bloco.appendChild(casos);
    caixa.appendChild(bloco);
  }
}

function abrirTodasAsRotinas(abrir) {
  for (const bloco of $$('#arvore-testes .arvore-rotina')) bloco.open = abrir;
}

/* Descarta o resultado da corrida anterior.

   Não mexe na seleção nem apaga arquivo nenhum: o que sai é a memória do que
   já rodou, para a mesma seleção poder ser executada de novo. Sem isto, a
   única forma de voltar tudo a cinza era reconfirmar a seleção. */
async function limparResultados() {
  const r = await api.limpar_execucao();
  if (!r.ok) {
    escreverLinha({ level: 'WARNING', text: `[LIMPAR] ${r.erro || 'não foi possível limpar'}` });
    return;
  }
  state.execucao = { ativa: false, rotinas: [] };
  renderExecucao();
  await renderArvore();
}

async function confirmarTestes() {
  const local = state.origemTestes === 'local';
  const r = local
    ? await api.salvar_selecao_local(state.selecionado, [...state.selecaoLocal])
    : await api.salvar_selecao(state.selecionado, [...state.selecaoTestes]);
  if (!r.ok) return;
  abrirCombo(false);
  await renderArvore();
  atualizarBotoes();
  if (!local || !state.selecaoLocal.size) return;

  // O config.json da pasta manda na corrida. Conferido aqui, na confirmação:
  // se diverge, a pessoa vê o que foi encontrado e decide se ajusta.
  const conferido = await api.validar_config_local(state.selecionado);
  if (!conferido.ok) {
    renderConfigLocalStatus({ existe: true, erro: conferido.erro, divergencias: [] });
    return;
  }
  if (conferido.divergencias.length) abrirModalConfigLocal(conferido);
}

/* ── Status e log (polling) ────────────────────────────── */

async function loopStatus() {
  try {
    const s = await api.get_status();
    const linkMudou = state.link !== s.link;
    const statusMudou = JSON.stringify(s.ambientes) !== JSON.stringify(state.statusAmbientes);
    const listaMudou = JSON.stringify(s.importados) !== JSON.stringify(state.importados);

    state.link = s.link;
    state.linkMotivo = s.link_motivo || '';
    state.statusAmbientes = s.ambientes;
    state.importados = s.importados;
    state.ocupado = s.ocupado;
    state.configValida = s.config_valida;
    state.vpn = s.vpn;

    chip($('#chip-link'), $('#chip-link-text'), s.link, 'Gerenciador');
    // Sem resposta mas dentro da tolerância: o link vale, e o chip avisa que
    // o Gerenciador está ocupado em vez de piscar para vermelho.
    if (s.link && s.link_instavel) {
      $('#chip-link').dataset.state = 'instavel';
      $('#chip-link-text').textContent = 'Gerenciador: ocupado';
    }
    chip($('#chip-vpn'), $('#chip-vpn-text'), s.link ? s.vpn : null, 'VPN');
    chip($('#chip-sql'), $('#chip-sql-text'), s.link ? s.config_valida : null, 'SQL');
    $('#chip-link').title = s.link ? `Gerenciador de Ambientes v${s.gerenciador_versao}`
                                  : state.linkMotivo;
    $('#conexao-ativa').textContent = s.conexao_ativa || '—';

    const badge = $('#badge-op');
    badge.hidden = !s.ocupado;
    badge.textContent = s.operacao ? s.operacao.replace(':', ' · ') : '';

    if (listaMudou) renderLista();
    else if (statusMudou || linkMudou) {
      $$('#env-list .env-item').forEach(li => {
        const info = state.statusAmbientes[li.dataset.nome] || {};
        aplicarStatusNoItem(li, info);
        $('.port', li).textContent = info.port ? ':' + info.port : '';
      });
    }
    state.andamento = s.andamento || {};

    // Exclusão roda em thread: a lista encolhe no ritmo do Gerenciador, e os
    // comandos ficam travados enquanto isso.
    const exclusaoAntes = state.exclusao.ativa;
    state.exclusao = s.exclusao || { ativa: false };
    renderAvisoExclusao();
    if (state.exclusao.ativa || exclusaoAntes !== state.exclusao.ativa) {
      await renderParalelos();
    }

    if (linkMudou) await renderDetalhes();

    // AppServer e DbAccess sobem e caem durante a corrida, e a bolinha de cada
    // um só era redesenhada ao trocar de ambiente ou depois de uma ação — a
    // instância ficava marcada como parada com o processo já no ar.
    // Redesenhar enquanto algo acontece é o suficiente: fora disso o estado
    // não muda sozinho.
    if (state.selecionado
        && (state.execucao.ativa === true || (state.andamento || {}).ativo)) {
      await renderParalelos();
    }

    // Andamento da corrida e liberação do botão vêm do backend, que conhece
    // todas as travas (VPN, SQL, seleção, execução já em curso).
    const execucaoAnterior = JSON.stringify(state.execucao && state.execucao.rotinas);
    state.execucao = await api.estado_execucao();
    const liberado = await api.pode_executar(state.selecionado || '');
    state.podeExecutar = liberado.ok === true;
    state.motivoExecutar = liberado.motivo || '';
    renderExecucao();

    // A aba "Casos de teste" mostra o mesmo resultado com outra lente, e
    // precisa acompanhar. Só quando muda: redesenhar a cada 2 s fecharia o
    // `details` que a pessoa abriu para olhar.
    if (JSON.stringify(state.execucao.rotinas) !== execucaoAnterior
        && state.selecionado) {
      await renderArvore();
    }

    atualizarBotoes();
  } catch (e) { /* janela fechando */ }

  // A cada 5ª volta (10 s), ou sempre que o modal estiver aberto. Ler o estado
  // da atualização a cada 2 s tocaria o disco (o `pendente.json`) sem motivo:
  // ele muda uma vez por dia.
  ticksUpdate = (ticksUpdate + 1) % 5;
  if (ticksUpdate === 0 || !$('#overlay-atualizacao').hidden) atualizarUpdate();

  setTimeout(loopStatus, 2000);
}

/* ── Atualização do programa ───────────────────────────── */

/* O que cada estado mostra no chip da barra. Ausente = chip escondido: em dia,
   ocioso e desligado não são notícia, e chip permanente vira ruído fixo.
   `erro` também não aparece (igual ao Gerenciador): rede corporativa, proxy e
   VPN caem o tempo todo, e um chip vermelho a cada 5 min seria alarme fixo
   para algo que não é problema do usuário — a rodada seguinte tenta de novo.
   A causa fica no log e em Configurações → Atualização. */
const ROTULO_UPDATE = {
  disponivel: 'Atualização disponível',
  baixando:   'Baixando atualização…',
  pronto:     'Reinicie para atualizar',
};

let ticksUpdate = 0;

async function atualizarUpdate() {
  try {
    state.atualizacao = await api.atualizacao_estado();
  } catch (e) { return; }        // janela fechando
  pintarChipUpdate();
  if (!$('#overlay-atualizacao').hidden) pintarModalUpdate();
}

function pintarChipUpdate() {
  const u = state.atualizacao;
  const chipEl = $('#chip-update');
  if (!u) { chipEl.hidden = true; return; }

  const rotulo = ROTULO_UPDATE[u.estado];
  chipEl.hidden = !rotulo;
  if (!rotulo) return;
  // Amarelo em todos os estados que aparecem: os três são "tem coisa
  // pendente". Verde diria "está tudo certo, não faça nada", que é o oposto.
  chipEl.dataset.state = 'pendente';
  $('#chip-update-text').textContent =
    u.estado === 'baixando' ? `Baixando… ${u.progresso}%` : rotulo;
}

function pintarModalUpdate(extra) {
  const u = state.atualizacao || {};
  $('#upd-versao-atual').textContent = u.versao_atual || '—';

  const temNova = Boolean(u.versao_nova) && u.versao_nova !== u.versao_atual;
  $('#upd-linha-nova').hidden = !temNova;
  $('#upd-versao-nova').textContent = u.versao_nova || '—';

  const mensagens = {
    desligado:   'Atualização automática desligada.',
    ocioso:      'Nada verificado ainda.',
    verificando: 'Consultando…',
    'em-dia':    'Você está na versão mais recente.',
    disponivel:  'Há uma versão nova. Baixe para instalar na próxima abertura.',
    baixando:    'Baixando…',
    pronto:      'Baixada. Reinicie o programa para aplicar.',
  };
  // Erro traz a causa junto (proxy, hash divergente, versão mínima) — é o
  // texto que diz o que fazer, e engoli-lo viraria "não atualiza e não diz".
  $('#upd-mensagem').textContent = extra || (u.estado === 'erro'
    ? (u.mensagem || 'Falhou.')
    : (mensagens[u.estado] || '—'));

  $('#upd-progresso').hidden = u.estado !== 'baixando';
  $('#upd-barra').style.width = `${u.progresso || 0}%`;

  const itens = u.changelog || [];
  $('#upd-changelog-box').hidden = itens.length === 0;
  $('#upd-changelog-vazio').hidden = !$('#upd-changelog-box').hidden;
  $('#upd-changelog').innerHTML = '';
  itens.forEach(txt => {
    const li = document.createElement('li');
    li.textContent = txt;          // vem de arquivo externo: nunca innerHTML
    $('#upd-changelog').appendChild(li);
  });

  pintarFamiliaUpdate(u.familia || []);

  $('#chk-upd-auto').checked = u.automatica !== false;
  $('#sobre-versao').textContent = u.versao_atual || '—';
  $('#sobre-pasta').textContent = u.pasta || '—';
  $('#sobre-vitrine').textContent = u.vitrine || '—';
  $('#sobre-ultima').textContent = u.ultima_verificacao
    ? new Date(u.ultima_verificacao).toLocaleString('pt-BR') : 'nunca';
  $('#sobre-intervalo').textContent = u.automatica === false ? 'desligada'
    : `a cada ${Math.round((u.intervalo_seg || 300) / 60)} min`;
  $('#btn-upd-baixar').hidden = u.estado !== 'disponivel';
  $('#btn-upd-reiniciar').hidden = u.estado !== 'pronto';
  $('#btn-upd-verificar').disabled = u.estado === 'verificando'
                                  || u.estado === 'baixando';
  $('#btn-upd-reverter').hidden = u.pode_reverter !== true;
}

/* As irmãs instaladas ao lado. `ausente` não aparece: ferramenta que não está
   nesta pasta não é notícia. O que se mostra é fato observado — versão lida
   do exe dela e o que a vitrine dela publicou. */
const ROTULO_FAMILIA = {
  'em-dia':   'em dia',
  disponivel: 'versão nova (baixe pelo próprio programa)',
  baixando:   'baixando…',
  pronto:     'baixada — entra quando abrir',
  atualizada: 'atualizada agora',
  erro:       'falhou',
};

function pintarFamiliaUpdate(familia) {
  const visiveis = familia.filter(f => f.estado !== 'ausente');
  $('#upd-familia-box').hidden = visiveis.length === 0;
  const lista = $('#upd-familia');
  lista.innerHTML = '';
  visiveis.forEach(f => {
    const li = document.createElement('li');
    li.dataset.state = f.estado;
    const nome = document.createElement('span');
    nome.className = 'upd-familia-nome';
    nome.textContent = f.nome;
    const versao = document.createElement('span');
    versao.className = 'upd-familia-versao';
    versao.setAttribute('data-numeric', '');
    versao.textContent = f.versao_nova && f.versao_nova !== f.versao_atual
      ? `${f.versao_atual} → ${f.versao_nova}` : (f.versao_atual || '—');
    const msg = document.createElement('span');
    msg.className = 'upd-familia-msg';
    // Erro traz a causa (vem de arquivo externo: nunca innerHTML).
    msg.textContent = f.estado === 'erro'
      ? (f.mensagem || 'falhou') : (ROTULO_FAMILIA[f.estado] || f.estado);
    li.append(nome, versao, msg);
    lista.appendChild(li);
  });
}

/* Abas do modal de Configurações. Separadas das abas do painel central
   (ABAS/trocarAba), que têm o próprio estado. */
const ABAS_CONFIG = [
  { aba: '#aba-cfg-atualizacao', painel: '#painel-cfg-atualizacao' },
  { aba: '#aba-cfg-sobre',       painel: '#painel-cfg-sobre'       },
];

function trocarAbaConfig(alvo) {
  for (const { aba, painel } of ABAS_CONFIG) {
    const ativa = aba === alvo;
    $(aba).setAttribute('aria-selected', String(ativa));
    $(aba).tabIndex = ativa ? 0 : -1;
    $(painel).hidden = !ativa;
  }
}

async function abrirAtualizacao(aba = '#aba-cfg-atualizacao') {
  await atualizarUpdate();
  pintarModalUpdate();
  trocarAbaConfig(aba);
  abrirModal('overlay-atualizacao');
}

function chip(elChip, elTexto, valor, rotulo) {
  if (valor === null || valor === undefined) {
    elChip.dataset.state = 'unknown';
    elTexto.textContent = `${rotulo}: —`;
  } else {
    elChip.dataset.state = valor ? 'on' : 'off';
    elTexto.textContent = `${rotulo}: ${valor ? 'online' : 'offline'}`;
  }
}

const MAX_LINHAS = 1500;

async function loopLogs() {
  try {
    const eventos = await api.poll_logs();
    for (const ev of eventos) if (ev.kind === 'log') escreverLinha(ev);
  } catch (e) { /* janela fechando */ }
  setTimeout(loopLogs, 150);
}

/* ── Painel de execução (coluna da direita) ────────────── */

/* Vocabulário da árvore ao vivo. O círculo vazio é "em execução": ele não
   preenche porque ainda não há resultado — só ✓ e ✕ afirmam alguma coisa.
   Estado nunca vai só na cor: o texto do title diz o mesmo. */
const MARCA_CASO = { fila: '·', rodando: '○', ok: '✓', erro: '✕', abortado: '■' };
const TITULO_CASO = {
  fila: 'na fila', rodando: 'em execução', ok: 'passou',
  erro: 'falhou', abortado: 'interrompido',
};

function renderArvoreExecucao() {
  const arvore = $('#exec-arvore');
  /* Toda instância da corrida aparece, inclusive a que não pegou trabalho.
     Esconder a ociosa fazia o paralelo parecer sequencial: com duas
     instâncias no ar e uma sem rotina, só uma aparecia na tela e não havia
     como saber se a outra existia. */
  const instancias = state.execucao.arvore || [];
  arvore.hidden = instancias.length === 0;
  if (!instancias.length) { arvore.innerHTML = ''; return; }

  arvore.innerHTML = '';
  for (const inst of instancias) {
    const li = document.createElement('li');
    li.className = 'arvore-ambiente';
    li.dataset.ociosa = String(!inst.rotina);

    const cab = document.createElement('div');
    cab.className = 'arvore-titulo';
    cab.textContent = inst.ambiente || `Instância ${inst.slot}`;
    li.appendChild(cab);

    const rotina = document.createElement('div');
    rotina.className = 'arvore-rotina';
    rotina.textContent = inst.rotina
      || (state.execucao.ativa ? 'aguardando trabalho' : 'sem trabalho nesta corrida');
    li.appendChild(rotina);

    const casos = document.createElement('ul');
    casos.className = 'arvore-casos';
    for (const caso of inst.casos || []) {
      const item = document.createElement('li');
      item.dataset.estado = caso.estado;
      item.title = `${caso.nome}: ${TITULO_CASO[caso.estado] || caso.estado}`;
      const marca = document.createElement('span');
      marca.className = 'marca';
      marca.setAttribute('aria-hidden', 'true');
      marca.textContent = MARCA_CASO[caso.estado] || '·';
      const texto = document.createElement('span');
      texto.className = 'texto';
      texto.textContent = caso.nome;
      item.append(marca, texto);
      casos.appendChild(item);
    }
    li.appendChild(casos);
    arvore.appendChild(li);
  }
}

function renderExecucao() {
  const rotinas = state.execucao.rotinas || [];

  // Enquanto o Gerenciador trabalha (clonagem, restauração, remoção), o painel
  // mostra a fase dele. Sem isso a tela parece travada por minutos.
  const andamento = state.andamento || {};
  if (!rotinas.length && andamento.ativo) {
    $('#exec-empty').hidden = true;
    $('#exec-body').hidden = false;
    $('#exec-modo').hidden = false;
    $('#exec-modo').textContent = andamento.modo || 'gerenciador';
    $('#exec-ambiente').textContent = andamento.ambiente || '';
    const lista = $('#exec-fases');
    lista.innerHTML = '';
    const tituloFases = $('#exec-fases-titulo');
    tituloFases.hidden = false;
    tituloFases.textContent = 'Preparando o ambiente';
    const li = document.createElement('li');
    li.dataset.estado = andamento.estado === 'erro' ? 'erro'
      : andamento.estado === 'ok' ? 'ok' : 'ativo';
    li.innerHTML = '<span class="marca" aria-hidden="true">▸</span>'
                 + '<span class="texto"></span>';
    $('.texto', li).textContent =
      [andamento.fase, andamento.titulo].filter(Boolean).join(' · ');
    lista.appendChild(li);
    $('#exec-final').hidden = true;
    $('#exec-arvore').hidden = true;
    return;
  }

  $('#exec-empty').hidden = rotinas.length > 0;
  $('#exec-body').hidden = rotinas.length === 0;
  if (!rotinas.length) { $('#exec-arvore').hidden = true; return; }

  renderArvoreExecucao();

  const modo = $('#exec-modo');
  modo.hidden = false;
  modo.textContent = `${state.execucao.concluidas || 0}/${state.execucao.total || 0}`;
  $('#exec-ambiente').textContent = state.execucao.ambiente || '';

  // O resultado por rotina — com evidência, log e os casos — vive na aba
  // "Casos de teste". Ter a mesma lista aqui era duplicar o dado em duas
  // telas, e a de lá é mais completa: esta só tinha o total.
  //
  // A `#exec-fases` continua no HTML porque é ela que mostra a fase do
  // Gerenciador enquanto ele clona ou restaura, acima.
  $('#exec-fases-titulo').hidden = true;
  $('#exec-fases').innerHTML = '';

  const final = $('#exec-final');
  final.hidden = state.execucao.ativa !== false || !rotinas.length;
  if (!final.hidden) {
    const falhou = rotinas.some(r => r.estado === 'erro' || r.estado === 'abortado');
    final.dataset.estado = falhou ? 'erro' : 'ok';
    final.textContent = falhou ? 'Execução terminada com falhas'
                               : 'Execução concluída com sucesso';
  }
}

/* ── Rastro da tela (debug-*.log) ──────────────────────── */

/* Tudo que o usuário faz na tela vai para o `debug-*.log`, pelo
   `api.rastro_ui`: clique (com o alvo identificado), campo alterado, tecla
   de atalho, aba, modal e painel de log. Fire-and-forget: nunca espera nem
   deixa erro do rastro chegar ao fluxo. O log da tela não muda. */
function rastro(evento, dados) {
  try {
    if (api && api.rastro_ui) api.rastro_ui(evento, dados || {}).catch(() => {});
  } catch (_) { /* rastro nunca derruba a tela */ }
}

function alvoDoRastro(el) {
  const alvo = el.closest('button, a, [role="tab"], li[data-nome], li[data-ambiente], '
    + 'input, select, textarea, label, summary, [data-acao], [data-rastro]');
  if (!alvo) return null;
  const d = { tag: alvo.tagName.toLowerCase() };
  if (alvo.id) d.id = alvo.id;
  if (alvo.dataset.acao) d.acao = alvo.dataset.acao;
  if (alvo.dataset.rastro) d.rastro = alvo.dataset.rastro;
  if (alvo.dataset.nome) d.nome = alvo.dataset.nome;
  if (alvo.dataset.ambiente) d.ambiente = alvo.dataset.ambiente;
  if (alvo.dataset.rotina) d.rotina = alvo.dataset.rotina;
  if (alvo.getAttribute('role') === 'tab') d.aba = alvo.id || alvo.textContent.trim();
  if (alvo.disabled) d.desabilitado = true;
  const texto = (alvo.textContent || alvo.value || alvo.title || '').trim();
  if (texto && !/password|senha/i.test(alvo.type || '')) d.texto = texto.slice(0, 80);
  return d;
}

function valorDoRastro(el) {
  if (!el) return undefined;
  if (/password|senha/i.test(el.type || '') || /senha|password/i.test(el.id || '')) return '***';
  if (el.type === 'checkbox' || el.type === 'radio') return el.checked;
  return String(el.value ?? '').slice(0, 200);
}

function ligarRastro() {
  document.addEventListener('click', (ev) => {
    const d = alvoDoRastro(ev.target);
    if (d) rastro('clique', d);
  }, true);
  document.addEventListener('change', (ev) => {
    const el = ev.target;
    if (!el || !('value' in el)) return;
    rastro('campo', { id: el.id || el.name || el.tagName.toLowerCase(),
                      valor: valorDoRastro(el) });
  }, true);
  document.addEventListener('keydown', (ev) => {
    if (ev.key === 'Escape' || ev.key === 'Enter' || ev.key === 'F5' || ev.ctrlKey) {
      rastro('tecla', { tecla: ev.key, ctrl: ev.ctrlKey, alvo: (ev.target && ev.target.id) || '' });
    }
  }, true);
  window.addEventListener('beforeunload', () => rastro('janela', { evento: 'fechando' }));
  document.addEventListener('visibilitychange', () =>
    rastro('janela', { visivel: document.visibilityState === 'visible' }));
}

/* ── Diagnóstico (zip para o suporte) ──────────────────── */

async function gerarDiagnostico() {
  const btn = $('#btn-diagnostico');
  btn.disabled = true;
  abrirLog(true);
  escreverLinha({ level: 'INFO', text: '[DIAG] Gerando o pacote de diagnóstico…' });
  try {
    const r = await api.gerar_diagnostico();
    if (r.ok) {
      escreverLinha({ level: 'INFO',
                      text: `[DIAG] Pacote gerado e aberto no Explorer: ${r.arquivo} — anexe na mensagem para o suporte.` });
    } else {
      escreverLinha({ level: 'ERROR', text: `[DIAG] ${r.erro || 'Falha ao gerar o pacote.'}` });
    }
  } finally {
    btn.disabled = false;
  }
}

/* ── Painel de log (sobreposição do rodapé) ────────────── */

/* Nasce contraído a cada abertura do programa, mesmo com "Fixar" ligado:
   fixar governa o fechamento automático, não o estado inicial. */
function abrirLog(abrir) {
  const painel = $('#panel-log');
  if (painel.dataset.aberto !== String(abrir)) rastro('log', { aberto: abrir });
  painel.dataset.aberto = String(abrir);
  $('#btn-log-toggle').setAttribute('aria-expanded', String(abrir));
  // "Fixar" só faz sentido com o painel aberto.
  $('#campo-fixar').hidden = !abrir;
  if (abrir) rolarLogAtivo();
}

function logAberto() {
  return $('#panel-log').dataset.aberto === 'true';
}

/* ── Log por ambiente ──────────────────────────────────── */

/* Linha com `ambiente` vai para a aba daquela instância (criada na hora);
   sem ambiente, para a "Geral". A barra de abas só aparece com mais de uma. */
function consoleDe(ambiente) {
  const nome = ambiente || '';
  let painel = $$('#log-paineis .console').find(c => c.dataset.ambiente === nome);
  if (painel) return painel;
  painel = document.createElement('div');
  painel.className = 'console';
  painel.dataset.ambiente = nome;
  painel.id = 'console-' + nome.replace(/[^A-Za-z0-9_-]/g, '_');
  painel.setAttribute('role', 'log');
  painel.setAttribute('aria-label', 'Log de ' + nome);
  painel.hidden = true;
  $('#log-paineis').appendChild(painel);

  const aba = document.createElement('button');
  aba.type = 'button';
  aba.className = 'aba';
  aba.setAttribute('role', 'tab');
  aba.setAttribute('aria-selected', 'false');
  aba.setAttribute('aria-controls', painel.id);
  aba.dataset.ambiente = nome;
  aba.textContent = nome;
  aba.addEventListener('click', () => trocarAbaLog(nome));
  $('#log-abas').appendChild(aba);
  $('#log-abas').hidden = false;
  return painel;
}

function abaLogAtiva() {
  const aba = $$('#log-abas .aba').find(a => a.getAttribute('aria-selected') === 'true');
  return aba ? aba.dataset.ambiente : '';
}

function trocarAbaLog(ambiente) {
  for (const aba of $$('#log-abas .aba')) {
    const ativa = aba.dataset.ambiente === ambiente;
    aba.setAttribute('aria-selected', String(ativa));
    if (ativa) delete aba.dataset.nivel;      // vista: apaga o sinal de aviso
  }
  for (const painel of $$('#log-paineis .console')) {
    painel.hidden = painel.dataset.ambiente !== ambiente;
  }
  rolarLogAtivo();
}

function rolarLogAtivo() {
  if (!logAberto() || !$('#chk-autoscroll').checked) return;
  const painel = $$('#log-paineis .console').find(c => !c.hidden);
  if (painel) painel.scrollTop = painel.scrollHeight;
}

function limparLog() {
  for (const painel of $$('#log-paineis .console')) {
    if (painel.dataset.ambiente) painel.remove(); else painel.innerHTML = '';
  }
  for (const aba of $$('#log-abas .aba')) {
    if (aba.dataset.ambiente) aba.remove();
  }
  trocarAbaLog('');
  $('#log-abas').hidden = true;
}

function escreverLinha(ev) {
  const consoleEl = consoleDe(ev.ambiente);
  const linha = document.createElement('span');
  linha.className = 'l l-' + ev.level;
  linha.textContent = ev.text;
  consoleEl.appendChild(linha);
  while (consoleEl.childElementCount > MAX_LINHAS) consoleEl.firstElementChild.remove();
  // Aviso ou erro numa aba que não está à vista: marca a aba.
  if (consoleEl.hidden && (ev.level === 'WARNING' || ev.level === 'ERROR')) {
    const aba = $$('#log-abas .aba').find(a => a.dataset.ambiente === (ev.ambiente || ''));
    if (aba && aba.dataset.nivel !== 'ERROR') aba.dataset.nivel = ev.level;
  }
  // Contraído, o console tem altura zero e a rolagem não teria efeito — ela é
  // reposta ao abrir.
  if (!consoleEl.hidden && logAberto() && $('#chk-autoscroll').checked) {
    consoleEl.scrollTop = consoleEl.scrollHeight;
  }
}

/* ── Gate e habilitação ────────────────────────────────── */

function atualizarBotoes() {
  // Gate mestre: sem o Gerenciador aberto nada é liberado, nem importar.
  const link = state.link === true;
  const pronto = link && state.configValida === true;
  const livre = !state.ocupado;
  const temSel = state.selecionado !== null && state.importados.includes(state.selecionado);

  const rodando = state.execucao.ativa === true;
  const mapa = [
    ['#btn-importar', link && pronto && livre && !rodando],
    ['#btn-inventario', true],
    ['#btn-configuracao', temSel],   // config é dado local: não exige o link
    ['#btn-excluir', link && temSel && livre && !rodando],
    ['#btn-executar-tir', state.podeExecutar === true],
    ['#btn-abortar-tir', rodando],
  ];
  for (const [sel, habilitado] of mapa) $(sel).disabled = !habilitado;

  // O motivo vem do backend, que é quem conhece todas as travas.
  explicarBotao('#btn-executar-tir', !state.podeExecutar, state.motivoExecutar);

  // Diz por que está desabilitado, em vez de só apagar o botão.
  explicarBotao('#btn-importar', !link, state.linkMotivo);
  explicarBotao('#btn-importar', link && !pronto,
    'O Gerenciador está sem conexão SQL válida — resolva lá primeiro.');
  explicarBotao('#btn-excluir', link && !temSel, 'Selecione um ambiente na lista.');
  explicarBotao('#btn-configuracao', !temSel, 'Selecione um ambiente na lista.');
  explicarBotao('#btn-abortar-tir', !rodando, 'Nenhuma execução em andamento.');
  explicarBotao('#btn-executar-tir', true,
    'Execução do TIR entra na próxima etapa do NebulaTIR.');

  renderGate(link);
}

function explicarBotao(sel, mostrar, motivo) {
  const el = $(sel);
  if (mostrar) el.title = motivo;
  else if (el.title === motivo) el.removeAttribute('title');
}

/** Mostra (ou esconde) o aviso de dependência e a ênfase no chip do link. */
function renderGate(link) {
  document.body.classList.toggle('sem-link', !link);
  const box = $('#gate');
  box.hidden = link;
  if (link) return;

  const verificando = state.link === null;
  $('#gate-msg').textContent = verificando
    ? 'Procurando o Gerenciador de Ambientes…'
    : state.linkMotivo;
  $('#detail-empty').hidden = true;
  $('#detail-body').hidden = true;
}

/* ── Modais: infraestrutura ────────────────────────────── */

let focoAnterior = null;

function abrirModal(id) {
  rastro('modal', { id, aberto: true });
  focoAnterior = document.activeElement;
  const ov = document.getElementById(id);
  ov.hidden = false;
  const alvo = ov.querySelector('button:not(:disabled), input, select');
  if (alvo) alvo.focus();
  ov.addEventListener('keydown', prenderFoco);
}

function fecharModal(id) {
  const ov = document.getElementById(id);
  if (!ov.hidden) rastro('modal', { id, aberto: false });
  ov.hidden = true;
  ov.removeEventListener('keydown', prenderFoco);
  if (focoAnterior && document.contains(focoAnterior)) focoAnterior.focus();
}

function prenderFoco(ev) {
  const ov = ev.currentTarget;
  if (ev.key === 'Escape') { ev.preventDefault(); fecharModal(ov.id); return; }
  if (ev.key !== 'Tab') return;
  const focaveis = $$('button, input, select, [href]', ov)
    .filter(el => !el.disabled && el.offsetParent !== null);
  if (!focaveis.length) return;
  const primeiro = focaveis[0];
  const ultimo = focaveis[focaveis.length - 1];
  if (ev.shiftKey && document.activeElement === primeiro) { ev.preventDefault(); ultimo.focus(); }
  else if (!ev.shiftKey && document.activeElement === ultimo) { ev.preventDefault(); primeiro.focus(); }
}

/* ── Importar ambiente ─────────────────────────────────── */

async function abrirImportar() {
  const r = await api.listar_disponiveis();
  const erro = $('#importar-erro');
  erro.hidden = true;
  state.disponiveis = r.ok ? r.ambientes : [];
  state.disponivelSel = null;

  if (!r.ok) {
    erro.hidden = false;
    erro.textContent = r.erro;
  }

  const lista = $('#lista-disponiveis');
  lista.innerHTML = '';
  for (const amb of state.disponiveis) {
    const li = document.createElement('li');
    li.className = 'conn-item';
    li.dataset.nome = amb.nome;
    li.dataset.status = amb.estado;
    li.setAttribute('role', 'option');
    li.setAttribute('aria-selected', 'false');
    li.tabIndex = 0;
    li.innerHTML = '<span class="nome"></span><span class="meta"></span>'
                 + '<span class="led" aria-hidden="true"></span>';
    $('.nome', li).textContent = amb.nome;
    $('.meta', li).textContent = [amb.port ? ':' + amb.port : '', amb.versao]
      .filter(Boolean).join('  ');
    const escolher = () => escolherDisponivel(amb.nome);
    li.addEventListener('click', escolher);
    li.addEventListener('keydown', ev => {
      if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); escolher(); }
    });
    lista.appendChild(li);
  }

  const vazio = r.ok && !state.disponiveis.length;
  $('#importar-vazio').hidden = !vazio;
  $('#importar-vazio').textContent = r.ok && r.total_no_gerenciador === 0
    ? 'O Gerenciador ainda não tem nenhum ambiente cadastrado.'
    : 'Todos os ambientes do Gerenciador já foram importados.';
  lista.hidden = vazio;
  $('#btn-confirmar-importar').disabled = true;
  abrirModal('overlay-importar');
}

function escolherDisponivel(nome) {
  state.disponivelSel = nome;
  $$('#lista-disponiveis .conn-item').forEach(li => {
    li.setAttribute('aria-selected', String(li.dataset.nome === nome));
  });
  $('#btn-confirmar-importar').disabled = false;
}

async function confirmarImportar() {
  if (!state.disponivelSel) return;
  const r = await api.importar_ambiente(state.disponivelSel);
  if (!r.ok) {
    const erro = $('#importar-erro');
    erro.hidden = false;
    erro.textContent = r.erro;
    return;
  }
  const nome = r.nome;
  fecharModal('overlay-importar');
  const s = await api.get_status();
  state.importados = s.importados;
  state.statusAmbientes = s.ambientes;
  renderLista();
  await selecionar(nome);
}

/* ── Configuração do TIR ───────────────────────────────── */

/* O formulário é montado a partir do esquema que o Python manda (`campos`),
   não escrito à mão no HTML: rótulo, tipo e trava ficam num lugar só
   (`services/config_tir.py`), e o arquivo gerado não sai do contrato do TIR. */

async function abrirConfiguracao() {
  if (!state.selecionado) return;
  const r = await api.obter_configuracao(state.selecionado);
  const erro = $('#config-erro');
  erro.hidden = true;
  if (!r.ok) {
    erro.hidden = false;
    erro.textContent = r.erro;
    return;
  }

  state.configCampos = r.campos;
  state.configAtual = r.config;
  $('#config-ambiente').textContent = r.nome;
  const local = r.local || null;
  $('#config-local-aviso').hidden = !local;
  $('#config-hint-padrao').hidden = !!local;
  if (local) $('#config-local-caminho').textContent = local.caminho;
  montarFormulario(r.campos, r.config);
  renderDivergencias(r.divergencias);
  renderFontes(r.fontes);
  abrirModal('overlay-configuracao');
}

/** Pasta dos fontes: preferência da máquina, mostrada aqui por conveniência.
    Vazio significa "detectar"; o texto abaixo diz de onde veio o caminho. */
function renderFontes(fontes) {
  if (!fontes) return;
  state.fontes = fontes;
  $('#raiz-testes').value = fontes.apontada || '';
  const situacao = $('#raiz-situacao');
  situacao.className = 'hint raiz-situacao';
  if (!fontes.existe) {
    situacao.dataset.estado = 'ausente';
    situacao.textContent = `Não encontrada: ${fontes.efetiva}`;
  } else if (fontes.detectada) {
    situacao.dataset.estado = 'detectada';
    situacao.textContent = `Detectada em ${fontes.efetiva}`;
  } else {
    situacao.dataset.estado = 'apontada';
    situacao.textContent = 'Apontada por você.';
  }
}

function montarFormulario(campos, config) {
  const form = $('#config-form');
  form.innerHTML = '';
  let grupoAtual = null;
  for (const campo of campos) {
    if (campo.grupo && campo.grupo !== grupoAtual) {
      grupoAtual = campo.grupo;
      const titulo = document.createElement('h3');
      titulo.className = 'grupo-titulo';
      titulo.textContent = grupoAtual;
      form.appendChild(titulo);
    }
    form.appendChild(construirCampo(campo, config[campo.chave]));
  }
}

function construirCampo(campo, valor) {
  const caixa = document.createElement('div');
  caixa.className = 'campo';
  if (campo.origem) caixa.dataset.origem = campo.origem;
  const id = 'cfg-' + campo.chave;

  if (campo.largo) caixa.classList.add('campo-largo');

  if (campo.tipo === 'switch') {
    const travado = Object.prototype.hasOwnProperty.call(campo, 'trava');
    caixa.innerHTML = `
      <div class="switch-campo" data-travado="${travado}">
        <span></span>
        <span class="switch-toggle">
          <input type="checkbox" id="${id}" data-chave="${campo.chave}">
          <span class="switch-trilho" aria-hidden="true"></span>
        </span>
      </div>`;
    $('.switch-campo > span', caixa).textContent = campo.rotulo;
    const entrada = $('input', caixa);
    entrada.checked = travado ? campo.trava : Boolean(valor);
    if (travado) {
      // `disabled` não vai ao servidor e some do foco; `readonly` não existe em
      // checkbox. Bloquear o evento mantém o campo legível, focável e travado.
      // O cinza do trilho é que diz "ligado e assim fica" — sem etiqueta.
      entrada.addEventListener('click', ev => ev.preventDefault());
      entrada.addEventListener('keydown', ev => {
        if (ev.key === ' ' || ev.key === 'Enter') ev.preventDefault();
      });
      entrada.setAttribute('aria-readonly', 'true');
      entrada.setAttribute('aria-describedby', id + '-trava');
      const nota = document.createElement('span');
      nota.id = id + '-trava';
      nota.className = 'sr-only';
      nota.textContent = 'Sempre ligado, não pode ser desativado.';
      caixa.appendChild(nota);
    }
    if (campo.ajuda) caixa.appendChild(dica(campo.ajuda, id));
    return caixa;
  }

  const rotulo = document.createElement('label');
  rotulo.setAttribute('for', id);
  rotulo.textContent = campo.rotulo;
  caixa.appendChild(rotulo);

  if (campo.tipo === 'combo') {
    const select = document.createElement('select');
    select.className = 'select';
    select.id = id;
    select.dataset.chave = campo.chave;
    const opcoes = campo.opcoes || [];
    // Valor gravado que não está mais disponível (navegador desinstalado) não
    // pode sumir calado do formulário.
    const lista = valor && !opcoes.includes(valor) ? [valor, ...opcoes] : opcoes;
    for (const opcao of lista) {
      const el = document.createElement('option');
      el.value = opcao;
      el.textContent = opcao === valor && !opcoes.includes(valor)
        ? `${opcao} (não instalado)` : opcao;
      select.appendChild(el);
    }
    select.value = valor || '';
    caixa.appendChild(select);
  } else if (campo.tipo === 'pasta') {
    const linha = document.createElement('div');
    linha.className = 'path-row';
    linha.innerHTML = `<input class="input mono" id="${id}" data-chave="${campo.chave}" required>
      <button type="button" class="icon-btn" id="${id}-btn"
              aria-label="Escolher pasta dos logs">📁</button>`;
    $('input', linha).value = valor || '';
    $('button', linha).addEventListener('click', () => escolherPastaLog(id));
    caixa.appendChild(linha);
  } else {
    const entrada = document.createElement('input');
    entrada.className = 'input';
    entrada.id = id;
    entrada.dataset.chave = campo.chave;
    entrada.value = valor === null || valor === undefined ? '' : String(valor);
    if (campo.tipo === 'numero') {
      entrada.type = 'number';
      entrada.min = campo.min ?? 1;
      entrada.max = campo.max ?? 3600;
      entrada.setAttribute('data-numeric', '');
    }
    if (campo.tipo === 'texto_fixo') {
      entrada.readOnly = true;
      entrada.setAttribute('aria-readonly', 'true');
    }
    if (campo.obrigatorio) {
      entrada.required = true;
      entrada.setAttribute('aria-required', 'true');
    }
    if (campo.chave === 'Url') entrada.classList.add('mono');
    caixa.appendChild(entrada);
  }

  if (campo.ajuda) caixa.appendChild(dica(campo.ajuda, id));
  return caixa;
}

function dica(texto, idCampo) {
  const p = document.createElement('p');
  p.className = 'hint';
  p.id = idCampo + '-ajuda';
  p.textContent = texto;
  const alvo = document.getElementById(idCampo);
  if (alvo) alvo.setAttribute('aria-describedby', p.id);
  return p;
}

async function aplicarRaiz(caminho) {
  const r = await api.salvar_raiz_testes(caminho);
  if (!r.ok) { mostrarErroConfig(r.erro); return; }
  $('#config-erro').hidden = true;
  renderFontes(r.fontes);
  // A lista de rotinas vem da raiz: trocar a pasta tem que recarregar já.
  await renderTestes();
}

function mostrarErroConfig(mensagem) {
  const erro = $('#config-erro');
  erro.hidden = false;
  erro.textContent = mensagem;
}

async function escolherPastaLog(idCampo) {
  const campo = document.getElementById(idCampo);
  const r = await api.escolher_pasta(campo.value || '');
  if (r.ok) campo.value = r.caminho;
}

function renderDivergencias(divergencias) {
  const caixa = $('#config-divergencia');
  caixa.hidden = !divergencias || !divergencias.length;
  if (caixa.hidden) return;
  const lista = $('#config-divergencia-lista');
  lista.innerHTML = '';
  for (const d of divergencias) {
    const li = document.createElement('li');
    li.textContent = `${d.chave}: aqui "${d.guardado || '—'}", no Gerenciador "${d.gerenciador}"`;
    lista.appendChild(li);
  }
  state.configDivergencias = divergencias;
}

function adotarDoGerenciador() {
  for (const d of state.configDivergencias || []) {
    const campo = document.getElementById('cfg-' + d.chave);
    if (campo) campo.value = d.gerenciador;
  }
  $('#config-divergencia').hidden = true;
}

function lerFormulario() {
  const config = {};
  for (const el of $$('#config-form [data-chave]')) {
    config[el.dataset.chave] = el.type === 'checkbox' ? el.checked : el.value;
  }
  return config;
}

/** Campo de digitação vazio: avisa e leva o foco até ele, em vez de mandar
    para o backend e devolver uma mensagem genérica no rodapé. */
function primeiroVazio() {
  return $$('#config-form input[required]').find(el => !el.value.trim()) || null;
}

async function salvarConfiguracao() {
  const erro = $('#config-erro');

  const vazio = primeiroVazio();
  if (vazio) {
    const rotulo = $(`label[for="${vazio.id}"]`);
    erro.hidden = false;
    erro.textContent = `O campo “${rotulo ? rotulo.textContent : vazio.id}” é obrigatório.`;
    vazio.focus();
    return;
  }

  const r = await api.salvar_configuracao(state.selecionado, lerFormulario());
  if (!r.ok) {
    erro.hidden = false;
    erro.textContent = r.erro;
    return;
  }
  erro.hidden = true;
  state.configAtual = r.config;
  fecharModal('overlay-configuracao');
}

/* ── Excluir importado ─────────────────────────────────── */

function abrirExcluir() {
  if (!state.selecionado) return;
  $('#excluir-msg').textContent =
    `Remover "${state.selecionado}" do NebulaTIR?`;
  abrirModal('overlay-excluir');
}

function mostrarErroBanco(mensagem) {
  const erro = $('#banco-erro');
  erro.hidden = !mensagem;
  erro.textContent = mensagem || '';
}

function mostrarErroRpo(mensagem) {
  const erro = $('#rpo-erro');
  erro.hidden = !mensagem;
  erro.textContent = mensagem || '';
}

async function confirmarExcluir() {
  // O mesmo modal confirma dois destinos; o alvo diz qual.
  if (state.exclusaoParalelos && state.exclusaoParalelos.length) {
    const alvos = state.exclusaoParalelos;
    state.exclusaoParalelos = null;
    // Devolve na hora: a corrida de exclusão roda em thread e a UI acompanha
    // pelo polling, mostrando qual ambiente está saindo.
    const r = await api.excluir_paralelos(alvos);
    fecharModal('overlay-excluir');
    if (!r.ok) mostrarErroParalelos(r.erro || 'Falha ao iniciar a exclusão.');
    abrirLog(true);
    await renderParalelos();
    return;
  }

  const alvo = state.selecionado;
  const r = await api.remover_importado(alvo);
  fecharModal('overlay-excluir');
  if (!r.ok) return;
  state.selecionado = null;
  $('#detalhe-ambiente').textContent = '—';
  const s = await api.get_status();
  state.importados = s.importados;
  renderLista();
  await renderDetalhes();
  atualizarBotoes();
}

/* ── Eventos ───────────────────────────────────────────── */

function ligarEventos() {
  ligarAbas();
  ligarRastro();

  $('#btn-expandir-tudo').addEventListener('click', () => abrirTodasAsRotinas(true));
  $('#btn-contrair-tudo').addEventListener('click', () => abrirTodasAsRotinas(false));
  $('#btn-limpar-resultado').addEventListener('click', limparResultados);

  // ── atualização do programa ──
  $('#chip-update').addEventListener('click', () => abrirAtualizacao());
  $('#btn-atualizacao').addEventListener('click', () => abrirAtualizacao());
  for (const { aba } of ABAS_CONFIG) {
    $(aba).addEventListener('click', () => trocarAbaConfig(aba));
  }
  $('#btn-fechar-upd').addEventListener('click',
    () => fecharModal('overlay-atualizacao'));

  $('#btn-upd-verificar').addEventListener('click', async () => {
    await api.atualizacao_verificar();     // roda em thread; o loop repinta
    await atualizarUpdate();
  });

  $('#btn-upd-baixar').addEventListener('click', async () => {
    await api.atualizacao_baixar();
    await atualizarUpdate();
  });

  // Fecha a janela; o main_web.py aplica a versão baixada e relança.
  $('#btn-upd-reiniciar').addEventListener('click', () => api.fechar_janela(true));

  $('#chk-upd-auto').addEventListener('change', async (ev) => {
    await api.atualizacao_configurar(ev.target.checked, null);
    await atualizarUpdate();
  });

  $('#btn-upd-reverter').addEventListener('click', async () => {
    const r = await api.atualizacao_reverter();
    // A troca já aconteceu em disco, mas quem está rodando é o binário
    // renomeado: a versão anterior só aparece quando o programa reiniciar —
    // e o botão de reiniciar fica à mão para isso.
    await atualizarUpdate();
    pintarModalUpdate(r.ok ? r.mensagem : r.erro);
    if (r.ok) $('#btn-upd-reiniciar').hidden = false;
  });

  $('#btn-importar').addEventListener('click', abrirImportar);
  $('#btn-cancelar-importar').addEventListener('click', () => fecharModal('overlay-importar'));
  $('#btn-fechar-importar').addEventListener('click', () => fecharModal('overlay-importar'));
  $('#btn-confirmar-importar').addEventListener('click', confirmarImportar);

  $('#btn-configuracao').addEventListener('click', abrirConfiguracao);
  $('#btn-cancelar-config').addEventListener('click', () => fecharModal('overlay-configuracao'));
  $('#btn-fechar-config').addEventListener('click', () => fecharModal('overlay-configuracao'));
  $('#btn-salvar-config').addEventListener('click', salvarConfiguracao);
  $('#btn-adotar-gerenciador').addEventListener('click', adotarDoGerenciador);

  // ── Fontes dos testes (preferência da máquina, não do config.json) ──
  $('#btn-raiz-pasta').addEventListener('click', async () => {
    const escolha = await api.escolher_pasta($('#raiz-testes').value || '');
    if (!escolha.ok) return;
    await aplicarRaiz(escolha.caminho);
  });
  $('#btn-raiz-detectar').addEventListener('click', async () => {
    const r = await api.detectar_raiz_testes();
    renderFontes(r.fontes);
    if (!r.ok) mostrarErroConfig(r.erro);
    else await renderTestes();
  });
  $('#raiz-testes').addEventListener('change', ev => aplicarRaiz(ev.target.value));

  $('#btn-excluir').addEventListener('click', abrirExcluir);
  $('#btn-cancelar-excluir').addEventListener('click', () => fecharModal('overlay-excluir'));
  $('#btn-fechar-excluir').addEventListener('click', () => fecharModal('overlay-excluir'));
  $('#btn-confirmar-excluir').addEventListener('click', confirmarExcluir);


  $('#btn-limpar-log').addEventListener('click', limparLog);
  $('#aba-log-geral').addEventListener('click', () => trocarAbaLog(''));
  $('#btn-diagnostico').addEventListener('click', gerarDiagnostico);

  // ── Log: abrir, fechar e fixar ──
  // O alvo é a barra inteira, não só a seta. O botão do título não tem
  // handler próprio: o clique dele borbulha para cá, o que mantém o teclado
  // funcionando sem alternar duas vezes.
  $('#log-barra').addEventListener('click', ev => {
    if ($('#log-barra .log-tools').contains(ev.target)) return;
    abrirLog(!logAberto());
  });

  $('#chk-fixar').addEventListener('change', async ev => {
    const r = await api.salvar_preferencias({ log_fixado: ev.target.checked });
    if (r.ok) state.preferencias = r.preferencias;
  });

  // Sem "Fixar", clicar fora contrai. Com "Fixar", o painel permanece —
  // é a única forma de acompanhar o log enquanto se mexe na tela.
  document.addEventListener('mousedown', ev => {
    if (!logAberto() || $('#chk-fixar').checked) return;
    if (!$('#panel-log').contains(ev.target)) abrirLog(false);
  });

  // Teclado também fecha o que o clique fecha.
  document.addEventListener('keydown', ev => {
    if (ev.key === 'Escape' && logAberto() && !$('#chk-fixar').checked
        && $$('.overlay:not([hidden])').length === 0) {
      abrirLog(false);
    }
  });

  // ── Modo e limite ──
  for (const botao of $$('.seg[data-modo]')) {
    botao.addEventListener('click', async () => {
      const r = await api.salvar_preferencias({ modo: botao.dataset.modo });
      if (r.ok) {
        state.preferencias = r.preferencias;
        renderModo();
        // Trocar de modo mostra ou esconde o cartão dos paralelos.
        await Promise.all([renderPortas(), renderParalelos()]);
      }
    });
  }
  $('#chk-dividir-casos').addEventListener('change', async ev => {
    const r = await api.salvar_preferencias({ dividir_casos: ev.target.checked });
    if (r.ok) { state.preferencias = r.preferencias; renderModo(); }
  });
  for (const [sel, chave] of [['#chk-restaurar-banco', 'restaurar_banco'],
                              ['#chk-restaurar-banco-local', 'restaurar_banco_local']]) {
    $(sel).addEventListener('change', async ev => {
      const r = await api.salvar_preferencias({ [chave]: ev.target.checked });
      if (r.ok) { state.preferencias = r.preferencias; renderModo(); }
    });
  }
  $('#max-instancias').addEventListener('change', async ev => {
    const r = await api.salvar_preferencias({ max_instancias: ev.target.value });
    if (r.ok) { state.preferencias = r.preferencias; renderModo(); await renderPortas(); }
  });

  // ── Combobox de testes ──
  $('#btn-combo').addEventListener('click', () => {
    abrirCombo($('#combo-painel').hidden);
  });
  // Busca a cada tecla, mas sem uma chamada ao Python por caractere.
  let debounce = null;
  $('#busca-testes').addEventListener('input', ev => {
    state.buscaTestes = ev.target.value;
    clearTimeout(debounce);
    debounce = setTimeout(renderTestes, 180);
  });
  $('#busca-testes').addEventListener('keydown', ev => {
    if (ev.key === 'Escape') { ev.preventDefault(); abrirCombo(false); $('#btn-combo').focus(); }
  });
  document.addEventListener('click', ev => {
    if (!$('#combo-painel').hidden && !$('#combo-testes').contains(ev.target)) {
      abrirCombo(false);
    }
  });

  $('#btn-confirmar-testes').addEventListener('click', confirmarTestes);

  // ── Origem dos testes e pasta local ──
  for (const botao of $$('.seg[data-origem]')) {
    botao.addEventListener('click', () => trocarOrigemTestes(botao.dataset.origem));
  }
  $('#btn-pasta-local').addEventListener('click', escolherPastaLocal);
  $('#pasta-local-campo').addEventListener('change', ev => aplicarPastaLocalDigitada(ev.target.value));
  $('#pasta-local-campo').addEventListener('keydown', ev => {
    if (ev.key === 'Enter') { ev.preventDefault(); aplicarPastaLocalDigitada(ev.target.value); }
  });
  $('#btn-config-local-sim').addEventListener('click', ajustarConfigLocal);
  $('#btn-config-local-nao').addEventListener('click', () => fecharModal('overlay-config-local'));
  $('#btn-fechar-config-local').addEventListener('click', () => fecharModal('overlay-config-local'));

  // ── Ambientes paralelos ──
  $('#chk-todos-paralelos').addEventListener('change', ev => {
    state.paralelosDesmarcados = new Set();
    for (const caixa of $$('#lista-paralelos input[type="checkbox"]')) {
      caixa.checked = ev.target.checked;
      if (!ev.target.checked) state.paralelosDesmarcados.add(caixa.dataset.ambiente);
    }
    sincronizarTodos();
  });

  $('#btn-gerar-paralelos').addEventListener('click', () => gerarParalelos(false));
  $('#btn-confirmar-parar-clonar').addEventListener('click', async () => {
    fecharModal('overlay-parar-clonar');
    await gerarParalelos(true);
  });
  for (const sel of ['#btn-cancelar-parar-clonar', '#btn-fechar-parar-clonar']) {
    $(sel).addEventListener('click', () => fecharModal('overlay-parar-clonar'));
  }

  async function gerarParalelos(pararPai) {
    abrirLog(true);
    mostrarErroParalelos('');
    const r = await api.gerar_paralelos(state.selecionado, pararPai);
    if (r.precisa_parar) {
      // Pai no ar: clonar exige pará-lo. Só com o OK do usuário.
      $('#parar-clonar-msg').textContent = r.erro;
      abrirModal('overlay-parar-clonar');
      return;
    }
    if (r.erros && r.erros.length) {
      mostrarErroParalelos(r.erros.map(e => `${e.ambiente}: ${e.erro}`).join('\n'));
    } else if (!r.ok) {
      mostrarErroParalelos(r.erro || 'Falha ao gerar os paralelos.');
    }
    await renderParalelos();
    await renderInventario();
  }

  // ── Instâncias no disco ──
  $('#btn-inventario').addEventListener('click', renderInventario);
  $('#btn-medir-inventario').addEventListener('click', medirInventario);
  $('#btn-limpar-inventario').addEventListener('click', abrirConfirmacaoLimpeza);
  $('#btn-confirmar-limpeza').addEventListener('click', confirmarLimpeza);
  for (const sel of ['#btn-cancelar-limpeza', '#btn-fechar-limpeza']) {
    $(sel).addEventListener('click', () => { $('#overlay-limpeza').hidden = true; });
  }

  $('#btn-subir-todos').addEventListener('click', async () => {
    abrirLog(true);
    const todos = $$('#lista-paralelos input[type="checkbox"]')
      .map(c => c.dataset.ambiente);
    const r = await api.subir_paralelos(todos);
    mostrarErroParalelos(r.ok ? '' : (r.erro || (r.erros || [])
      .map(e => `${e.ambiente}: ${e.erro}`).join('\n')));
    await renderParalelos();
  });

  $('#btn-subir-selecionados').addEventListener('click', async () => {
    const alvos = paralelosSelecionados();
    if (!alvos.length) { mostrarErroParalelos('Nenhuma instância selecionada.'); return; }
    abrirLog(true);
    const r = await api.subir_paralelos(alvos);
    mostrarErroParalelos(r.ok ? '' : (r.erro || (r.erros || [])
      .map(e => `${e.ambiente}: ${e.erro}`).join('\n')));
    await renderParalelos();
  });

  $('#btn-parar-paralelos').addEventListener('click', async () => {
    const alvos = paralelosSelecionados();
    const r = await api.parar_paralelos(alvos);
    mostrarErroParalelos(r.ok ? '' : (r.erro || ''));
    await renderParalelos();
  });

  $('#btn-excluir-paralelos').addEventListener('click', async () => {
    const alvos = paralelosSelecionados();
    if (!alvos.length) { mostrarErroParalelos('Nenhuma instância selecionada.'); return; }
    $('#excluir-msg').textContent =
      `Excluir ${alvos.length} ambiente(s) paralelo(s)?\n\n${alvos.join('\n')}`;
    state.exclusaoParalelos = alvos;   // confirmação compartilha o modal
    abrirModal('overlay-excluir');
  });

  // ── Banco de dados ──
  $('#btn-restaurar-banco').addEventListener('click', async () => {
    abrirLog(true);
    const r = await api.restaurar_banco([state.selecionado]);
    mostrarErroBanco(r.ok ? '' : (r.erro || (r.erros || [])
      .map(e => `${e.ambiente}: ${e.erro}`).join('\n')));
    await renderParalelos();
  });
  $('#btn-restaurar-banco-paralelos').addEventListener('click', async () => {
    const alvos = paralelosSelecionados();
    if (!alvos.length) { mostrarErroBanco('Nenhuma instância selecionada.'); return; }
    abrirLog(true);
    const r = await api.restaurar_banco(alvos);
    mostrarErroBanco(r.ok ? '' : (r.erro || (r.erros || [])
      .map(e => `${e.ambiente}: ${e.erro}`).join('\n')));
    await renderParalelos();
  });

  // ── RPO ──
  $('#btn-guardar-rpo').addEventListener('click', async () => {
    const r = await api.guardar_rpo(state.selecionado);
    mostrarErroRpo(r.ok ? '' : r.erro);
    await renderParalelos();
  });
  $('#btn-rpo-ambiente').addEventListener('click', async () => {
    const r = await api.restaurar_rpo_ambiente(state.selecionado);
    mostrarErroRpo(r.ok ? '' : r.erro);
  });
  $('#btn-rpo-zerado').addEventListener('click', async () => {
    abrirLog(true);
    const r = await api.restaurar_rpo_zerado(state.selecionado);
    mostrarErroRpo(r.ok ? '' : r.erro);
  });

  // ── Execução do TIR ──
  $('#btn-executar-tir').addEventListener('click', async () => {
    // Abre o log: é lá que a execução aparece linha a linha.
    abrirLog(true);
    const r = await api.executar_tir(state.selecionado);
    if (!r.ok) escreverLinha({ level: 'ERROR', text: r.erro });
  });
  $('#btn-abortar-tir').addEventListener('click', async () => {
    await api.abortar_tir();
  });

  // Setas navegam a lista sem tirar o foco dela (padrão de listbox).
  $('#env-list').addEventListener('keydown', ev => {
    if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(ev.key)) return;
    ev.preventDefault();
    if (!state.importados.length) return;
    const atual = state.importados.indexOf(state.selecionado);
    let alvo = atual;
    if (ev.key === 'ArrowDown') alvo = Math.min(atual + 1, state.importados.length - 1);
    else if (ev.key === 'ArrowUp') alvo = Math.max(atual - 1, 0);
    else if (ev.key === 'Home') alvo = 0;
    else alvo = state.importados.length - 1;
    if (atual === -1) alvo = 0;
    selecionar(state.importados[alvo]);
  });
}
