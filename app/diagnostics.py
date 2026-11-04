"""Russian explanations of observed properties; never infer missing facts."""

import re
from datetime import date

from app.document_parser import ParsedDocument

ROLES = {
    'body': 'основной текст', 'recipient': 'адресат', 'reference_data': 'справочные данные организации',
    'subject': 'заголовок', 'attachment': 'отметка о приложении', 'signature': 'подпись',
    'executor': 'исполнитель', 'date': 'дата письма',
}
CHECKS = {
    'ORG_MARGINS': 'поля страницы', 'ORG_BODY_FONT': 'шрифт основного текста',
    'ORG_BODY_FONT_SIZE': 'размер основного текста', 'ORG_BODY_LINE_SPACING': 'интервал основного текста',
    'ORG_BODY_FIRST_LINE': 'абзацный отступ', 'ORG_REFERENCE_DATA_FONT': 'шрифт справочных данных',
    'ORG_RECIPIENT_FONT': 'шрифт адресата', 'ORG_SUBJECT_FONT': 'шрифт заголовка',
    'ORG_ATTACHMENT_FONT': 'шрифт отметки о приложении', 'ORG_SIGNATURE_FONT': 'шрифт подписи',
    'ORG_EXECUTOR_FONT': 'шрифт исполнителя', 'ORG_EXECUTOR_SPACING': 'интервал в блоке исполнителя',
    'ORG_DATE_FORMAT': 'дата письма', 'ORG_EXECUTOR_CONTACT': 'ФИО и телефон исполнителя',
    'ORG_SIGNATURE_FORBIDDEN_PREFIX': 'оформление должности подписанта',
    'ORG_RECIPIENT_COPY_LABEL': 'метка «Копия» перед адресатом',
}
MARGINS = {'left_mm': 'левое поле', 'right_mm': 'правое поле',
           'top_mm': 'верхнее поле', 'bottom_mm': 'нижнее поле'}
CHECK_ROLES = {
    'ORG_BODY_FONT': 'body', 'ORG_BODY_FONT_SIZE': 'body', 'ORG_BODY_LINE_SPACING': 'body',
    'ORG_BODY_FIRST_LINE': 'body', 'ORG_DATE_FORMAT': 'date',
    'ORG_REFERENCE_DATA_FONT': 'reference_data', 'ORG_RECIPIENT_FONT': 'recipient',
    'ORG_RECIPIENT_COPY_LABEL': 'recipient', 'ORG_SUBJECT_FONT': 'subject',
    'ORG_ATTACHMENT_FONT': 'attachment', 'ORG_SIGNATURE_FONT': 'signature',
    'ORG_SIGNATURE_FORBIDDEN_PREFIX': 'signature', 'ORG_EXECUTOR_FONT': 'executor',
    'ORG_EXECUTOR_SPACING': 'executor', 'ORG_EXECUTOR_CONTACT': 'executor',
}
MISSING_ROLE = {
    'body': 'не установлены границы основного текста по приветствию или начальной фразе письма',
    'recipient': 'до начала текста не распознан отдельный блок адресата; адресование только организации этот алгоритм может не выделить',
    'reference_data': 'не найден единый блок с ИНН/КПП и телефоном или email; данные могут быть разнесены по разным строкам',
    'subject': 'не выделена строка заголовка, начинающаяся с «О» или «Об»',
    'attachment': 'не выделена отметка «Приложение:» или «Приложения:»; наличие приложений не установлено',
    'signature': 'не выделена единственная строка должности подписанта после основного текста',
    'executor': 'не распознана строка «Исполнитель:» или полное ФИО в нижнем колонтитуле',
    'date': 'до начала основного текста не выделена отдельная дата письма',
}


def number(value) -> str:
    return f'{value:g}'.replace('.', ',') if isinstance(value, (float, int)) else 'не определено'


def location(fragment: str | None) -> str:
    if not fragment:
        return 'документ'
    section = re.search(r'section\[(\d+)\]', fragment)
    parts = [f'раздел {int(section[1]) + 1}'] if section else []
    for name, label in [('first_page_footer', 'нижний колонтитул первой страницы'),
                        ('even_page_footer', 'нижний колонтитул чётных страниц'),
                        ('footer', 'нижний колонтитул'), ('header', 'верхний колонтитул')]:
        if name in fragment:
            parts.append(label)
            break
    if fragment.startswith('body'):
        parts.append('основная часть')
    block = re.search(r'/p\[(\d+)\]', fragment)
    if block:
        parts.append(f'блок {int(block[1]) + 1}')
    if '/table[' in fragment:
        parts.append('ячейка таблицы')
    return ', '.join(parts) or 'фрагмент документа'


def spacing(observed: dict) -> str:
    mode, value = observed.get('mode'), observed.get('value')
    if mode == 'exact':
        return f'{number(value)} пт «точно»'
    if mode == 'at_least':
        return f'не менее {number(value)} пт'
    if mode == 'multiple':
        return f'{number(value)} строки'
    return 'режим или значение интервала не извлечены'


def policy_text(code: str, fragment: str | None, expected: dict, observed: dict,
                outcome: str) -> tuple[str, str]:
    title = CHECKS[code]
    place = location(fragment)
    if outcome == 'pass':
        return f'{place.capitalize()}: проверка «{title}» не выявила отклонений в проверенных свойствах.', 'Правки по этой проверке не требуются.'
    if outcome == 'insufficient_data':
        if not observed:
            role = CHECK_ROLES.get(code)
            why = MISSING_ROLE[role] if role else 'не извлечены свойства разделов документа'
            return (f'Проверка «{title}» не выполнена: {why}.',
                    'Проверьте этот реквизит в Word вручную. Отсутствие распознанного блока не означает отсутствие реквизита на странице.')
        if code == 'ORG_MARGINS':
            missing = [MARGINS[k] for k, v in observed.items() if k in MARGINS and v is None]
            details = []
            if observed.get('mirrored_margins'):
                details.append('включены зеркальные поля')
            if observed.get('gutter_mm'):
                details.append(f'задан переплёт {number(observed["gutter_mm"])} мм')
            why = 'не извлечены ' + ', '.join(missing) if missing else '; '.join(details)
            return (f'{place.capitalize()}: поля не проверены — {why}.',
                    'Проверьте фактические поля этого раздела в Word с учётом переплёта и чётных/нечётных страниц.')
        if 'SPACING' in code:
            if observed.get('mode') == 'exact':
                why = 'верхняя граница инструкции задана в строках (двойной интервал), а высота строки не вычислена'
            elif observed.get('mode') == 'multiple':
                why = 'нижняя граница инструкции задана в пунктах (12 пт), а высота строки не вычислена'
            else:
                why = 'точная высота строки или режим интервала не определены'
            return (f'{place.capitalize()}: {title} не проверен; задано {spacing(observed)}. '
                    f'Причина: {why}.',
                    'Проверьте режим межстрочного интервала и высоту строки в Word. Не меняйте интервал только из-за этого предупреждения.')
        if code == 'ORG_RECIPIENT_COPY_LABEL':
            return (f'{place.capitalize()}: найдена метка «Копия», но номер адресата не установлен.',
                    'Уточните, относится ли метка ко второму–четвёртому адресату: перед ними инструкция запрещает «Копия».')
        if code == 'ORG_BODY_FIRST_LINE':
            return (f'{place.capitalize()}: абзацный отступ не проверен — свойство не извлечено '
                    'либо положение задано списком, табуляцией или таблицей.',
                    'Проверьте начало первой строки относительно левого поля: требуется 12,5 мм. Число пробелов не доказывает этот отступ.')
        if code == 'ORG_EXECUTOR_CONTACT':
            return (f'{place.capitalize()}: проверка контактов отложена — рядом с блоком исполнителя '
                    f'найдено ссылок на изображения: {observed.get("image_references", 0)}. Текст внутри них не прочитан.',
                    'Проверьте наличие ФИО и телефона на изображении вручную; отсутствие данных в извлечённом тексте не доказывает их отсутствие на листе.')
        needed = {'family', 'size_pt'} if 'match' in expected else set(expected)
        if 'min_pt' in expected:
            needed.add('size_pt')
        missing = [label for key, label in [('family', 'гарнитура'), ('size_pt', 'размер'), ('bold', 'насыщенность')]
                   if key in needed and observed.get(key) is None]
        why = 'не удалось определить: ' + ', '.join(missing) if missing else 'основной шрифт смешанный или не удалось установить значение для сравнения'
        return (f'{place.capitalize()}: проверка «{title}» отложена — {why}.',
                'Проверьте эффективное форматирование блока с учётом стилей и темы Word; неизвестное значение не считается нарушением.')
    if code == 'ORG_MARGINS':
        differences = [(k, v) for k, v in expected.items()
                       if observed.get(k) is not None and abs(observed[k] - v) > .1]
        text = '; '.join(f'{MARGINS[k]} — {number(observed[k])} мм, требуется {number(v)} мм' for k, v in differences)
        action = '; '.join(f'{MARGINS[k]} {number(v)} мм ({number(v / 10)} см)' for k, v in differences)
        return f'{place.capitalize()}: {text}.', f'В параметрах страницы этого раздела установите: {action}.'
    if code == 'ORG_BODY_FIRST_LINE':
        first, left = observed.get('first_line_mm'), observed.get('left_indent_mm')
        description = (f'начало абзаца по извлечённым отступам — {number(first + left)} мм от левого поля, требуется 12,5 мм'
                       if first is not None and left is not None else
                       f'отступ первой строки — {number(first)} мм; левое смещение отдельно не определено, фактическое положение строки нужно уточнить')
        return (f'{place.capitalize()}: {description}.',
                'Установите начало первой строки на 1,25 см от левого поля. '
                f'Сейчас отступ первой строки — {number(first)} мм, левый отступ абзаца — {number(left)} мм; учитывайте оба значения.')
    if 'SPACING' in code:
        requirement = '12 пт «точно»' if code == 'ORG_EXECUTOR_SPACING' else 'от 12 пт «точно» до двойного'
        action = ('Задайте для блока исполнителя режим «точно» и значение 12 пт.'
                  if code == 'ORG_EXECUTOR_SPACING' else
                  'Для этого абзаца выберите допустимый интервал; например, 12 пт в режиме «точно».')
        return f'{place.capitalize()}: {title} — {spacing(observed)}, требуется {requirement}.', action
    if code == 'ORG_EXECUTOR_CONTACT':
        missing = [label for key, label in [('full_name_found', 'имя, отчество и фамилия'), ('phone_found', 'номер телефона')]
                   if observed.get(key) is False]
        verb = 'не найден номер телефона' if missing == ['номер телефона'] else 'не выделены ' + ' и '.join(missing)
        return (f'{place.capitalize()}: в распознанном блоке исполнителя {verb}.',
                'Добавьте недостающие данные исполнителя рядом с его ФИО в этом блоке. '
                'Телефон организации в шапке не заменяет телефон исполнителя; фактический номер укажите самостоятельно.')
    if code == 'ORG_DATE_FORMAT':
        value = str(observed.get('date_text', ''))[:90]
        cleaned = re.sub(r'^от\s+', '', value, flags=re.I)
        match = re.fullmatch(r'(\d{2})[./-](\d{2})[./-](\d{4})', cleaned)
        if match:
            day, month, year = map(int, match.groups())
            try:
                date(year, month, day)
            except ValueError:
                return (f'{place.capitalize()}: дата «{value}» не существует в календаре.',
                        'Уточните фактическую дату документа: проверьте день, месяц и год. Автоматически подменять дату нельзя.')
            suggestion = f'{day:02d}.{month:02d}.{year:04d}'
            return (f'{place.capitalize()}: дата записана как «{value}»; разделители не соответствуют цифровому формату.',
                    f'Сохраните ту же дату в виде «{suggestion}». Также допускается словесно-цифровая запись месяца с «г.» после года.')
        return (f'{place.capitalize()}: запись даты «{value}» не соответствует допустимому оформлению.',
                'Для цифровой даты используйте ДД.ММ.ГГГГ без «г.». Для словесной даты — день без начального нуля, '
                'месяц словом, четырёхзначный год и «г.». Сохраните фактическую дату документа.')
    if code == 'ORG_SIGNATURE_FORBIDDEN_PREFIX':
        prefix = str(observed.get('forbidden_prefix', '«За» или «/»'))[:15]
        return (f'{place.capitalize()}: перед должностью подписанта обнаружено «{prefix}».',
                'Уберите этот префикс из реквизита подписи и укажите фактическую должность уполномоченного подписанта; полномочия проверьте отдельно.')
    # Font checks explain only known discrepancies, never fabricate unresolved values.
    differences, actions = [], []
    family, size = observed.get('family'), observed.get('size_pt')
    target = expected.get('family')
    def normalized(value: str) -> str:
        return re.sub(r'\s*\(cyr\)\s*$', '', value.casefold()).strip()
    if family is not None and target and normalized(family) != normalized(target):
        differences.append(f'гарнитура «{str(family)[:120]}» вместо «{target}»')
        actions.append(f'выберите гарнитуру «{target}»')
    if size is not None:
        if 'min_pt' in expected and not expected['min_pt'] <= size <= expected['max_pt']:
            differences.append(f'размер {number(size)} пт при допустимых 10–14 пт')
            actions.append('выберите размер от 10 до 14 пт')
        elif 'size_pt' in expected and size != expected['size_pt']:
            differences.append(f'размер {number(size)} пт вместо {number(expected["size_pt"])} пт')
            actions.append(f'задайте размер {number(expected["size_pt"])} пт')
    if expected.get('bold') is False and observed.get('bold') is True:
        differences.append('полужирное начертание вместо обычного')
        actions.append('отключите полужирное начертание')
    return (f'{place.capitalize()}: {title} — ' + '; '.join(differences) + '.',
            'Для этого блока ' + '; '.join(actions) + '. Проверьте как прямое форматирование, так и стиль.')


def local_warnings(document: ParsedDocument, policy: dict) -> list[str]:
    warnings = []
    if not policy.get('profile'):
        warnings.append('Профиль инструкции не выбран: поля, кегль, абзацные отступы, даты и телефон исполнителя '
                        'по этой инструкции не проверялись. Для них передайте policy_profile=office_instruction_letters.')
    else:
        warnings.append('Нумерация со второй страницы, положение исполнителя внизу последнего листа и расстояние '
                        'перед подписью не проверялись: физическая раскладка DOCX не вычисляется. Проверьте эти три пункта в Word.')
    images = [p for p in document.paragraphs if p.image_count]
    if images:
        count = sum(p.image_count for p in images)
        places = '; '.join(location(p.fragment_id) for p in images[:3])
        warnings.append(f'Найдено ссылок на изображения: {count}; расположение: {places}. '
                        'Текст внутри изображений не распознавался. Если там подпись, печать или контакты, проверьте их вручную.')
    fields = sum(len(p.fields) for p in document.paragraphs)
    if fields:
        warnings.append(f'Найдено полей Word: {fields}. Прочитаны только сохранённые значения; поля не обновлялись. '
                        'Проверьте актуальность автоматических дат и номеров в Word перед повторной загрузкой.')
    # One actionable warning per affected check; detailed addresses remain in results.
    groups = {}
    for row in policy.get('results', []):
        if row['outcome'] == 'insufficient_data':
            groups.setdefault(row['code'], []).append(row)
    for code, rows in groups.items():
        first = rows[0]
        message = first.get('message', first['reason'])
        action = first.get('recommendation', 'Смотрите результаты проверки в metrics.policy_checks.results.')
        suffix = f' Фрагментов с этой причиной: {len(rows)}.' if all(r.get('fragment_id') for r in rows) else ''
        warnings.append(f'{code}: {message} {action}{suffix}')
    return warnings
