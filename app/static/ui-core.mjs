// Pure presentation and request helpers. No credentials or document persistence.
export const categoryNames = { formatting: 'Оформление', language: 'Язык', content: 'Содержание' };
export const methodNames = { rule: 'Локальное правило', llm: 'Модель · нужна проверка', language_tool: 'LanguageTool · нужна проверка' };
export const statusNames = { completed: 'Завершено', partial: 'Неполная проверка', processing: 'Обработка не завершена' };
export const modeNames = { rules_only: 'Локальные проверки', hybrid: 'Правила и модель', rules_fallback: 'Локальные результаты · сбой модели' };
export const outcomeNames = { pass: 'Без отклонений', violation: 'Отклонение', insufficient_data: 'Недостаточно данных' };
export const typeNames = { unspecified: 'Не указан', request: 'Запрос', response: 'Ответ', notification: 'Уведомление', cover_letter: 'Сопроводительное' };

export function fileError(file, maxBytes = 5242880) {
  if (!file) return 'Выберите письмо в формате DOCX.';
  if (!/\.docx$/i.test(file.name)) return 'Поддерживается только DOCX. Выберите файл Word с расширением .docx.';
  if (file.size === 0) return 'Файл пуст. Выберите документ с содержимым.';
  if (file.size > maxBytes) return `Файл превышает лимит ${formatBytes(maxBytes)}. Выберите документ меньшего размера.`;
  return '';
}

export function formatBytes(bytes) {
  if (bytes < 1024) return `${bytes} Б`;
  if (bytes < 1048576) return `${(bytes / 1024).toLocaleString('ru-RU', { maximumFractionDigits: 1 })} КиБ`;
  return `${(bytes / 1048576).toLocaleString('ru-RU', { maximumFractionDigits: 1 })} МиБ`;
}

export function formatDate(value) {
  if (!value) return 'Дата не указана';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return 'Дата не указана';
  return date.toLocaleString('ru-RU', { day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' });
}

export function plural(count, forms) {
  const mod10 = Math.abs(count) % 10, mod100 = Math.abs(count) % 100;
  return `${count} ${forms[mod10 === 1 && mod100 !== 11 ? 0 : mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14) ? 1 : 2]}`;
}

export function fragmentLabel(value) {
  if (!value) return 'Фрагмент не определён';
  const paragraph = /(?:^|\/)p\[(\d+)\]/.exec(value);
  const section = /^section\[(\d+)\]/.exec(value);
  let location = value.startsWith('body/') ? 'Основная часть' : section ? `Раздел ${Number(section[1]) + 1}` : 'Фрагмент DOCX';
  if (/footer/.test(value)) location += ' · нижний колонтитул';
  if (/header/.test(value)) location += ' · верхний колонтитул';
  if (/table/.test(value)) location += ' · таблица';
  if (paragraph) location += ` · блок ${Number(paragraph[1]) + 1}`;
  return location;
}

export function analysisFields(values) {
  const useModel = Boolean(values.use_llm);
  if (useModel && !values.allow_external_transfer) throw new Error('Для модельной проверки подтвердите передачу данных именно этого запроса.');
  const fields = {
    use_llm: String(useModel),
    use_language_checks: String(Boolean(values.use_language_checks)),
    use_cache: String(Boolean(values.use_cache)),
    allow_external_transfer: String(useModel && Boolean(values.allow_external_transfer)),
    document_type: values.document_type || 'unspecified',
  };
  for (const name of ['policy_profile', 'external_document_id', 'template_id']) {
    if (values[name]?.trim()) fields[name] = values[name].trim();
  }
  return fields;
}

export function errorText(status, detail) {
  if (status === 401) return 'Ключ приложения не принят. Введите APP_API_KEY из .env, а не ключ модели.';
  if (status === 409) return typeof detail === 'string' ? detail : 'Запрос уже обрабатывается или его параметры изменились. Проверьте отчёты перед повтором.';
  if (status === 410) return 'Отчёт этой операции удалён. Для новой проверки отправьте письмо ещё раз.';
  if (status === 413) return 'Файл превышает допустимый размер.';
  if (status === 415) return 'Этот формат не поддерживается. Загрузите DOCX.';
  if (status === 422 && Array.isArray(detail)) return 'Проверьте файл и параметры формы: сервер не принял переданные данные.';
  if (status >= 500) return 'Сервис не смог завершить запрос. Проверьте сохранённые отчёты перед повторной проверкой.';
  return typeof detail === 'string' ? detail.slice(0, 1500) : 'Не удалось выполнить запрос. Повторите позже.';
}

export function assistantFields(values) {
  const action = values.action || 'explain';
  const useModel = Boolean(values.use_llm);
  if (['question', 'rephrase'].includes(action) && !useModel) throw new Error('Для вопроса или формулировки нужна модель.');
  if (useModel && !values.allow_external_transfer) throw new Error('Подтвердите передачу данных для этого запроса помощника.');
  const result = { finding_index: values.finding_index, action, use_llm: useModel,
    allow_external_transfer: useModel && Boolean(values.allow_external_transfer) };
  if (action === 'question') {
    const question = values.question?.trim() || '';
    if (question.length < 3 || question.length > 1000) throw new Error('Введите вопрос от 3 до 1000 символов.');
    result.question = question;
  }
  if (['question', 'rephrase'].includes(action)) {
    const text = values.selected_text?.trim() || '';
    if (action === 'rephrase' && text.length < 3) throw new Error('Вставьте фрагмент письма для переформулировки.');
    if (text.length > 2000 || text.length > 0 && text.length < 3) throw new Error('Фрагмент должен содержать от 3 до 2000 символов.');
    if (text) result.selected_text = text;
  }
  return result;
}
