"""Allow ``python -m app.services.visual_lab <cmd>`` shortcut.

Delegates to :func:`app.services.visual_lab.cli.main`.
"""

from app.services.visual_lab.cli import main

if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
