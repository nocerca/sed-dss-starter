import test from 'node:test';
import assert from 'node:assert/strict';
import { analysisFields, assistantFields, errorText, fileError, formatDate, fragmentLabel, plural } from '../app/static/ui-core.mjs';

test('upload rejects missing, empty, oversized and unsupported files', () => {
  assert.match(fileError(null), /DOCX/);
  assert.match(fileError({ name: 'letter.pdf', size: 10 }), /только DOCX/);
  assert.match(fileError({ name: 'letter.docx', size: 0 }), /пуст/);
  assert.match(fileError({ name: 'letter.docx', size: 11 }, 10), /лимит/);
  assert.equal(fileError({ name: 'letter.DOCX', size: 10 }, 10), '');
});

test('model consent cannot be inferred from unrelated flags', () => {
  assert.throws(() => analysisFields({ use_llm: true, use_cache: true }), /подтвердите/);
  const fields = analysisFields({ use_llm: false, allow_external_transfer: true, use_language_checks: true, use_cache: true });
  assert.equal(fields.allow_external_transfer, 'false');
  assert.equal(fields.use_language_checks, 'true');
  assert.equal(fields.use_llm, 'false');
});

test('API fields omit empty optional values and keep explicit analysis options', () => {
  assert.deepEqual(analysisFields({ use_llm: true, allow_external_transfer: true, use_cache: false,
    document_type: 'request', external_document_id: '  letter-1 ', template_id: '',
    policy_profile: 'office_instruction_letters' }), {
    use_llm: 'true', allow_external_transfer: 'true', use_cache: 'false',
    use_language_checks: 'false', document_type: 'request', external_document_id: 'letter-1',
    policy_profile: 'office_instruction_letters',
  });
});

test('structural positions are labelled as blocks and never physical pages', () => {
  assert.equal(fragmentLabel('body/p[7]/r[0]'), 'Основная часть · блок 8');
  assert.equal(fragmentLabel('section[0]/footer/p[1]'), 'Раздел 1 · нижний колонтитул · блок 2');
  assert.equal(fragmentLabel(null), 'Фрагмент не определён');
  assert.doesNotMatch(fragmentLabel('body/table[1]/p[0]'), /страниц/);
});

test('server failures do not reproduce potentially private response details', () => {
  assert.doesNotMatch(errorText(500, 'PRIVATE_SQL_AND_CONTENT'), /PRIVATE/);
  assert.match(errorText(401, 'anything'), /APP_API_KEY/);
  assert.match(errorText(422, [{ input: 'PRIVATE' }]), /параметры/);
  assert.doesNotMatch(errorText(422, [{ input: 'PRIVATE' }]), /PRIVATE/);
});

test('Russian counters and absent dates are presented without invented values', () => {
  const forms = ['замечание', 'замечания', 'замечаний'];
  assert.equal(plural(1, forms), '1 замечание');
  assert.equal(plural(12, forms), '12 замечаний');
  assert.equal(plural(22, forms), '22 замечания');
  assert.equal(formatDate('invalid'), 'Дата не указана');
  assert.equal(formatDate(null), 'Дата не указана');
});

test('local assistant excludes hidden text/question and never infers consent', () => {
  assert.deepEqual(assistantFields({ finding_index: 7, action: 'fix', selected_text: 'hidden input',
    question: 'hidden question', allow_external_transfer: true }), {
    finding_index: 7, action: 'fix', use_llm: false, allow_external_transfer: false,
  });
});

test('model assistant requires explicit consent and preserves the original finding index', () => {
  assert.throws(() => assistantFields({ action: 'question', question: 'Почему?', use_llm: true }), /Подтвердите/);
  assert.throws(() => assistantFields({ action: 'question', question: 'Почему?' }), /нужна модель/);
  const payload = assistantFields({ finding_index: 3, action: 'question', question: ' Почему? ',
    use_llm: true, allow_external_transfer: true });
  assert.equal(payload.finding_index, 3);
  assert.equal(payload.question, 'Почему?');
  assert.equal(payload.allow_external_transfer, true);
});

test('rephrasing requires user text and bounds question/excerpt lengths', () => {
  assert.throws(() => assistantFields({ action: 'rephrase', use_llm: true, allow_external_transfer: true }), /Вставьте/);
  assert.throws(() => assistantFields({ action: 'question', use_llm: true, allow_external_transfer: true, question: 'x'.repeat(1001) }), /1000/);
  assert.throws(() => assistantFields({ action: 'rephrase', use_llm: true, allow_external_transfer: true, selected_text: 'x'.repeat(2001) }), /2000/);
});
