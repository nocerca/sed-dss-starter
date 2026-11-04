import { assistantFields } from './ui-core.mjs';

// The assistant is a client of the public REST contract. No persistent chat or
// document storage, direct model calls or DOM interpretation of model content.
export function createFindingAssistant({ api, node, icon, detailBlock, getReport, getCapabilities, unauthorized, toast }) {
  const $ = (id) => document.querySelector(id);
  const dialog = $('#assistant-dialog');
  let target = null, pending = null, busy = false, generation = 0;

  function error(message = '') { $('#assistant-error').textContent = message; $('#assistant-error').hidden = !message; }
  function modelAvailable() {
    const caps = getCapabilities();
    return Boolean(caps?.external_llm_enabled && caps?.current_provider === 'openclaw');
  }
  function clearAnswer() { $('#assistant-answer').replaceChildren(); $('#assistant-answer').hidden = true; }
  function setBusy(value, model = false) {
    busy = value;
    $('#assistant-fields').disabled = value;
    $('#assistant-submit').disabled = value;
    $('#assistant-new-attempt').disabled = value;
    $('#close-assistant').disabled = value;
    $('#assistant-progress').hidden = !value;
    $('#assistant-progress-text').textContent = model ? 'Модель готовит ответ. Ожидание может занять больше минуты.' : 'Готовим локальное объяснение…';
    $('#assistant-form').setAttribute('aria-busy', String(value));
  }
  function updateMode() {
    const action = $('#assistant-action').value;
    const requiresModel = ['question', 'rephrase'].includes(action);
    const available = modelAvailable();
    $('#assistant-use-model').disabled = requiresModel || !available;
    if (requiresModel) $('#assistant-use-model').checked = available;
    if (!available) $('#assistant-use-model').checked = false;
    $('#assistant-question-field').hidden = action !== 'question';
    $('#assistant-question').required = action === 'question';
    $('#assistant-text-field').hidden = !requiresModel;
    $('#assistant-text').required = action === 'rephrase';
    $('#assistant-text-optional').hidden = action === 'rephrase';
    const useModel = $('#assistant-use-model').checked;
    $('#assistant-consent-box').hidden = !useModel;
    $('#assistant-consent').required = useModel;
    $('#assistant-mode-help').textContent = !available
      ? requiresModel ? 'Для этого действия включите модель на сервере. Объяснение и шаги исправления доступны локально.' : 'Модель выключена на сервере. Доступен локальный ответ.'
      : requiresModel ? 'Этот запрос требует модели и отдельного согласия на передачу.' : 'Без переключателя ответ формируется локально по сохранённым фактам и версии правила.';
    $('#assistant-submit').textContent = useModel ? 'Отправить модели' : 'Получить ответ';
    $('#assistant-submit').disabled = requiresModel && !available;
    $('#assistant-consent').checked = false;
  }
  function fields() {
    return assistantFields({ finding_index: target.index, action: $('#assistant-action').value,
      use_llm: $('#assistant-use-model').checked, allow_external_transfer: $('#assistant-consent').checked,
      question: $('#assistant-question').value, selected_text: $('#assistant-text').value });
  }
  function renderAnswer(response) {
    const box = $('#assistant-answer');
    box.replaceChildren();
    box.hidden = false;
    const title = response.status === 'insufficient_data' ? 'Недостаточно данных' : response.method === 'local' ? 'Локальное объяснение' : 'Ответ модели';
    box.append(node('div', { class: 'assistant-answer-title' }, icon(response.status === 'insufficient_data' ? 'info' : 'message'), node('h3', { text: title })), node('p', { text: response.answer }));
    if (response.steps?.length) box.append(node('ol', { class: 'assistant-steps' }, ...response.steps.map((step) => node('li', { text: step }))));
    if (response.suggestion) {
      const text = node('textarea', { readonly: true, rows: '4', 'aria-label': 'Предлагаемая формулировка' });
      text.value = response.suggestion;
      const copy = node('button', { type: 'button', class: 'button button-small', onclick: async () => {
        try { await navigator.clipboard.writeText(response.suggestion); toast('Предложение скопировано. Проверьте факты перед использованием.'); }
        catch { toast('Не удалось скопировать. Выделите текст предложения вручную.'); }
      } }, 'Скопировать формулировку');
      box.append(node('div', { class: 'assistant-suggestion' }, node('span', { class: 'detail-label', text: 'Предложение для ручной проверки' }), text, copy));
    }
    for (const source of response.sources || []) {
      box.append(node('div', { class: 'assistant-source' }, node('span', { class: 'detail-label', text: 'Основание · сохранённая спецификация' }),
        node('code', { text: `${source.source_ref} · ${source.code} v${source.version}` }),
        node('p', { text: source.requirement }),
        node('details', { class: 'technical-details' }, node('summary', { text: 'Применимость и исключения' }),
          node('div', { class: 'technical-content' }, detailBlock('Когда применяется', source.applicability),
            source.exceptions ? detailBlock('Исключения', source.exceptions) : null,
            detailBlock('Источник', source.source_title)))));
    }
    if (response.warnings?.length) box.append(node('ul', { class: 'warning-list' }, ...response.warnings.map((warning) => node('li', { text: warning }))));
  }
  async function submit(event) {
    event?.preventDefault();
    if (busy || !target) return;
    let payload;
    try { payload = fields(); } catch (failure) { error(failure.message); return; }
    const signature = JSON.stringify(payload);
    if (!pending || pending.signature !== signature) pending = { signature, key: crypto.randomUUID() };
    const operation = pending;
    const current = ++generation;
    $('#assistant-consent').checked = false;
    error();
    clearAnswer();
    $('#assistant-new-attempt').hidden = true;
    setBusy(true, payload.use_llm);
    try {
      const { body } = await api(`reports/${encodeURIComponent(target.reportId)}/assistant`, {
        method: 'POST', headers: { 'Content-Type': 'application/json', 'Idempotency-Key': operation.key },
        body: JSON.stringify(payload),
      });
      if (current !== generation) return;
      pending = null;
      renderAnswer(body);
    } catch (failure) {
      if (current !== generation) return;
      if (failure.status === 401) { clear(); unauthorized(failure); return; }
      if (failure.status === 502) {
        error('Модельный ответ не получен или отклонён проверкой. Исходный отчёт сохранён. Для отдельного нового вызова нажмите «Новая попытка» и подтвердите согласие снова.');
        $('#assistant-new-attempt').hidden = false;
      } else if (failure.status === 410 || failure.status === 404) {
        error('Исходный отчёт или замечание недоступны. Закройте помощника и обновите список отчётов.');
      } else if (failure.status === 409) error(failure.message);
      else error('Ответ мог быть принят сервером, но не получен. Повтор неизменного запроса использует тот же ключ операции. Для модели подтвердите согласие снова.');
    } finally { if (current === generation) { setBusy(false); updateAfterRequest(); } }
  }
  function updateAfterRequest() {
    const requiresModel = ['question', 'rephrase'].includes($('#assistant-action').value);
    $('#assistant-submit').disabled = requiresModel && !modelAvailable();
  }
  function clear() {
    generation += 1;
    target = null;
    pending = null;
    $('#assistant-form').reset();
    clearAnswer();
    error();
    setBusy(false);
    $('#assistant-finding').textContent = '';
    $('#assistant-new-attempt').hidden = true;
    dialog.close();
  }
  function open(index) {
    const report = getReport();
    if (!report?.findings?.[index]) return;
    const same = target?.reportId === report.id && target.index === index;
    if (!same) {
      clear();
      target = { reportId: report.id, index };
      $('#assistant-finding').textContent = report.findings[index].message;
      updateMode();
    }
    if (!dialog.open) dialog.showModal();
    if (!same) submit(); // Initial explanation is always local; no model consent inferred.
    else $('#assistant-consent').checked = false;
  }
  $('#assistant-form').addEventListener('submit', submit);
  $('#close-assistant').addEventListener('click', () => { if (!busy) dialog.close(); });
  dialog.addEventListener('cancel', (event) => { if (busy) event.preventDefault(); });
  dialog.addEventListener('close', () => { $('#assistant-consent').checked = false; });
  $('#assistant-action').addEventListener('change', () => { error(); clearAnswer(); updateMode(); });
  $('#assistant-use-model').addEventListener('change', updateMode);
  for (const id of ['#assistant-question', '#assistant-text']) $(id).addEventListener('input', () => { $('#assistant-consent').checked = false; });
  $('#assistant-new-attempt').addEventListener('click', () => {
    pending = null;
    $('#assistant-consent').checked = false;
    $('#assistant-new-attempt').hidden = true;
    error();
    toast('Подготовлена новая попытка. Перед отправкой модели нужно новое согласие.');
  });
  return { open, clear };
}
