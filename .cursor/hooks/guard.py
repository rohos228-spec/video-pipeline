"""beforeShellExecution: git-политика, секреты, опасные удаления, второй Chrome.

Всегда печатает JSON с permission allow/ask/deny и выходит с кодом 0.
"""
from __future__ import annotations

import re
import shlex
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from vp_common import PROTECTED_BRANCHES, current_branch, log, project_root, read_input, respond  # noqa: E402

ALLOW, ASK, DENY = "allow", "ask", "deny"
RANK = {ALLOW: 0, ASK: 1, DENY: 2}

GIT_TOKEN = re.compile(r"(?:^|[\s;&|(`'\"])git(?:\.exe)?(?=\s)", re.I)
SEGMENT_END = re.compile(r"&&|\|\||[;|\n]|\)")

SECRET_TOKEN = re.compile(
    r"(?<![\w.-])(?:[\w./\\:-]*[/\\])?(\.env(?:\.(?!example\b|fleet\.example\b)[\w.-]+)?|Data_video_pipeline\.env|browser_profile)(?![\w.-])",
    re.I,
)
PRINTENV = re.compile(r"(?<![\w-])(printenv|Get-ChildItem\s+env:|gci\s+env:|dir\s+env:|ls\s+env:)", re.I)
DANGEROUS_RM = [
    re.compile(r"(?<![\w-])rm\s+(-[a-zA-Z]*[rR][a-zA-Z]*|--recursive)\b"),
    re.compile(r"Remove-Item\b[^\n;|&]*-(Recurse|r)\b", re.I),
    re.compile(r"(?<![\w-])(rd|rmdir)\s+/s\b", re.I),
    re.compile(r"(?<![\w-])del\s+(/[a-z]\s+)*/s\b", re.I),
    re.compile(r"(?<![\w-])(format|diskpart|mkfs)(\.exe)?\s", re.I),
]
CHROME = re.compile(r"(chrome(\.exe)?|msedge(\.exe)?|google-chrome|chromium)\b[^\n]*|--remote-debugging-port", re.I)
CHROME_OK = re.compile(r"scripts[/\\](Start-ChromeCDP\.ps1|[\w-]*chrome[\w-]*\.(ps1|py|cmd|bat))", re.I)

ASK_SUBCOMMANDS_ON_PROTECTED = {"merge", "rebase", "pull", "cherry-pick", "revert", "am"}
GLOBAL_OPTS_WITH_VALUE = {"-C", "-c", "--git-dir", "--work-tree", "--namespace"}


def worst(a: tuple[str, str], b: tuple[str, str]) -> tuple[str, str]:
    return b if RANK[b[0]] > RANK[a[0]] else a


def split_tokens(text: str) -> list[str]:
    try:
        return shlex.split(text, posix=True)
    except ValueError:
        return text.split()


def git_invocations(command: str) -> list[list[str]]:
    """Все вызовы git (в т.ч. внутри bash -c "..."/powershell -Command "...")."""
    result = []
    for m in GIT_TOKEN.finditer(command):
        rest = command[m.end():]
        end = SEGMENT_END.search(rest)
        segment = rest[: end.start()] if end else rest
        tokens = [t.strip("'\"") for t in split_tokens(segment)]
        result.append([t for t in tokens if t])
    return result


def strip_global_opts(tokens: list[str]) -> tuple[list[str], str | None]:
    """Возвращает (аргументы после подкоманды включительно, путь из -C)."""
    i, cwd = 0, None
    while i < len(tokens) and tokens[i].startswith("-"):
        opt = tokens[i]
        if opt in GLOBAL_OPTS_WITH_VALUE and i + 1 < len(tokens):
            if opt == "-C":
                cwd = tokens[i + 1]
            i += 2
        else:
            i += 1
    return tokens[i:], cwd


def targets_protected(ref: str) -> bool:
    ref = ref.lstrip("+")
    dst = ref.split(":", 1)[1] if ":" in ref else ref
    dst = dst.removeprefix("refs/heads/")
    return dst in PROTECTED_BRANCHES or dst.endswith("/main") and dst.startswith("origin/")


def check_push(args: list[str], branch: str) -> tuple[str, str]:
    opts = [a for a in args if a.startswith("-")]
    pos = [a for a in args if not a.startswith("-")]
    if any(o in ("-f", "--force", "--force-with-lease", "--force-if-includes") or o.startswith("--force") for o in opts):
        return DENY, "force push запрещён"
    if any(o in ("--all", "--mirror", "--delete", "-d", "--prune", "--tags") for o in opts):
        return DENY, "push --all/--mirror/--delete/--tags запрещён"
    refspecs = pos[1:]
    if any(r.startswith("+") for r in refspecs):
        return DENY, "force push через +refspec запрещён"
    if any(r.startswith(":") for r in refspecs):
        return DENY, "удаление удалённой ветки запрещено"
    if any(targets_protected(r) or r in ("HEAD",) and branch in PROTECTED_BRANCHES for r in refspecs):
        return DENY, "push в main/master делает только человек вручную"
    if not refspecs and branch in PROTECTED_BRANCHES | {""}:
        return DENY, f"push без refspec с ветки '{branch or 'detached'}' запрещён"
    return ASK, "push требует подтверждения человека"


def check_git(tokens: list[str], cwd: Path) -> tuple[str, str]:
    args, c_path = strip_global_opts(tokens)
    if not args:
        return ALLOW, ""
    sub, rest = args[0], args[1:]
    where = (cwd / c_path) if c_path else cwd
    branch = current_branch(where) if where.exists() else ""
    on_protected = branch in PROTECTED_BRANCHES

    if sub == "push":
        return check_push(rest, branch)
    if sub == "commit":
        if on_protected:
            return DENY, f"коммит в '{branch}' запрещён: работай в worktree-ветке (superpowers:using-git-worktrees)"
        return ALLOW, ""
    if sub in ASK_SUBCOMMANDS_ON_PROTECTED and on_protected:
        return ASK, f"git {sub} меняет '{branch}'"
    if sub == "reset" and "--hard" in rest:
        return ASK, "git reset --hard теряет изменения"
    if sub == "branch" and any(a in ("-D", "--delete", "-d") for a in rest):
        if any(a in PROTECTED_BRANCHES for a in rest):
            return DENY, "удаление main/master запрещено"
        return ASK, "удаление ветки"
    if sub == "branch" and any(a in ("-f", "--force", "-M", "-m") for a in rest):
        return ASK, "переименование/перезапись ветки"
    if sub == "stash" and rest[:1] and rest[0] in ("drop", "clear"):
        return ASK, "удаление stash"
    if sub == "tag" and any(a in ("-d", "--delete", "-f") for a in rest):
        return ASK, "удаление/перезапись тега"
    if sub in ("update-ref", "filter-branch", "filter-repo", "replace"):
        return ASK, f"git {sub} переписывает историю"
    if sub == "gc" and any(a.startswith("--prune") for a in rest):
        return ASK, "git gc --prune"
    if sub == "reflog" and rest[:1] and rest[0] in ("expire", "delete"):
        return ASK, "git reflog expire/delete"
    if sub == "config" and any(a in ("--global", "--system") for a in rest):
        return ASK, "изменение глобального git config"
    if sub == "clean":
        flags = "".join(a.lstrip("-") for a in rest if a.startswith("-") and not a.startswith("--"))
        if "x" in flags or "X" in flags:
            return DENY, "git clean -x удалит data/, prompts/, .venv (gitignored)"
        if "f" in flags or "--force" in rest:
            return ASK, "git clean -f удаляет неотслеживаемые файлы"
    if sub == "checkout" and ("--" in rest or "." in rest) and not any(a in ("-b", "-B") for a in rest):
        return ASK, "git checkout -- отбрасывает локальные изменения"
    if sub == "restore" and "--staged" not in rest:
        return ASK, "git restore отбрасывает локальные изменения"
    if sub == "worktree" and rest[:1] and rest[0] == "remove" and any(a in ("-f", "--force") for a in rest):
        return ASK, "git worktree remove --force теряет изменения"
    return ALLOW, ""


def decide(command: str, cwd: Path) -> tuple[str, str]:
    verdict: tuple[str, str] = (ALLOW, "")
    if SECRET_TOKEN.search(command) or PRINTENV.search(command):
        return DENY, "доступ к секретам (.env, browser_profile, переменные окружения) запрещён"
    for rx in DANGEROUS_RM:
        if rx.search(command):
            return DENY, "рекурсивное удаление запрещено: удаляй конкретные файлы или попроси человека"
    chrome = CHROME.search(command)
    if chrome and not CHROME_OK.search(command) and not re.search(r"\b(taskkill|Stop-Process|pkill|kill)\b", command, re.I):
        if re.search(r"(Start-Process|(?<![\w-])start\s|&\s*['\"]?[^\s]*chrome|--remote-debugging-port|^\s*['\"]?[^\s]*chrome)", command, re.I):
            return DENY, "не запускай второй Chrome: используй существующий CDP на 29229 (scripts/Start-ChromeCDP.ps1)"
    for tokens in git_invocations(command):
        verdict = worst(verdict, check_git(tokens, cwd))
        if verdict[0] == DENY:
            break
    return verdict


def main() -> None:
    data = read_input()
    command = str(data.get("command") or "")
    cwd = Path(data.get("cwd") or project_root())
    try:
        permission, reason = decide(command, cwd)
    except Exception as exc:  # хук не должен падать молча — лучше спросить
        permission, reason = ASK, f"guard error: {exc}"
    if permission != ALLOW:
        log("guard", f"{permission}: {reason} :: {command[:300]}")
    out: dict = {"permission": permission, "continue": True}
    if reason:
        out["user_message"] = f"vp-guard: {reason}"
        out["agent_message"] = (
            f"vp-guard ({permission}): {reason}. Политика: .cursor/rules/00-autonomy.mdc. "
            "Не пытайся обойти через алиасы/скрипты; если действие нужно — остановись и опиши команду для человека."
        )
    respond(out)


if __name__ == "__main__":
    main()
