import {
  analysisFields, categoryNames, errorText, fileError, formatBytes, formatDate,
  fragmentLabel, methodNames, modeNames, outcomeNames, plural, statusNames, typeNames,
} from './ui-core.mjs';
import { createFindingAssistant } from './assistant.mjs';

const $ = (selector) => document.querySelector(selector);
const state = {
  apiKey: '', capabilities: null, file: null, busy: false, report: null,
  reports: [], offset: 0, rules: [], pending: null, category: 'all',
  receipt: null, viewGeneration: 0, session: 0,
};
let progressTimer, toastTimer;

// All server/user content is inserted as text nodes, never interpreted as HTML.
function node(tag, attributes = {}, ...children) {
  const element = document.createElement(tag);
  for (const [key, value] of Object.entries(attributes)) {
    if (value === undefined || value === null) continue;
    if (key === 'class') element.className = value;
    else if (key === 'text') element.textContent = String(value);
    else if (key.startsWith('on') && typeof value === 'function') element.addEventListener(key.slice(2), value);
    else if (key in element && typeof value === 'boolean') element[key] = value;
    else element.setAttribute(key, String(value));
  }
  element.append(...children.filter((child) => child !== null && child !== undefined));
  return element;
}

function icon(name, className = '') {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('aria-hidden', 'true');
  if (className) svg.setAttribute('class', className);
  const use = document.createElementNS('http://www.w3.org/2000/svg', 'use');
  use.setAttribute('href', `/assets/icons.svg#${name}`);
  svg.append(use);
  return svg;
}

function badge(text, variant = '') { return node('span', { class: `badge ${variant ? `badge-${variant}` : ''}`, text }); }
function showError(id, message = '') { $(id).textContent = message; $(id).hidden = !message; }
function toast(message) {
  clearTimeout(toastTimer);
  $('#toast').textContent = message;
  $('#toast').hidden = false;
  toastTimer = setTimeout(() => { $('#toast').hidden = true; }, 4000);
}
function currentView() { return ['reports', 'rules'].includes(location.hash.slice(1)) ? location.hash.slice(1) : 'analyze'; }
function setView(name) { if (currentView() === name) navigate(); else location.hash = name; }
function actionButton(text, callback, iconName, primary = false) {
  return node('button', { type: 'button', class: `button ${primary ? 'button-primary' : ''}`, onclick: callback }, ...(iconName ? [icon(iconName)] : []), text);
}

class RequestError extends Error {
  constructor(status, message) { super(message); this.status = status; }
}

async function api(path, options = {}, key = state.apiKey) {
  const response = await fetch(`/api/v1/${path}`, {
    ...options, credentials: 'omit', cache: 'no-store', redirect: 'error',
    headers: { ...options.headers, 'X-API-Key': key },
  });
  let body;
  try { body = await response.json(); } catch { body = null; }
  if (!response.ok) throw new RequestError(response.status, errorText(response.status, body?.detail));
  if (body === null) throw new Error('Сервер вернул ответ, который не удалось прочитать. Проверьте отчёты перед повтором.');
  return { body, headers: response.headers };
}

const findingAssistant = createFindingAssistant({ api, node, icon, detailBlock,
  getReport: () => state.report, getCapabilities: () => state.capabilities,
  unauthorized: (failure) => { clearSession(); openConnection(); showError('#connection-error', failure.message); }, toast });

function authNeeded() {
  if (state.apiKey) return false;
  openConnection();
  return true;
}

function openConnection() {
  if (state.busy) return;
  showError('#connection-error');
  $('#api-key').value = '';
  $('#api-key').required = !state.apiKey;
  $('#disconnect-button').hidden = !state.apiKey;
  $('#connect-button').textContent = state.apiKey ? 'Сменить ключ' : 'Подключиться';
  if (!$('#connection-dialog').open) $('#connection-dialog').showModal();
  $('#api-key').focus();
}

function clearSession() {
  findingAssistant.clear();
  state.apiKey = '';
  state.capabilities = null;
  state.report = null;
  state.reports = [];
  state.rules = [];
  state.receipt = null;
  state.pending = null;
  state.offset = 0;
  state.session += 1;
  state.viewGeneration += 1;
  $('#report-screen').replaceChildren();
  $('#report-screen').hidden = true;
  $('#upload-screen').hidden = false;
  $('#reports-content').replaceChildren();
  $('#rules-content').replaceChildren();
  $('#reports-pagination').hidden = true;
  $('#report-search').value = '';
  $('#rule-search').value = '';
  $('#connection-label').textContent = 'Подключить';
  $('#connection-dot').classList.remove('connected');
  $('#external-consent').checked = false;
  $('#model-check').checked = false;
  $('#language-check').checked = false;
  updateCapabilities();
}

function handleError(error, target) {
  const message = error instanceof RequestError ? error.message : 'Не удалось получить ответ сервера. Запрос мог быть принят: проверьте «Отчёты» перед повтором. Повтор этой формы использует тот же ключ операции.';
  showError(target, message);
  if (error.status === 401) {
    clearSession();
    openConnection();
    showError('#connection-error', message);
  }
}

async function connect(event) {
  event.preventDefault();
  const key = $('#api-key').value.trim();
  if (!key) { showError('#connection-error', 'Введите ключ приложения.'); return; }
  $('#connect-button').disabled = true;
  $('#close-connection').disabled = true;
  $('#disconnect-button').disabled = true;
  showError('#connection-error');
  try {
    const { body } = await api('capabilities', {}, key);
    clearSession();
    state.apiKey = key;
    state.capabilities = body;
    $('#api-key').value = '';
    $('#connection-dialog').close();
    $('#connection-label').textContent = 'Подключено';
    $('#connection-dot').classList.add('connected');
    updateCapabilities();
    navigate();
    toast('Сервис подключён');
  } catch (error) {
    $('#api-key').value = '';
    showError('#connection-error', error instanceof RequestError ? error.message : 'Сервис недоступен. Проверьте, что приложение запущено.');
  } finally {
    $('#connect-button').disabled = false;
    $('#close-connection').disabled = false;
    $('#disconnect-button').disabled = false;
  }
}

function updateCapabilities() {
  const caps = state.capabilities;
  const languageAvailable = Boolean(caps?.local_language_checks_available);
  const modelAvailable = Boolean(caps?.external_llm_enabled && caps?.current_provider === 'openclaw');
  $('#language-check').disabled = !languageAvailable;
  $('#model-check').disabled = !modelAvailable;
  if (!languageAvailable) $('#language-check').checked = false;
  if (!modelAvailable) $('#model-check').checked = false;
  $('#language-help').textContent = !caps ? 'Локальный LanguageTool. Доступность — после подключения.' : languageAvailable ? 'Локальный LanguageTool · текст не передаётся внешней модели.' : 'LanguageTool не включён на сервере. Доступны остальные проверки.';
  $('#model-help').textContent = !caps ? 'Модель через OpenClaw. Доступность — после подключения.' : modelAvailable ? 'Внешняя модель через OpenClaw · нужно согласие на передачу.' : 'Модельная обработка выключена на сервере.';
  $('#upload-limit').textContent = `DOCX · до ${formatBytes(caps?.max_document_bytes || 5242880)}`;
  if (caps && !caps.policy_profiles?.includes('office_instruction_letters')) $('#policy-profile').value = '';
  $('#cache-check').disabled = Boolean(caps && !caps.analysis_cache?.enabled);
  updateModel();
}

function updateModel() {
  const enabled = $('#model-check').checked;
  $('#consent-box').hidden = !enabled;
  $('#external-consent').required = enabled;
  if (!enabled) $('#external-consent').checked = false;
  $('#privacy-label').textContent = enabled ? 'Передача модели — с вашего согласия' : 'Проверка без внешней модели';
}

function selectFile(file) {
  const message = fileError(file, state.capabilities?.max_document_bytes);
  showError('#file-error', message);
  if (message) {
    state.file = null;
    $('#file-input').value = '';
  } else state.file = file;
  state.pending = null;
  $('#selected-file').hidden = !state.file;
  $('#dropzone-idle').hidden = Boolean(state.file);
  $('#external-consent').checked = false;
  if (state.file) {
    $('#selected-filename').textContent = state.file.name;
    $('#selected-filesize').textContent = `${formatBytes(state.file.size)} · готов к проверке`;
  }
}

function readForm() {
  return analysisFields({
    use_llm: $('#model-check').checked, use_language_checks: $('#language-check').checked,
    use_cache: $('#cache-check').checked, allow_external_transfer: $('#external-consent').checked,
    policy_profile: $('#policy-profile').value, document_type: $('#document-type').value,
    external_document_id: $('#external-id').value, template_id: $('#template-id').value,
  });
}

function setBusy(busy, model = false) {
  state.busy = busy;
  $('#analysis-fields').disabled = busy;
  $('#analyze-button').disabled = busy;
  $('#connection-button').disabled = busy;
  $('#analysis-form').setAttribute('aria-busy', String(busy));
  $('#analysis-progress').hidden = !busy;
  $('#analyze-button span').textContent = busy ? 'Проверяем…' : 'Проверить письмо';
  clearInterval(progressTimer);
  if (busy) {
    const start = performance.now();
    $('#elapsed').textContent = '0 с';
    $('#progress-help').textContent = model ? 'Дожидаемся ответа сервера и модели. Это может занять больше минуты. Повторно отправлять файл не нужно.' : 'Сервер проверяет доступные свойства документа. Дождитесь ответа.';
    progressTimer = setInterval(() => { $('#elapsed').textContent = `${Math.floor((performance.now() - start) / 1000)} с`; }, 1000);
  }
}

async function analyze(event) {
  event.preventDefault();
  if (state.busy) return;
  if (authNeeded()) return;
  const invalid = fileError(state.file, state.capabilities.max_document_bytes);
  showError('#file-error', invalid);
  if (invalid) { $('#file-input').focus(); return; }
  let fields;
  try { fields = readForm(); } catch (error) { showError('#analysis-error', error.message); return; }
  const signature = JSON.stringify(fields);
  // Preserve the operation key after an uncertain response. Never automatically
  // retry or issue a new operation for the same pending form.
  const operation = state.pending?.file === state.file && state.pending.signature === signature
    ? state.pending : { key: crypto.randomUUID(), file: state.file, signature };
  state.pending = operation;
  const payload = new FormData();
  payload.append('file', operation.file);
  for (const [name, value] of Object.entries(fields)) payload.append(name, value);
  const model = fields.use_llm === 'true';
  // Consent is consumed by this Send, including failed requests.
  $('#external-consent').checked = false;
  showError('#analysis-error');
  setBusy(true, model);
  const started = performance.now();
  try {
    const { body, headers } = await api('analyze', { method: 'POST', body: payload, headers: { 'Idempotency-Key': operation.key } });
    state.pending = null;
    state.receipt = {
      cache: headers.get('Analysis-Cache'), replayed: headers.get('Idempotency-Replayed') === 'true',
      duration: performance.now() - started,
    };
    state.report = body;
    state.category = 'all';
    renderReport();
    setView('analyze');
    window.scrollTo({ top: 0, behavior: 'instant' });
    $('#report-title').focus({ preventScroll: true });
  } catch (error) {
    if (error.status && ![409, 500, 502, 503, 504].includes(error.status)) state.pending = null;
    handleError(error, '#analysis-error');
  } finally {
    setBusy(false);
    if (!state.apiKey) openConnection();
  }
}

function newAnalysis() {
  findingAssistant.clear();
  state.report = null;
  state.receipt = null;
  $('#report-screen').replaceChildren();
  $('#report-screen').hidden = true;
  $('#upload-screen').hidden = false;
  showError('#analysis-error');
  $('#file-input').value = '';
  state.file = null;
  state.pending = null;
  $('#selected-file').hidden = true;
  $('#dropzone-idle').hidden = false;
  $('#external-consent').checked = false;
  setView('analyze');
  window.scrollTo({ top: 0, behavior: 'instant' });
  $('#file-input').focus({ preventScroll: true });
}

function emptyState(title, description, iconName = 'file', button = null) {
  return node('div', { class: 'empty-state' }, icon(iconName), node('h2', { text: title }), node('p', { text: description }), button);
}

function detailBlock(label, content, code = false) {
  return node('div', { class: 'detail-block' }, node('span', { class: 'detail-label', text: label }), node(code ? 'pre' : 'p', { text: typeof content === 'object' ? JSON.stringify(content, null, 2) : content }));
}

function technicalDetails(finding) {
  const content = node('div', { class: 'technical-content' });
  for (const [label, key] of [['Ожидается', 'expected'], ['Обнаружено', 'observed'], ['Почему применено', 'applicability_reason'], ['Адрес в структуре DOCX', 'fragment_id'], ['Версия правила / движка', 'rule_version']]) {
    if (finding[key] !== null && finding[key] !== undefined) content.append(detailBlock(label, finding[key], ['expected', 'observed'].includes(key)));
  }
  if (!content.childElementCount) return null;
  return node('details', { class: 'technical-details' }, node('summary', { text: 'Технические данные' }), content);
}

function findingCard(finding, open, index) {
  const level = finding.severity || 'info';
  const summary = node('summary', {}, icon(level === 'info' ? 'info' : 'warning', `finding-icon ${level}`),
    node('div', { class: 'finding-title' }, node('span', { text: finding.message }),
      node('div', { class: 'finding-meta' }, badge(categoryNames[finding.category] || finding.category),
        node('span', { text: methodNames[finding.method] || finding.method }),
        level === 'critical' ? badge('Критично', 'critical') : null)), icon('chevron', 'chevron'));
  const body = node('div', { class: 'finding-body' });
  if (finding.evidence && finding.evidence !== finding.message) body.append(node('blockquote', { class: 'evidence', text: finding.evidence }));
  body.append(detailBlock('Что исправить', finding.recommendation || 'Проверьте замечание вручную.'));
  body.append(node('div', { class: 'finding-source' },
    node('span', { text: fragmentLabel(finding.fragment_id) }),
    node('code', { text: finding.source_ref || 'Нормативное основание не установлено' }),
    node('code', { text: finding.code })));
  const technical = technicalDetails(finding);
  if (technical) body.append(technical);
  body.append(node('div', { class: 'finding-actions' }, node('button', { type: 'button', class: 'button button-small', onclick: () => findingAssistant.open(index) }, icon('message'), 'Разобрать замечание')));
  return node('details', { class: 'panel finding-card', open }, summary, body);
}

function renderFindings() {
  const report = state.report;
  const findings = report.findings || [];
  const filtered = findings.filter((finding) => state.category === 'all' || finding.category === state.category);
  const list = $('#findings-list');
  list.replaceChildren();
  if (!findings.length) {
    const title = report.status === 'processing' ? 'Пока нет замечаний' : 'Замечаний в выполненных проверках не найдено';
    const description = report.status === 'processing' ? 'Это предварительный отчёт. Обработка ещё не зафиксирована; обновите результат позже.' : 'Это относится к выполненным проверкам. Посмотрите ограничения и пункты, для которых недостаточно данных.';
    const empty = emptyState(title, description, report.status === 'processing' ? 'history' : 'check');
    empty.classList.add('zero-findings');
    list.append(empty);
  } else if (!filtered.length) list.append(emptyState('В этой категории замечаний нет', 'Выберите другую категорию.', 'check'));
  else list.append(...filtered.map((finding) => findingCard(finding, filtered.length === 1, findings.indexOf(finding))));
  document.querySelectorAll('[data-category]').forEach((button) => button.setAttribute('aria-pressed', String(button.dataset.category === state.category)));
}

function reportSection(title, iconName, content, open = false) {
  return node('details', { class: 'panel report-section', open }, node('summary', {}, icon(iconName), title, icon('chevron', 'chevron')), node('div', { class: 'section-body' }, content));
}

function renderAudit(checks) {
  const outcomes = checks.results || [];
  const list = node('div');
  const select = node('select', { 'aria-label': 'Фильтр результатов проверок' },
    ...[['all', 'Все результаты'], ['violation', 'Отклонения'], ['insufficient_data', 'Недостаточно данных'], ['pass', 'Без отклонений']].map(([value, text]) => node('option', { value, text })));
  const counts = checks.counts || {};
  const toolbar = node('div', { class: 'audit-toolbar' }, node('div', { class: 'audit-counts' },
    ...Object.entries(outcomeNames).map(([code, name]) => node('span', { text: `${name}: ${counts[code] || 0}` }))), select);
  const render = () => {
    const filtered = outcomes.filter((result) => select.value === 'all' || result.outcome === select.value);
    list.replaceChildren(...filtered.map((result) => {
      const variant = { pass: 'success', violation: 'warning', insufficient_data: 'info' }[result.outcome] || '';
      return node('details', { class: 'audit-row' }, node('summary', {},
        node('div', {}, node('code', { text: result.code }), node('p', { text: fragmentLabel(result.fragment_id), class: 'muted small' })),
        badge(outcomeNames[result.outcome] || result.outcome, variant)),
      node('div', { class: 'audit-row-content' }, node('p', { text: result.message || result.reason }),
        node('p', { text: result.recommendation || '' }),
        technicalDetails({ ...result, applicability_reason: result.reason }),
        node('code', { text: result.source_ref || '' })));
    }));
    if (!filtered.length) list.append(node('p', { class: 'muted small', text: 'Результатов с таким исходом нет.' }));
  };
  select.addEventListener('change', render);
  render();
  return node('div', {}, toolbar, list, node('p', { class: 'field-help', text: '«Без отклонений» относится только к проверенным свойствам. Это не подтверждение полного соответствия инструкции.' }));
}

function downloadReport() {
  const data = new Blob([JSON.stringify(state.report, null, 2)], { type: 'application/json;charset=utf-8' });
  const url = URL.createObjectURL(data);
  const anchor = node('a', { href: url, download: `report-${String(state.report.id).replace(/[^a-zA-Z0-9-]/g, '')}.json` });
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function renderReport() {
  const report = state.report;
  const screen = $('#report-screen');
  screen.replaceChildren();
  $('#upload-screen').hidden = true;
  screen.hidden = false;
  const statusVariant = { completed: 'success', partial: 'warning', processing: 'info' }[report.status] || '';
  const back = node('button', { class: 'back-button', type: 'button', onclick: newAnalysis, text: '← Новая проверка' });
  const actions = node('div', { class: 'report-actions' }, actionButton('Скачать JSON', downloadReport, 'download'));
  if (report.status === 'processing') actions.prepend(actionButton('Обновить результат', () => openReport(report.id), 'refresh'));
  const metadata = node('div', { class: 'report-heading-meta' }, node('span', { text: formatDate(report.created_at) }), node('span', { text: modeNames[report.mode] || report.mode }));
  if (state.receipt?.cache === 'hit') metadata.append(badge('Из кэша', 'info'));
  if (state.receipt?.replayed) metadata.append(badge('Повтор операции', 'info'));
  screen.append(back, node('div', { class: 'page-heading report-heading' },
    node('div', {}, node('div', { class: 'heading-topline' },
      node('h1', { id: 'report-title', tabindex: '-1', text: report.filename }), badge(statusNames[report.status] || report.status, statusVariant)), metadata), actions));
  const notice = node('div', { id: 'report-error', class: 'notice notice-error', role: 'alert', hidden: true });
  screen.append(notice);
  if (report.status === 'partial') screen.append(node('div', { class: 'notice notice-warning', text: 'Проверка завершена не полностью. Доступные замечания сохранены; причину смотрите в предупреждениях ниже.' }));
  if (report.status === 'processing') screen.append(node('div', { class: 'notice', text: 'Предварительный результат. При прерывании сервера такой статус может сохраниться. Обновление отчёта не запускает модель повторно.' }));
  const findings = report.findings || [];
  const main = node('div', {}, node('div', { class: 'result-summary' }, node('h2', { text: plural(findings.length, ['замечание', 'замечания', 'замечаний']) }), node('span', { class: 'muted small', text: 'Проверьте перед исправлением' })));
  const tabs = node('div', { class: 'filter-tabs', role: 'group', 'aria-label': 'Категории замечаний' });
  for (const [code, title] of [['all', 'Все'], ...Object.entries(categoryNames)]) {
    const count = findings.filter((finding) => code === 'all' || finding.category === code).length;
    tabs.append(node('button', { type: 'button', class: 'filter-tab', 'data-category': code, 'aria-pressed': String(code === state.category), disabled: !count && code !== 'all', onclick: () => { state.category = code; renderFindings(); } }, title, node('span', { class: 'count', text: count })));
  }
  main.append(tabs, node('div', { id: 'findings-list' }));
  const warnings = report.warnings || [];
  if (warnings.length) main.append(reportSection(`Ограничения и предупреждения · ${warnings.length}`, 'info', node('ul', { class: 'warning-list' }, ...warnings.map((warning) => node('li', { text: warning }))), report.status === 'partial'));
  const policy = report.metrics?.policy_checks;
  if (policy?.results?.length) main.append(reportSection(`Все результаты проверок · ${policy.results.length}`, 'book', renderAudit(policy)));
  const timing = report.metrics?.timings_ms?.total_before_final_save_ms;
  const sidebar = node('aside', { class: 'report-sidebar', 'aria-label': 'Сведения об отчёте' }, node('h2', { text: 'О проверке' }));
  const pairs = [
    ['Применено правил', String(Object.keys(report.metrics?.applied_rules || {}).length)],
    ['Абзацев основной части', String(report.metrics?.paragraph_count ?? '—')],
    ['Тип письма', typeNames[report.document_type] || 'Не указан'],
    ['Орфография и грамматика', ({ completed: 'LanguageTool · выполнено', partial: 'LanguageTool · сбой', pending: 'В процессе' })[report.metrics?.language_check?.status] || 'Не запрашивались'],
    ['Реестр правил', report.ruleset_version || 'Не указан'],
  ];
  if (timing !== undefined && timing !== null) pairs.push(['Время исходного анализа', `${(timing / 1000).toLocaleString('ru-RU', { maximumFractionDigits: 2 })} с`]);
  if (state.receipt) pairs.push(['Ответ на этот запрос', `${(state.receipt.duration / 1000).toLocaleString('ru-RU', { maximumFractionDigits: 2 })} с`]);
  sidebar.append(node('dl', {}, ...pairs.flatMap(([title, value]) => [node('dt', { text: title }), node('dd', { text: value })])), node('p', { text: 'Нет замечаний ≠ нет ошибок. Сервис не вычисляет итоговый балл и не подтверждает полное соответствие ГОСТ.' }));
  const ids = node('details', { class: 'technical-details' }, node('summary', { text: 'Идентификаторы отчёта' }),
    node('div', { class: 'technical-content' }, detailBlock('ID отчёта', report.id), detailBlock('Исходный request_id', report.request_id || 'Не указан'), detailBlock('ID документа в СЭД', report.external_document_id || 'Не указан')));
  sidebar.append(ids);
  screen.append(node('div', { class: 'report-layout report-section' }, main, sidebar));
  renderFindings();
}

async function openReport(id) {
  if (authNeeded()) return;
  const session = state.session;
  try {
    const { body } = await api(`reports/${encodeURIComponent(id)}`);
    if (session !== state.session) return;
    state.report = body;
    state.receipt = null;
    state.category = 'all';
    renderReport();
    setView('analyze');
    window.scrollTo({ top: 0, behavior: 'instant' });
    $('#report-title').focus({ preventScroll: true });
  } catch (error) {
    if (session !== state.session) return;
    if (currentView() === 'reports') listError('#reports-content', error, loadReports);
    else handleError(error, '#report-error');
  }
}

function lockedState(target, description) {
  $(target).replaceChildren(emptyState('Подключитесь к сервису', description, 'lock', actionButton('Ввести ключ приложения', openConnection, 'key')));
}

function listError(target, error, retry) {
  const message = error instanceof RequestError ? error.message : 'Не удалось связаться с сервисом. Проверьте подключение и повторите.';
  $(target).replaceChildren(emptyState('Не удалось загрузить данные', message, 'warning', actionButton('Повторить', retry, 'refresh')));
  if (error.status === 401) { clearSession(); navigate(); openConnection(); showError('#connection-error', message); }
}

function loadingState(target) { $(target).replaceChildren(node('div', { class: 'empty-state', role: 'status' }, node('span', { class: 'spinner' }), node('p', { text: 'Загружаем…' }))); }

async function loadReports() {
  if (!state.apiKey) { lockedState('#reports-content', 'История доступна по ключу приложения. Отчёты хранятся на сервере.'); return; }
  const generation = ++state.viewGeneration;
  loadingState('#reports-content');
  $('#reports-pagination').hidden = true;
  try {
    const { body } = await api(`reports?limit=20&offset=${state.offset}`);
    if (generation !== state.viewGeneration) return;
    state.reports = body;
    renderReports();
  } catch (error) { if (generation === state.viewGeneration) listError('#reports-content', error, loadReports); }
}

function renderReports() {
  const search = $('#report-search').value.toLocaleLowerCase('ru');
  const reports = state.reports.filter((report) => report.filename.toLocaleLowerCase('ru').includes(search));
  $('#reports-count').textContent = plural(state.reports.length, ['отчёт загружен', 'отчёта загружено', 'отчётов загружено']);
  if (!state.reports.length && state.offset) $('#reports-content').replaceChildren(emptyState('Больше отчётов нет', 'Вернитесь на предыдущую страницу.', 'history'));
  else if (!state.reports.length) $('#reports-content').replaceChildren(emptyState('Здесь будут ваши отчёты', 'Загрузите письмо на вкладке «Проверка». Результат появится в этом списке.', 'history', actionButton('Проверить письмо', newAnalysis, 'check-file')));
  else if (!reports.length) $('#reports-content').replaceChildren(emptyState('Совпадений в этом списке нет', 'Измените поисковый запрос или перейдите на другую страницу.', 'search'));
  else $('#reports-content').replaceChildren(node('div', { class: 'panel' }, node('ul', { class: 'report-list' }, ...reports.map((report) => node('li', {}, node('div', { class: 'report-row' },
    node('div', { class: 'report-row-main' }, node('button', { type: 'button', class: 'report-name', text: report.filename, onclick: () => openReport(report.id) }), node('p', { text: `${formatDate(report.created_at)} · ${modeNames[report.mode] || report.mode}` })),
    node('div', { class: 'report-row-meta' }, node('span', { text: plural(report.findings?.length || 0, ['замечание', 'замечания', 'замечаний']) }), badge(statusNames[report.status] || report.status, report.status === 'completed' ? 'success' : report.status === 'partial' ? 'warning' : 'info'))))))));
  $('#reports-pagination').hidden = !state.reports.length && !state.offset;
  $('#previous-reports').disabled = state.offset === 0;
  $('#next-reports').disabled = state.reports.length < 20;
  $('#reports-page').textContent = `Страница ${state.offset / 20 + 1}`;
}

async function loadRules() {
  if (!state.apiKey) { lockedState('#rules-content', 'Введите ключ, чтобы посмотреть действующие правила и их применимость.'); return; }
  const generation = ++state.viewGeneration;
  loadingState('#rules-content');
  try {
    const { body } = await api('rules');
    if (generation !== state.viewGeneration) return;
    state.rules = body;
    renderRules();
  } catch (error) { if (generation === state.viewGeneration) listError('#rules-content', error, loadRules); }
}

function renderRules() {
  const search = $('#rule-search').value.toLocaleLowerCase('ru');
  const group = $('#rule-filter').value;
  const rules = state.rules.filter((rule) => (group === 'all' || rule.source.kind === group) && `${rule.code} ${rule.description}`.toLocaleLowerCase('ru').includes(search));
  $('#rules-count').textContent = plural(state.rules.length, ['правило', 'правила', 'правил']);
  if (!rules.length) { $('#rules-content').replaceChildren(emptyState('Правила не найдены', 'Попробуйте изменить запрос или группу правил.', 'search')); return; }
  $('#rules-content').replaceChildren(node('div', { class: 'panel' }, ...rules.map((rule) => {
    const spec = rule.policy_specification;
    const content = node('div', { class: 'rule-detail' }, detailBlock('Когда применяется', rule.applicability.explanation), detailBlock('Рекомендация', rule.recommendation), detailBlock('Основание', `${rule.source.title} · ${rule.source.ref}`));
    if (spec?.exceptions) content.append(detailBlock('Исключения', spec.exceptions));
    if (spec?.expected) content.append(detailBlock('Ожидаемые свойства', spec.expected, true));
    content.append(node('p', { class: 'field-help', text: rule.source.kind === 'instruction' ? 'Профиль выбирается явно в форме. Реализована часть требований; источник предоставлен владельцем и требует экспертного подтверждения.' : 'Локальная эвристика. Это не обязательное требование ГОСТ.' }));
    return node('details', { class: 'rule-card' }, node('summary', {}, icon('chevron', 'chevron'), node('div', { class: 'rule-card-title' }, node('code', { text: `${rule.code} · v${rule.version}` }), node('strong', { text: rule.description })), badge(rule.enabled ? rule.source.kind === 'instruction' ? 'Инструкция' : 'Эвристика' : 'Выключено')), content);
  })));
}

function navigate() {
  const view = currentView();
  state.viewGeneration += 1;
  for (const name of ['analyze', 'reports', 'rules']) $(`#${name}-view`).hidden = name !== view;
  document.querySelectorAll('[data-view]').forEach((link) => {
    if (link.dataset.view === view) link.setAttribute('aria-current', 'page');
    else link.removeAttribute('aria-current');
  });
  document.title = `${({ analyze: 'Проверка письма', reports: 'Отчёты', rules: 'Правила' })[view]} · SED DSS`;
  if (view === 'reports') loadReports();
  if (view === 'rules') loadRules();
}

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  $('#theme-toggle').setAttribute('aria-label', theme === 'dark' ? 'Включить светлую тему' : 'Включить тёмную тему');
}
let theme;
try { theme = localStorage.getItem('sed-dss-theme'); } catch { /* Storage may be disabled. */ }
applyTheme(['light', 'dark'].includes(theme) ? theme : matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
$('#theme-toggle').addEventListener('click', () => {
  const next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
  applyTheme(next);
  try { localStorage.setItem('sed-dss-theme', next); } catch { /* Theme remains in memory. */ }
});
$('#connection-button').addEventListener('click', openConnection);
$('#close-connection').addEventListener('click', () => $('#connection-dialog').close());
$('#connection-dialog').addEventListener('close', () => { $('#api-key').value = ''; });
$('#connection-form').addEventListener('submit', connect);
$('#disconnect-button').addEventListener('click', () => { clearSession(); $('#connection-dialog').close(); navigate(); toast('Ключ и открытые отчёты удалены из памяти интерфейса'); });
$('#analysis-form').addEventListener('submit', analyze);
$('#file-input').addEventListener('change', (event) => { if (event.target.files[0]) selectFile(event.target.files[0]); });
$('#model-check').addEventListener('change', updateModel);
$('#policy-profile').addEventListener('change', () => { $('#profile-help').textContent = $('#policy-profile').value ? 'Профиль предоставленной инструкции: реализованные проверки оформления и реквизитов, вместе с базовыми эвристиками.' : 'Пять локальных эвристик. Профиль инструкции можно выбрать отдельно.'; });
$('#analysis-fields').addEventListener('change', (event) => { if (event.target.id !== 'external-consent') $('#external-consent').checked = false; });
const dropzone = $('#dropzone');
for (const name of ['dragenter', 'dragover']) dropzone.addEventListener(name, (event) => { event.preventDefault(); if (!state.busy) dropzone.classList.add('dragging'); });
for (const name of ['dragleave', 'drop']) dropzone.addEventListener(name, (event) => { event.preventDefault(); dropzone.classList.remove('dragging'); });
dropzone.addEventListener('drop', (event) => { if (!state.busy && event.dataTransfer.files[0]) selectFile(event.dataTransfer.files[0]); });
// Dropping a document outside the upload zone must not navigate away from the app.
window.addEventListener('dragover', (event) => event.preventDefault());
window.addEventListener('drop', (event) => event.preventDefault());
$('#refresh-reports').addEventListener('click', loadReports);
$('#report-search').addEventListener('input', () => { if (state.apiKey) renderReports(); });
$('#rule-search').addEventListener('input', () => { if (state.apiKey) renderRules(); });
$('#rule-filter').addEventListener('change', () => { if (state.apiKey) renderRules(); });
$('#previous-reports').addEventListener('click', () => { state.offset = Math.max(0, state.offset - 20); loadReports(); });
$('#next-reports').addEventListener('click', () => { state.offset += 20; loadReports(); });
window.addEventListener('hashchange', navigate);
window.addEventListener('pagehide', () => { state.apiKey = ''; $('#api-key').value = ''; });
window.addEventListener('pageshow', (event) => { if (event.persisted) { clearSession(); navigate(); } });
navigate();
