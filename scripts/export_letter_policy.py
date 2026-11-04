"""Validate and publish the letter-policy specification; never call a model."""

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

LEVELS = {
    "hard": "Однозначные требования к установленному реквизиту",
    "conditional": "Обязательные требования при подтверждённых условиях",
    "advisory": "Базовые языковые проверки и рекомендации",
    "unconfirmed": "Предложения, которые нельзя считать нарушениями инструкции",
}
OUTCOMES = {"pass", "violation", "not_applicable", "insufficient_data", "needs_review"}


def load_policy(path: Path) -> dict:
    """Reject incomplete or internally inconsistent specifications."""
    policy = json.loads(path.read_text(encoding="utf-8"))
    if policy.get("runtime_enabled") is not True or not policy.get("implemented_checks"):
        raise ValueError("The active subset must be explicitly declared")
    if policy.get("baseline_examples_are_gold") is not False:
        raise ValueError("Unreviewed examples must not be labelled gold")
    if set(policy.get("outcomes", [])) != OUTCOMES:
        raise ValueError("All five evidence outcomes are required")
    references = policy["references"]
    for reference in references.values():
        if not reference["label"] or not reference["paragraphs"]:
            raise ValueError("Empty source reference")
        if any(type(i) is not int or not 80 <= i < 700 for i in reference["paragraphs"]):
            raise ValueError("Source reference is outside chapter III")
    seen = set()
    for check in policy["checks"]:
        code = check["id"]
        if not re.fullmatch(r"[A-Z][A-Z0-9_]+", code) or code in seen:
            raise ValueError("Invalid or duplicate check identifier")
        seen.add(code)
        if check["level"] not in LEVELS:
            raise ValueError("Unknown check level")
        for key in ("requirement", "applies_when", "evidence", "exceptions", "method"):
            if not isinstance(check.get(key), str) or not check[key].strip():
                raise ValueError(f"Missing specification field: {key}")
        if not isinstance(check.get("expected"), dict):
            raise ValueError("Expected properties must be an object")
        sources = check["sources"]
        if any(source not in references for source in sources):
            raise ValueError("Unknown source reference")
        if check["level"] in {"hard", "conditional"} and not sources:
            raise ValueError("A mandatory check needs a source")
    if not seen:
        raise ValueError("No checks supplied")
    if not set(policy["implemented_checks"]) <= {c["id"] for c in policy["checks"] if c["level"] in {"hard", "conditional"}}:
        raise ValueError("Only sourced requirements can be activated")
    return policy


def render_policy(policy: dict) -> str:
    counts = Counter(check["level"] for check in policy["checks"])
    lines = [
        "# Правила проверки исходящих писем по предоставленной инструкции",
        "",
        f"Спецификация `{policy['profile_id']}` версии {policy['version']}. "
        "Основание — предоставленная владельцем копия инструкции, глава III: "
        "общие требования и раздел 7 «Служебная переписка». "
        "Требования разделов о приказах, положениях и протоколах на письма не переносятся.",
        "",
        f"**Статус: в API реализованы {len(policy['implemented_checks'])} проверок/диагностик профиля.** "
        "Они включаются полем `policy_profile=office_instruction_letters`; "
        "остальные требования остаются спецификацией для дальнейшей реализации. "
        "Также доступны пять эвристик и необязательный локальный LanguageTool. "
        "Не все перечисленные языковые проверки покрываются его текущими правилами.",
        "",
        f"Всего {sum(counts.values())} позиций: {counts['hard']} требований к реквизитам, "
        f"{counts['conditional']} требований с дополнительными условиями, "
        f"{counts['advisory']} языковых групп, {counts['unconfirmed']} спорных предложений.",
        "",
        "## Как агент должен применять правила",
        "",
        "1. Выбрать подтверждённый профиль организации. Выделить роли блоков: "
        "бланк, адресат, заголовок, основной текст, приложения, подпись, исполнитель.",
        "2. Установить стадию (проект/подписан/зарегистрирован), канал отправки, "
        "тип письма и носитель. Если сведений нет, не угадывать их по примеру.",
        "3. Разрешить наследование свойств Word. Прямое отсутствие свойства "
        "в run или абзаце не означает отсутствие эффективного форматирования.",
        "4. Для нарушения сохранить код и версию профиля, основание, условие "
        "применимости, адрес фрагмента, ожидаемое/наблюдаемое значение и рекомендацию. "
        "Не придумывать телефон, номер, дату, фамилию или полномочия подписанта.",
        "5. Не считать отсутствие в извлечённом тексте отсутствием на странице. "
        "Для геометрии нужны рендеринг/координаты, для полномочий и стадий — данные СЭД. "
        "Изображение не доказывает ни наличие, ни отсутствие конкретного реквизита.",
        "6. Не исправлять оригинал автоматически. Передача текста модели требует "
        "глобального разрешения и отдельного согласия на запрос.",
        "",
        "Результаты: `pass` — проверено; `violation` — доказанное нарушение; "
        "`not_applicable` — неприменимо; `insufficient_data` — не хватает данных; "
        "`needs_review` — нужен эксперт. Это проектируемые исходы проверок профиля, "
        "не новые значения `status` HTTP-отчёта.",
        "",
        "## Приоритет источников и разметка примеров",
        "",
        "Инструкция и подтверждённый шаблон задают требования; комментарии сотрудника "
        "помогают уточнить практику. Письмо из папки примеров может содержать ошибки "
        "и само по себе не устанавливает норму. Противоречия передаются эксперту.",
        "",
        "Варианты с ошибками создаются только как отдельные локальные копии. "
        "Одна контролируемая правка — одна запись с исходным документом, селектором, "
        "старым/новым значением, кодом правила и ожидаемым исходом. "
        "Сначала подтвердить исходное свойство и область применения. "
        "Все производные одного оригинала остаются в одной экспериментальной группе.",
        "",
        "Подходящие контролируемые дефекты: Arial в обычном основном тексте; "
        "16 пт в основном тексте; неверные поля; абзацный отступ 0 вместо 12,5 мм; "
        "удаление подтверждённого телефона исполнителя. Изменение интервала перед "
        "подписью требует проверки раскладки. Замена цифровой даты допустимой "
        "словесной датой — положительный контроль, а не дефект. Для запятых и "
        "орфографии нужна подтверждённая ошибка с достаточным контекстом.",
        "",
    ]
    for level, title in LEVELS.items():
        lines.extend([f"## {title}", ""])
        for check in policy["checks"]:
            if check["level"] != level:
                continue
            sources = "; ".join(
                policy["references"][key]["label"] for key in check["sources"]
            ) or "языковая рекомендация; отдельной формальной нормы инструкции не заявляется"
            lines.extend([
                f"### {check['id']}", "", check["requirement"], "",
                "Статус: " + (
                    "реализована проверка доступных свойств; геометрия и полная применимость не подтверждаются."
                    if check["id"] in policy["implemented_checks"] else
                    "языковая рекомендация; покрытие LanguageTool зависит от конкретной конструкции."
                    if level == "advisory" else
                    "не активировано как нарушение инструкции."
                    if level == "unconfirmed" else "не реализовано в профиле API."
                ), "",
                f"- Применять: {check['applies_when']}",
                f"- Доказательство: {check['evidence']}",
                f"- Ограничения: {check['exceptions']}",
                f"- Основание: {sources}.", "",
            ])
    lines.extend([
        "## Воспроизводимость и хранение",
        "",
        "Машинный каталог: [config/letter_policy.json](../config/letter_policy.json). "
        "Реализованная часть продублирована с условиями и основаниями в активном "
        "реестре `config/rules.json`; `implemented_checks` перечисляет её коды. "
        "Номера `paragraphs` — индексы верхнеуровневых абзацев DOCX с нуля, "
        "не страницы и не номера пунктов инструкции.",
        "",
        "Локальная привязка `examples/private/policy/source_binding.json` содержит "
        "SHA-256 источника/каталога и хеши опорных абзацев с адресами. "
        "Инвентаризация `letters_inventory.json` хранит пути/хеши оригиналов "
        "и статус `unreviewed`. Эти файлы, инструкция и письма исключены из Git/Docker. "
        "Они не загружаются в БД или модель этим инструментом.",
        "",
        "Переэкспорт документа: `python scripts/export_letter_policy.py`. "
        "Локальная привязка: добавить `--source <путь-к-инструкции.docx>`; "
        "инвентаризация: `--letters <папка-писем>`. "
        "Отчёт не подтверждает экспертную разметку, актуальность редакции "
        "инструкции или универсальное соответствие ГОСТ.", "",
    ])
    return "\n".join(lines)


def source_binding(policy: dict, source: Path) -> dict:
    from app.document_parser import parse_docx

    data = source.read_bytes()
    parsed = parse_docx(data)
    paragraphs = [p for p in parsed.paragraphs if re.fullmatch(r"body/p\[\d+\]", p.fragment_id)]
    references = {}
    for key, reference in policy["references"].items():
        anchors = []
        for index in reference["paragraphs"]:
            if index >= len(paragraphs):
                raise ValueError("Source does not contain a required paragraph")
            paragraph = paragraphs[index]
            anchors.append({
                "paragraph_index": index,
                "fragment_id": paragraph.fragment_id,
                "text_sha256": hashlib.sha256(paragraph.text.encode("utf-8")).hexdigest(),
            })
        references[key] = anchors
    return {
        "source_alias": policy["source_alias"], "source_path": str(source.resolve()),
        "source_sha256": hashlib.sha256(data).hexdigest(),
        "policy_sha256": hashlib.sha256(
            json.dumps(policy, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest(),
        "references": references,
        "paragraph_index_basis": "zero_based_top_level_body_paragraphs",
        "meaning": "Привязка к прочитанной копии; не автоматическая проверка смысла или редакции",
    }


def letters_inventory(directory: Path) -> dict:
    if not directory.is_dir():
        raise ValueError("Letters directory does not exist")
    records = []
    for path in sorted(directory.rglob("*")):
        if path.suffix.lower() != ".docx" or path.name.startswith("~$"):
            continue
        records.append({
            "doc_id": f"L{len(records) + 1:04d}", "source_path": str(path.resolve()),
            "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "label": "unreviewed", "allow_external_transfer": False,
            "split": None, "derived_from": None,
        })
    return {"schema_version": "1.0.0", "count": len(records), "documents": records}


def write_private(name: str, value: dict) -> None:
    directory = ROOT / "examples" / "private" / "policy"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Локальная спецификация правил, без LLM")
    parser.add_argument("--source", type=Path)
    parser.add_argument("--letters", type=Path)
    args = parser.parse_args()
    policy = load_policy(ROOT / "config" / "letter_policy.json")
    # Complete reads/validation before replacing any generated artifacts.
    binding = source_binding(policy, args.source) if args.source else None
    inventory = letters_inventory(args.letters) if args.letters else None
    (ROOT / "docs" / "LETTER_POLICY.md").write_text(render_policy(policy), encoding="utf-8")
    if binding is not None:
        write_private("source_binding.json", binding)
    if inventory is not None:
        write_private("letters_inventory.json", inventory)
    print(f"OK: {len(policy['checks'])} specification entries; {len(policy['implemented_checks'])} implemented checks")
    if inventory is not None:
        print(f"OK: {inventory['count']} local documents; unreviewed; no external transfer")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError) as exc:
        # Never print document paths, text or identifiers on a failure.
        print(f"Policy export failed: {type(exc).__name__}", file=sys.stderr)
        sys.exit(1)
