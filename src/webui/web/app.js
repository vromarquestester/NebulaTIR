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
  execucao: { ativa: false, rotinas: [] },
  paralelosDesmarcados: new Set(),   // padrão é tudo marcado
  andamento: {},                     // fase em curso no Gerenciador
  exclusao: { ativa: false },        // exclusão de paralelos em andamento
  inventario: [],                    // instâncias no disco, com situação
  limpeza: { ativa: false },         // remoção do que o Gerenciador não alcança
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

  await Promise.all([renderPortas(), renderTestes(), renderArvore(),
                     renderParalelos(), renderInventario()]);
}

/* ── Modo, limite e portas ─────────────────────────────── */

function renderModo() {
  const modo = state.preferencias.modo || 'sequencial';
  for (const botao of $$('.seg')) {
    botao.setAttribute('aria-checked', String(botao.dataset.modo === modo));
  }
  $('#max-instancias').value = state.preferencias.max_instancias ?? 3;
  // O limite só tem efeito em paralelo — em sequencial o campo sai da tela em
  // vez de ficar ali pedindo um número que não muda nada.
  // A linha de campos só existe em paralelo: nem o número de instâncias nem a
  // divisão de casos mudam alguma coisa em sequencial.
  $('#modo-campos').hidden = modo !== 'paralelo';
  $('#chk-dividir-casos').checked = state.preferencias.dividir_casos === true;

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

async function renderTestes() {
  const r = await api.listar_testes(state.selecionado, state.buscaTestes || '');
  const lista = $('#lista-testes');
  const vazio = $('#testes-vazio');
  lista.innerHTML = '';

  if (!r.ok) {
    vazio.hidden = false;
    vazio.textContent = r.erro;
    $('#testes-origem').textContent = '—';
    atualizarResumoCombo();
    return;
  }

  $('#testes-origem').textContent =
    `${r.pais} · ${r.total} rotinas em ${r.raiz}`;

  if (!r.rotinas.length) {
    vazio.hidden = false;
    vazio.textContent = state.buscaTestes
      ? 'Nenhuma rotina com esse trecho no nome.'
      : 'Nenhuma rotina para este país.';
    atualizarResumoCombo();
    return;
  }

  vazio.hidden = true;
  for (const rotina of r.rotinas) {
    if (rotina.selecionada) state.selecaoTestes.add(rotina.rotina);
    const li = document.createElement('li');
    li.className = 'combo-item';
    li.setAttribute('role', 'option');
    li.dataset.semCase = String(!rotina.tem_case);
    li.setAttribute('aria-selected', String(state.selecaoTestes.has(rotina.rotina)));
    li.innerHTML = `<input type="checkbox" tabindex="-1" aria-hidden="true">
      <span class="rotina"></span><span class="modulo"></span>`;
    $('.rotina', li).textContent = rotina.rotina;
    $('.modulo', li).textContent =
      `${rotina.modulo} · ${rotina.casos.length} casos`;
    $('input', li).checked = state.selecaoTestes.has(rotina.rotina);
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
  if (state.selecaoTestes.has(nome)) state.selecaoTestes.delete(nome);
  else state.selecaoTestes.add(nome);
  const marcado = state.selecaoTestes.has(nome);
  $('input', li).checked = marcado;
  li.setAttribute('aria-selected', String(marcado));
  atualizarResumoCombo();
}

function atualizarResumoCombo() {
  const total = state.selecaoTestes.size;
  $('#combo-resumo').textContent = total
    ? `${total} rotina${total > 1 ? 's' : ''} marcada${total > 1 ? 's' : ''}`
    : 'Selecionar rotinas…';
}

function abrirCombo(abrir) {
  $('#combo-painel').hidden = !abrir;
  $('#btn-combo').setAttribute('aria-expanded', String(abrir));
  if (abrir) $('#busca-testes').focus();
}

/* ── Árvore da seleção confirmada ──────────────────────── */

async function renderArvore() {
  const r = await api.get_selecao(state.selecionado);
  const caixa = $('#arvore-testes');
  caixa.innerHTML = '';
  if (!r.ok || !r.arvore.length) {
    $('#arvore-vazia').hidden = false;
    return;
  }
  $('#arvore-vazia').hidden = true;

  for (const rotina of r.arvore) {
    const bloco = document.createElement('details');
    bloco.className = 'arvore-rotina';
    bloco.dataset.ausente = String(rotina.ausente);
    // Contraída: com várias rotinas confirmadas, tudo aberto vira uma parede
    // de casos e esconde a própria lista de rotinas.
    bloco.open = false;
    bloco.innerHTML = `<summary>
        <span class="nome"></span>
        <span class="modulo"></span>
        <span class="contagem"></span>
      </summary><ul class="arvore-casos"></ul>`;
    $('.nome', bloco).textContent = rotina.rotina;
    $('.modulo', bloco).textContent = rotina.ausente
      ? 'não encontrada no disco' : rotina.modulo;
    $('.contagem', bloco).textContent = rotina.casos.length;

    const casos = $('.arvore-casos', bloco);
    for (const caso of rotina.casos) {
      const li = document.createElement('li');
      li.textContent = caso;
      casos.appendChild(li);
    }
    caixa.appendChild(bloco);
  }
}

async function confirmarTestes() {
  const r = await api.salvar_selecao(state.selecionado, [...state.selecaoTestes]);
  if (!r.ok) return;
  abrirCombo(false);
  await renderArvore();
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

    // Andamento da corrida e liberação do botão vêm do backend, que conhece
    // todas as travas (VPN, SQL, seleção, execução já em curso).
    state.execucao = await api.estado_execucao();
    const liberado = await api.pode_executar(state.selecionado || '');
    state.podeExecutar = liberado.ok === true;
    state.motivoExecutar = liberado.motivo || '';
    renderExecucao();

    atualizarBotoes();
  } catch (e) { /* janela fechando */ }
  setTimeout(loopStatus, 2000);
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

const MARCA_ESTADO = { fila: '·', rodando: '▸', ok: '✓', erro: '✕', abortado: '■' };

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

  // O rótulo segue o que a lista realmente é naquele momento. Durante a
  // corrida ela é a espera; no fim, o resultado de tudo que rodou.
  const naFila = rotinas.filter(r => r.estado === 'fila').length;
  const titulo = $('#exec-fases-titulo');
  titulo.textContent = state.execucao.ativa === false
    ? 'Resultado por rotina'
    : naFila
      ? `Rotinas na fila (${naFila})`
      : 'Rotinas desta corrida';

  const lista = $('#exec-fases');
  lista.innerHTML = '';
  for (const item of rotinas) {
    const li = document.createElement('li');
    li.dataset.estado = item.estado === 'rodando' ? 'ativo'
      : item.estado === 'ok' ? 'ok'
      : (item.estado === 'erro' || item.estado === 'abortado') ? 'erro' : '';
    li.innerHTML = '<span class="marca" aria-hidden="true"></span><span class="texto"></span>';
    $('.marca', li).textContent = MARCA_ESTADO[item.estado] || '·';
    // O PNG é o relatório que a pessoa realmente olha: vira link quando existe.
    const texto = $('.texto', li);
    texto.textContent = item.rotina + (item.mensagem ? ` — ${item.mensagem}` : '');
    if (item.png) {
      const abrir = document.createElement('button');
      abrir.type = 'button';
      abrir.className = 'link';
      abrir.textContent = 'ver relatório';
      abrir.onclick = () => api.abrir_arquivo(item.png);
      texto.appendChild(document.createTextNode(' '));
      texto.appendChild(abrir);
    }
    lista.appendChild(li);
  }

  const final = $('#exec-final');
  final.hidden = state.execucao.ativa !== false || !rotinas.length;
  if (!final.hidden) {
    const falhou = rotinas.some(r => r.estado === 'erro' || r.estado === 'abortado');
    final.dataset.estado = falhou ? 'erro' : 'ok';
    final.textContent = falhou ? 'Execução terminada com falhas'
                               : 'Execução concluída com sucesso';
  }
}

/* ── Painel de log (sobreposição do rodapé) ────────────── */

/* Nasce contraído a cada abertura do programa, mesmo com "Fixar" ligado:
   fixar governa o fechamento automático, não o estado inicial. */
function abrirLog(abrir) {
  const painel = $('#panel-log');
  painel.dataset.aberto = String(abrir);
  $('#btn-log-toggle').setAttribute('aria-expanded', String(abrir));
  // "Fixar" só faz sentido com o painel aberto.
  $('#campo-fixar').hidden = !abrir;
  if (abrir && $('#chk-autoscroll').checked) {
    const consoleEl = $('#console');
    consoleEl.scrollTop = consoleEl.scrollHeight;
  }
}

function logAberto() {
  return $('#panel-log').dataset.aberto === 'true';
}

function escreverLinha(ev) {
  const consoleEl = $('#console');
  const linha = document.createElement('span');
  linha.className = 'l l-' + ev.level;
  linha.textContent = ev.text;
  consoleEl.appendChild(linha);
  while (consoleEl.childElementCount > MAX_LINHAS) consoleEl.firstElementChild.remove();
  // Contraído, o console tem altura zero e a rolagem não teria efeito — ela é
  // reposta ao abrir.
  if (logAberto() && $('#chk-autoscroll').checked) {
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
    ['#btn-sincronizar', link],
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
  focoAnterior = document.activeElement;
  const ov = document.getElementById(id);
  ov.hidden = false;
  const alvo = ov.querySelector('button:not(:disabled), input, select');
  if (alvo) alvo.focus();
  ov.addEventListener('keydown', prenderFoco);
}

function fecharModal(id) {
  const ov = document.getElementById(id);
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

  $('#btn-sincronizar').addEventListener('click', async () => {
    const r = await api.sincronizar();
    if (r.ok) {
      const s = await api.get_status();
      state.importados = s.importados;
      state.statusAmbientes = s.ambientes;
      renderLista();
      await renderDetalhes();
    }
    atualizarBotoes();
  });

  $('#btn-limpar-log').addEventListener('click', () => { $('#console').innerHTML = ''; });

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
  for (const botao of $$('.seg')) {
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

  // ── Ambientes paralelos ──
  $('#chk-todos-paralelos').addEventListener('change', ev => {
    state.paralelosDesmarcados = new Set();
    for (const caixa of $$('#lista-paralelos input[type="checkbox"]')) {
      caixa.checked = ev.target.checked;
      if (!ev.target.checked) state.paralelosDesmarcados.add(caixa.dataset.ambiente);
    }
    sincronizarTodos();
  });

  $('#btn-gerar-paralelos').addEventListener('click', async () => {
    abrirLog(true);
    mostrarErroParalelos('');
    const r = await api.gerar_paralelos(state.selecionado);
    if (r.erros && r.erros.length) {
      mostrarErroParalelos(r.erros.map(e => `${e.ambiente}: ${e.erro}`).join('\n'));
    } else if (!r.ok) {
      mostrarErroParalelos(r.erro || 'Falha ao gerar os paralelos.');
    }
    await renderParalelos();
    await renderInventario();
  });

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
