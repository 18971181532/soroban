'use strict';

/* Soroban 工作台前端：无框架、无构建步骤。
   状态只有一个：服务端返回的整份表格。每次写入都用新状态整体替换，
   省掉了所有增量同步的麻烦。 */

const state = {
  data: null,
  sheet: null,
  selected: 'A1',
  precedents: new Set(),
  dependents: new Set(),
  auditToken: 0,
};

const $ = (selector) => document.querySelector(selector);

const escapeHtml = (text) => String(text ?? '').replace(/[&<>"']/g, (ch) => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
}[ch]));

function letters(col) {
  let value = col + 1;
  let out = '';
  while (value > 0) {
    const rem = (value - 1) % 26;
    out = String.fromCharCode(65 + rem) + out;
    value = Math.floor((value - 1) / 26);
  }
  return out;
}

function parseRef(ref) {
  const match = /^([A-Z]+)(\d+)$/.exec(ref);
  if (!match) return { row: 0, col: 0 };
  let col = 0;
  for (const ch of match[1]) col = col * 26 + (ch.charCodeAt(0) - 64);
  return { row: Number(match[2]) - 1, col: col - 1 };
}

function makeRef(row, col) {
  return `${letters(col)}${row + 1}`;
}

function splitFull(text) {
  const index = String(text).lastIndexOf('!');
  if (index < 0) return [state.sheet, String(text)];
  return [text.slice(0, index), text.slice(index + 1)];
}

function currentSheet() {
  if (!state.data) return null;
  return state.data.sheets.find((item) => item.name === state.sheet) || state.data.sheets[0];
}

function cellOf(ref) {
  const sheet = currentSheet();
  if (!sheet) return null;
  return sheet.cells.find((cell) => cell.ref === ref) || null;
}

function gridSize() {
  const sheet = currentSheet();
  const rows = Math.max(20, Math.min(42, (sheet?.rows || 0) + 3));
  const cols = Math.max(9, Math.min(12, (sheet?.cols || 0) + 1));
  return { rows, cols };
}

/* ---------------- 网络 ---------------- */
async function api(path, body) {
  const options = body === undefined ? {} : {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  };
  const response = await fetch(path, options);
  let data = {};
  try {
    data = await response.json();
  } catch (err) {
    throw new Error(`服务返回了非 JSON 内容（${response.status}）`);
  }
  if (!response.ok) throw new Error(data.error || `请求失败（${response.status}）`);
  return data;
}

/* ---------------- 渲染 ---------------- */
function render() {
  if (!state.data) return;
  const sheet = currentSheet();
  if (!sheet) return;

  renderTabs();

  const { rows, cols } = gridSize();
  const cells = new Map(sheet.cells.map((cell) => [cell.ref, cell]));

  let html = '<table><thead><tr><th class="corner"></th>';
  for (let col = 0; col < cols; col += 1) html += `<th>${letters(col)}</th>`;
  html += '</tr></thead><tbody>';

  for (let row = 0; row < rows; row += 1) {
    html += `<tr><th>${row + 1}</th>`;
    for (let col = 0; col < cols; col += 1) {
      const ref = makeRef(row, col);
      const cell = cells.get(ref);
      const type = cell ? cell.type : 'blank';
      const classes = ['cell', type === 'number' ? 'num' : type];
      if (state.selected === ref) classes.push('selected');
      if (state.precedents.has(ref)) classes.push('precedent');
      if (state.dependents.has(ref)) classes.push('dependent');
      const text = cell ? cell.display : '';
      const tip = cell ? `${cell.raw}${cell.error ? '  ·  ' + cell.error : ''}` : '';
      html += `<td class="${classes.join(' ')}" data-ref="${ref}" title="${escapeHtml(tip)}">${escapeHtml(text)}</td>`;
    }
    html += '</tr>';
  }
  html += '</tbody></table>';

  $('#grid').innerHTML = html;
}

function renderTabs() {
  $('#tabs').innerHTML = state.data.sheets
    .map((sheet) => `<button data-sheet="${escapeHtml(sheet.name)}" class="${sheet.name === state.sheet ? 'active' : ''}">${escapeHtml(sheet.name)}</button>`)
    .join('');
}

function renderStats() {
  const stats = state.data.stats;
  const millis = (stats.seconds * 1000).toFixed(1);
  $('#stats').innerHTML = `
    <div><b>${stats.computed}</b><span>公式</span></div>
    <div><b>${millis}</b><span>毫秒</span></div>
    <div><b>${stats.cycles}</b><span>循环</span></div>`;
  $('#recalc-mode').textContent = stats.full ? '全量重算' : '增量重算';
}

function renderAudit(data) {
  const body = $('#audit-body');
  const labels = {
    formula: '公式', number: '数字', text: '文本', bool: '逻辑',
    blank: '空', bad: '语法错误',
  };
  const kind = labels[data.kind] || data.kind;
  const precedents = (data.tree && data.tree.precedents) || [];
  const dependents = data.dependents || [];

  const chips = precedents.length
    ? precedents.map((item) => `
        <button class="chip" data-ref="${escapeHtml(item.cell)}" data-sheet="${escapeHtml(item.sheet)}">
          <span>${escapeHtml(item.cell)}</span><b>${escapeHtml(item.value || '空')}</b>
        </button>`).join('')
    : '<span class="empty-line">没有依赖，这是一个输入格。</span>';

  const links = dependents.length
    ? dependents.map((ref) => {
        const [sheet, cell] = splitFull(ref);
        return `<button class="chip" data-ref="${escapeHtml(cell)}" data-sheet="${escapeHtml(sheet)}"><span>${escapeHtml(cell)}</span></button>`;
      }).join('')
    : '<span class="empty-line">还没有别的格子引用它。</span>';

  body.innerHTML = `
    <div class="audit-title">
      <span class="audit-ref">${escapeHtml(data.ref)}</span>
      <span class="tag ${escapeHtml(data.kind)}">${escapeHtml(kind)}</span>
    </div>
    <div class="audit-formula">${escapeHtml(data.raw || '（空单元格）')}</div>
    <div class="audit-value">${escapeHtml(data.value || '—')}</div>
    <div class="audit-sub">由这些格子算出</div>
    <div class="chips">${chips}</div>
    <div class="audit-sub">被这些格子引用</div>
    <div class="chips">${links}</div>`;
}

function updateFormulaBar() {
  const sheet = currentSheet();
  if (!sheet) return;
  $('#address').textContent = `${sheet.name}!${state.selected}`;
  const cell = cellOf(state.selected);
  $('#formula').value = cell ? cell.raw : '';
}

function highlight() {
  document.querySelectorAll('#grid td[data-ref]').forEach((td) => {
    td.classList.toggle('precedent', state.precedents.has(td.dataset.ref));
    td.classList.toggle('dependent', state.dependents.has(td.dataset.ref));
  });
}

/* ---------------- 选择与编辑 ---------------- */
function selectCell(ref, { focus = false } = {}) {
  state.selected = ref;
  document.querySelectorAll('#grid td.selected').forEach((td) => td.classList.remove('selected'));
  const td = document.querySelector(`#grid td[data-ref="${ref}"]`);
  if (td) {
    td.classList.add('selected');
    td.scrollIntoView({ block: 'nearest', inline: 'nearest' });
  }
  updateFormulaBar();
  if (focus) $('#grid').focus({ preventScroll: true });
  loadAudit();
}

async function loadAudit() {
  const token = (state.auditToken += 1);
  const full = `${state.sheet}!${state.selected}`;
  try {
    const data = await api(`/api/audit?cell=${encodeURIComponent(full)}&depth=2`);
    if (token !== state.auditToken) return;
    renderAudit(data);
    state.precedents = new Set(
      ((data.tree && data.tree.precedents) || [])
        .filter((item) => item.sheet === state.sheet)
        .map((item) => item.cell),
    );
    state.dependents = new Set(
      (data.dependents || [])
        .filter((ref) => ref.startsWith(`${state.sheet}!`))
        .map((ref) => ref.split('!')[1]),
    );
    highlight();
  } catch (err) {
    if (token !== state.auditToken) return;
    $('#audit-body').innerHTML = `<p class="placeholder">${escapeHtml(err.message)}</p>`;
  }
}

function startEdit(initial) {
  const input = $('#formula');
  if (initial !== undefined) input.value = initial;
  input.focus();
  input.setSelectionRange(input.value.length, input.value.length);
}

async function commit(value) {
  const full = `${state.sheet}!${state.selected}`;
  try {
    const data = await api('/api/set', { changes: { [full]: value } });
    applyState(data);
    setStatus(value === '' ? `已清空 ${full}` : `已更新 ${full}`);
  } catch (err) {
    setStatus(err.message, true);
  }
}

function applyState(data) {
  state.data = data;
  if (!data.sheets.some((sheet) => sheet.name === state.sheet)) {
    state.sheet = data.sheets[0].name;
  }
  const version = `v${data.version}`;
  $('#version').textContent = version;
  $('#foot').textContent = `Soroban ${version}`;
  render();
  updateFormulaBar();
  renderStats();
  highlight();
}

function setStatus(text, isError = false) {
  const node = $('#status');
  node.textContent = text;
  node.classList.toggle('error', Boolean(isError));
}

function moveSelection(deltaRow, deltaCol) {
  const { row, col } = parseRef(state.selected);
  const { rows, cols } = gridSize();
  const nextRow = Math.min(Math.max(row + deltaRow, 0), rows - 1);
  const nextCol = Math.min(Math.max(col + deltaCol, 0), cols - 1);
  selectCell(makeRef(nextRow, nextCol), { focus: true });
}

/* ---------------- 目标搜索 ---------------- */
function formatNumber(value) {
  if (typeof value !== 'number' || !Number.isFinite(value)) return String(value);
  if (Number.isInteger(value)) return String(value);
  return value.toFixed(6).replace(/0+$/, '').replace(/\.$/, '');
}

async function runGoal() {
  const target = $('#goal-target').value.trim();
  const variable = $('#goal-variable').value.trim();
  const raw = $('#goal-value').value.trim();
  const box = $('#goal-result');
  if (!target || !variable || raw === '') {
    box.className = 'result fail';
    box.textContent = '三栏都要填。';
    return;
  }
  box.className = 'result';
  box.textContent = '求解中……';
  try {
    const data = await api('/api/goal', {
      target, variable, value: Number(raw), apply: $('#goal-apply').checked,
    });
    const result = data.result;
    if (result.success) {
      const [sheet, ref] = splitFull(result.variable);
      if (data.state.sheets.some((item) => item.name === sheet)) {
        state.sheet = sheet;
        state.selected = ref;
      }
      applyState(data.state);
      selectCell(ref);
      box.innerHTML = `收敛：<strong>${escapeHtml(result.variable)}</strong> = `
        + `<strong>${formatNumber(result.variable_value)}</strong>，`
        + `目标值 <strong>${formatNumber(result.achieved)}</strong>`
        + `（${result.iterations} 次迭代）`;
      setStatus(`目标搜索完成：${result.variable} = ${formatNumber(result.variable_value)}`);
    } else {
      box.className = 'result fail';
      box.textContent = result.message || '没有找到解。';
      setStatus('目标搜索没有收敛', true);
    }
  } catch (err) {
    box.className = 'result fail';
    box.textContent = err.message;
    setStatus(err.message, true);
  }
}

/* ---------------- 事件 ---------------- */
$('#grid').addEventListener('click', (event) => {
  const td = event.target.closest('td[data-ref]');
  if (!td) return;
  selectCell(td.dataset.ref, { focus: true });
});

$('#grid').addEventListener('dblclick', (event) => {
  const td = event.target.closest('td[data-ref]');
  if (!td) return;
  selectCell(td.dataset.ref);
  startEdit();
});

$('#tabs').addEventListener('click', (event) => {
  const button = event.target.closest('button[data-sheet]');
  if (!button) return;
  state.sheet = button.dataset.sheet;
  state.selected = 'A1';
  state.precedents = new Set();
  state.dependents = new Set();
  render();
  updateFormulaBar();
  selectCell('A1');
});

$('#audit-body').addEventListener('click', (event) => {
  const chip = event.target.closest('button.chip');
  if (!chip) return;
  const sheet = chip.dataset.sheet;
  if (sheet && sheet !== state.sheet && state.data.sheets.some((item) => item.name === sheet)) {
    state.sheet = sheet;
    state.selected = chip.dataset.ref;
    render();
    updateFormulaBar();
    selectCell(chip.dataset.ref);
    return;
  }
  selectCell(chip.dataset.ref);
});

$('#formula').addEventListener('keydown', (event) => {
  if (event.key === 'Enter') {
    event.preventDefault();
    const value = event.target.value;
    event.target.blur();
    commit(value);
  } else if (event.key === 'Escape') {
    event.preventDefault();
    event.target.blur();
    updateFormulaBar();
    setStatus('已取消编辑');
  } else if (event.key === 'Tab') {
    event.preventDefault();
    const value = event.target.value;
    event.target.blur();
    commit(value).then(() => moveSelection(0, 1));
  }
});

document.addEventListener('keydown', (event) => {
  if (event.target.tagName === 'INPUT' || event.target.tagName === 'TEXTAREA') return;
  if (event.ctrlKey || event.metaKey || event.altKey) return;
  if (!state.data) return;

  if (event.key === 'ArrowDown') { event.preventDefault(); moveSelection(1, 0); return; }
  if (event.key === 'ArrowUp') { event.preventDefault(); moveSelection(-1, 0); return; }
  if (event.key === 'ArrowLeft') { event.preventDefault(); moveSelection(0, -1); return; }
  if (event.key === 'ArrowRight') { event.preventDefault(); moveSelection(0, 1); return; }
  if (event.key === 'Enter') { event.preventDefault(); startEdit(); return; }
  if (event.key === 'Delete' || event.key === 'Backspace') {
    event.preventDefault();
    commit('');
    return;
  }
  if (event.key.length === 1) {
    event.preventDefault();
    startEdit(event.key);
  }
});

$('#btn-goal').addEventListener('click', runGoal);

$('#btn-demo').addEventListener('click', async () => {
  try {
    const data = await api('/api/reset', {});
    state.sheet = data.sheets[0].name;
    state.selected = 'D8';
    applyState(data);
    selectCell('D8');
    setStatus('示例已重新载入');
  } catch (err) {
    setStatus(err.message, true);
  }
});

$('#btn-recalc').addEventListener('click', async () => {
  try {
    const data = await api('/api/recalc', {});
    applyState(data);
    setStatus(`全量重算完成：${data.stats.computed} 个公式，${(data.stats.seconds * 1000).toFixed(1)} ms`);
  } catch (err) {
    setStatus(err.message, true);
  }
});

/* ---------------- 启动 ---------------- */
async function boot() {
  try {
    const data = await api('/api/state');
    state.sheet = data.sheets[0].name;
    const first = data.sheets[0];
    state.selected = first.cells.some((cell) => cell.ref === 'D8') ? 'D8' : 'A1';
    applyState(data);
    await loadAudit();
    setStatus(`引擎就绪 · ${data.sheets.length} 张表 · ${data.stats.computed} 个公式`);
  } catch (err) {
    setStatus(`连接失败：${err.message}`, true);
  }
}

document.addEventListener('DOMContentLoaded', boot);
