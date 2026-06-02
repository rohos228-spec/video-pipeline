"""One-off: replace toast.error(String(e)) with errorMessageFromUnknown in web/src."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "web" / "src"
IMPORT_LINE = 'import { errorMessageFromUnknown } from "@/lib/error-message";\n'
OLD = "toast.error(String(e))"
NEW = "toast.error(errorMessageFromUnknown(e))"
OLD2 = "toast.error(`Ошибка: ${String(e)}`)"
NEW2 = "toast.error(`Ошибка: ${errorMessageFromUnknown(e)}`)"


def patch_file(path: Path) -> bool:
    text = path.read_text(encoding="utf-8")
    if OLD not in text and OLD2 not in text:
        return False
    text = text.replace(OLD, NEW).replace(OLD2, NEW2)
    if "errorMessageFromUnknown" in text and IMPORT_LINE.strip() not in text:
        if 'from "sonner"' in text:
            text = text.replace('from "sonner";\n', 'from "sonner";\n' + IMPORT_LINE, 1)
        elif "from 'sonner'" in text:
            text = text.replace("from 'sonner';\n", "from 'sonner';\n" + IMPORT_LINE, 1)
    path.write_text(text, encoding="utf-8")
    return True


def main() -> None:
    n = 0
    for path in SRC.rglob("*.tsx"):
        if patch_file(path):
            n += 1
            print(path.relative_to(ROOT))
    print(f"patched {n} files")


if __name__ == "__main__":
    main()
