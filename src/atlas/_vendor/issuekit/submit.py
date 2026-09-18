"""Отправка жалобы как issue в GitHub/GitLab от залогиненного профиля (gh/glab).

Provider-agnostic тонкий слой над CLI хостинга: ``gh issue create`` (GitHub) /
``glab issue create`` (GitLab). Авторизация — у самого gh/glab (их логин/токен),
issuekit ничего не хранит. Перед отправкой потребитель валидирует тело через
``issuekit.lint`` (блокирующая дисциплина — неполную жалобу не шлём).
"""
from __future__ import annotations

import re
import subprocess

#: Поддерживаемые хостинги (совпадает с CLI gh/glab).
PROVIDERS = frozenset({"github", "gitlab"})


class SubmitError(RuntimeError):
    """Ошибка отправки issue (caller → CliError/Exit)."""


def run(cmd: list[str]) -> tuple[int, str, str]:
    """subprocess без shell; вернуть (rc, stdout, stderr). Не raise на rc!=0."""
    p = subprocess.run(list(cmd), text=True, capture_output=True, check=False)
    return p.returncode, p.stdout or "", p.stderr or ""


def extract_title(body: str) -> str | None:
    """Заголовок из первой строки ``# …`` тела (срезая префикс ``[Вид]``)."""
    for line in body.splitlines():
        s = line.strip()
        if s.startswith("# "):
            t = s[2:].strip()
            t = re.sub(r"^\[[^\]]*\]\s*", "", t)  # срезать "[Баг] " и т.п.
            return t or None
    return None


def _extract_url(text: str) -> str | None:
    for tok in text.replace("\n", " ").split():
        if tok.startswith("http://") or tok.startswith("https://"):
            return tok.rstrip(".,;:")
    return None


def submit_issue(
    provider: str,
    repo: str,
    title: str,
    body: str,
    *,
    labels: list[str] | None = None,
) -> dict[str, str]:
    """Создать issue в ``repo`` через gh/glab. Вернуть ``{url, provider, repo}``.

    GitHub: ``gh issue create --repo <repo> --title <t> --body <b> [--label …]``.
    GitLab: ``glab issue create --repo <repo> --title <t> --description <b> [--label l1,l2]``.
    """
    p = (provider or "").lower()
    if p not in PROVIDERS:
        raise SubmitError(f"provider '{provider}': github | gitlab.")

    if p == "github":
        cmd = ["gh", "issue", "create", "--repo", repo, "--title", title, "--body", body]
        for lbl in labels or []:
            cmd += ["--label", lbl]
    else:  # gitlab
        cmd = ["glab", "issue", "create", "--repo", repo, "--title", title,
               "--description", body, "--yes"]
        if labels:
            cmd += ["--label", ",".join(labels)]

    rc, out, err = run(cmd)
    if rc != 0:
        tool = "gh" if p == "github" else "glab"
        raise SubmitError(
            f"{tool} issue create failed (rc={rc}): {err.strip() or out.strip()}. "
            f"Залогинен? ({tool} auth status)"
        )
    return {"url": _extract_url(out) or out.strip(), "provider": p, "repo": repo}
