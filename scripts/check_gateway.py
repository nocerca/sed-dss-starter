"""Run and clean up only the isolated synthetic gateway test project."""
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent
command = ["docker", "compose", "-p", "sed-dss-gateway-test", "-f", str(root / "tests/compose.gateway.yaml")]


def run(arguments: list[str], timeout: int = 300) -> subprocess.CompletedProcess:
    return subprocess.run(command + arguments, cwd=root, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout)


try:
    result = run(["up", "--build", "--abort-on-container-exit", "--exit-code-from", "check"], timeout=600)
    if result.returncode:
        print("Проверка цепочки OpenClaw не пройдена; проверьте изолированный тестовый проект")
        sys.exit(1)
    logs = run(["logs", "--no-color"]).stdout
    if any(marker in logs for marker in ("SEMANTIC_DEMO", "учебные материалы", "FIRST_SYNTHETIC_MARKER", "synthetic-zai-api-key", "PRIVATE-SED-ID", "SYNTHETIC_ASSISTANT_MARKER")):
        print("Проверка журналов не пройдена: обнаружены тестовые данные")
        sys.exit(1)
    print("OK: REST, PostgreSQL, OpenClaw, тестовая модель, идемпотентность, согласие, сессии и журналы")
finally:
    # No production containers or database volumes belong to this project.
    run(["down", "--remove-orphans"], timeout=60)
