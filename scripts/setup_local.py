"""Prepare local settings without displaying secrets or enabling model calls."""

from pathlib import Path
import secrets


def prepare_settings(root: Path) -> list[str]:
    target = root / ".env"
    content = target.read_text(encoding="utf-8-sig") if target.exists() else (root / ".env.example").read_text(encoding="utf-8")
    lines = content.splitlines()
    added = []
    for name in ("OPENCLAW_GATEWAY_TOKEN", "MODEL_BRIDGE_API_KEY", "IDEMPOTENCY_SECRET"):
        index = next((i for i, line in enumerate(lines) if line.startswith(name + "=")), None)
        if index is None:
            lines.append(f"{name}={secrets.token_urlsafe(36)}")
            added.append(name)
        elif not lines[index].split("=", 1)[1].strip().strip("\"'"):
            lines[index] = f"{name}={secrets.token_urlsafe(36)}"
            added.append(name)
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return added


if __name__ == "__main__":
    added = prepare_settings(Path(__file__).resolve().parent.parent)
    print("Локальные настройки подготовлены. Создано ключей:", len(added))
    print("Значения сохранены только в .env. Внешние обращения не включались.")
