"""Git worktree-циклы под эпик (#300) — логика над ``git_backend.run``.

Идея: на время работы над эпиком заводим изолированный git worktree + ветку
``epic/<slug>`` в репозитории проекта эпика. Закончили (приёмка эпика) — мёржим
ветку в base и убираем worktree. Состояние worktree — в самом git (источник
правды), без отдельной схемы в БД: ветка детерминирована из slug, а `git
worktree list` отдаёт фактическую раскладку.

Все subprocess — через ``atlas.git_backend.run`` (одна точка мокинга в тестах).
Модуль без typer/БД-зависимостей: чистая логика, удобно юнит-тестировать.

Безопасность merge: НЕ переключаем ветки молча и НЕ трогаем грязное дерево —
merge требует, чтобы основной репозиторий был чист и стоял на base-ветке (иначе
понятная ошибка с подсказкой). «git — карта, при расхождении верь git».
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from atlas.git_backend import PathLike, run


class WorktreeError(RuntimeError):
    """Ошибка операции worktree (caller конвертит в CliError/Exit)."""


def epic_branch(slug: str) -> str:
    """Имя ветки эпика — детерминированно из slug: ``epic/<slug>``."""
    return f"epic/{slug}"


def default_worktree_path(repo_path: PathLike, slug: str) -> Path:
    """Путь worktree по умолчанию — sibling вне основного дерева репо.

    ``<repo>.worktrees/epic-<slug>`` рядом с репо (worktree НЕ должен лежать
    внутри основного рабочего дерева — это путает git).
    """
    repo = Path(repo_path)
    return repo.parent / f"{repo.name}.worktrees" / f"epic-{slug}"


def _git(repo_path: PathLike, *args: str) -> tuple[int, str, str]:
    return run(["git", *args], cwd=repo_path)


def current_branch(repo_path: PathLike) -> Optional[str]:
    """Текущая ветка основного репо (``None`` если detached/ошибка)."""
    rc, out, _ = _git(repo_path, "rev-parse", "--abbrev-ref", "HEAD")
    if rc != 0:
        return None
    br = out.strip()
    return None if br in ("", "HEAD") else br


def is_clean(repo_path: PathLike) -> bool:
    """Чистое ли рабочее дерево основного репо (нет незакоммиченных изменений)."""
    rc, out, err = _git(repo_path, "status", "--porcelain")
    if rc != 0:
        raise WorktreeError(f"git status failed: {err.strip() or out.strip()}")
    return out.strip() == ""


def branch_exists(repo_path: PathLike, branch: str) -> bool:
    """Существует ли локальная ветка ``branch``."""
    rc, _, _ = _git(
        repo_path, "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"
    )
    return rc == 0


def list_worktrees(repo_path: PathLike) -> list[dict[str, Any]]:
    """Распарсить ``git worktree list --porcelain`` → список dict.

    Каждый элемент: ``{path, head, branch}`` (branch без ``refs/heads/`` или
    ``None`` для detached). ``is_epic`` помечает worktree эпика (ветка epic/*).
    """
    rc, out, err = _git(repo_path, "worktree", "list", "--porcelain")
    if rc != 0:
        raise WorktreeError(
            f"git worktree list failed: {err.strip() or out.strip()}"
        )
    items: list[dict[str, Any]] = []
    cur: dict[str, Any] = {}
    for line in out.splitlines():
        line = line.rstrip()
        if not line:
            if cur:
                items.append(cur)
                cur = {}
            continue
        if line.startswith("worktree "):
            cur = {"path": line[len("worktree "):], "head": None, "branch": None}
        elif line.startswith("HEAD "):
            cur["head"] = line[len("HEAD "):]
        elif line.startswith("branch "):
            ref = line[len("branch "):]
            cur["branch"] = ref.replace("refs/heads/", "", 1)
        elif line == "detached":
            cur["branch"] = None
    if cur:
        items.append(cur)
    for it in items:
        it["is_epic"] = bool(it.get("branch") and it["branch"].startswith("epic/"))
    return items


def find_worktree(repo_path: PathLike, branch: str) -> Optional[dict[str, Any]]:
    """Найти worktree по ветке (или ``None``)."""
    for wt in list_worktrees(repo_path):
        if wt.get("branch") == branch:
            return wt
    return None


def add_worktree(
    repo_path: PathLike,
    path: PathLike,
    branch: str,
    base: str,
) -> dict[str, Any]:
    """Создать worktree ``path`` на ветке ``branch`` (от ``base``).

    Если ветки нет — ``git worktree add <path> -b <branch> <base>`` (создаёт).
    Если ветка уже есть — ``git worktree add <path> <branch>`` (переиспользует).
    Идемпотентность на уровне «уже есть worktree этой ветки» проверяет caller.
    """
    path = str(path)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    if branch_exists(repo_path, branch):
        rc, out, err = _git(repo_path, "worktree", "add", path, branch)
    else:
        rc, out, err = _git(repo_path, "worktree", "add", path, "-b", branch, base)
    if rc != 0:
        raise WorktreeError(
            f"git worktree add failed: {err.strip() or out.strip()}"
        )
    return {"path": path, "branch": branch, "base": base}


def merge_into(
    repo_path: PathLike,
    branch: str,
    into: str,
    *,
    no_ff: bool = True,
    push: bool = False,
) -> dict[str, Any]:
    """Влить ``branch`` в ``into`` в ОСНОВНОМ репо (безопасно).

    Требует: основной репозиторий чист и стоит на ветке ``into`` (иначе
    ``WorktreeError`` с подсказкой — молча ветки не переключаем). При успехе
    делает ``git merge [--no-ff] <branch>``; опц. ``git push origin <into>``.
    """
    if not branch_exists(repo_path, branch):
        raise WorktreeError(f"Ветка '{branch}' не найдена — нечего мёржить.")
    cur = current_branch(repo_path)
    if cur != into:
        raise WorktreeError(
            f"Основной репо на ветке '{cur or 'detached'}', а нужно '{into}'. "
            f"Переключись: git switch {into} (или укажи --into {cur})."
        )
    if not is_clean(repo_path):
        raise WorktreeError(
            f"Рабочее дерево '{into}' грязное — закоммить/спрячь изменения перед merge."
        )
    merge_args = ["merge"]
    if no_ff:
        merge_args.append("--no-ff")
    merge_args.append(branch)
    rc, out, err = _git(repo_path, *merge_args)
    if rc != 0:
        # откатываем незавершённый merge, чтобы не оставить дерево в конфликте.
        _git(repo_path, "merge", "--abort")
        raise WorktreeError(
            f"git merge {branch} → {into} failed (откатил): "
            f"{err.strip() or out.strip()}"
        )
    result: dict[str, Any] = {"branch": branch, "into": into, "merged": True,
                              "pushed": False}
    if push:
        prc, pout, perr = _git(repo_path, "push", "origin", into)
        if prc != 0:
            raise WorktreeError(
                f"merge ок, но push origin {into} failed: "
                f"{perr.strip() or pout.strip()}"
            )
        result["pushed"] = True
    return result


def remove_worktree(
    repo_path: PathLike,
    path: PathLike,
    *,
    force: bool = False,
) -> None:
    """Снять worktree ``path`` (``git worktree remove [--force]``)."""
    args = ["worktree", "remove"]
    if force:
        args.append("--force")
    args.append(str(path))
    rc, out, err = _git(repo_path, *args)
    if rc != 0:
        raise WorktreeError(
            f"git worktree remove failed: {err.strip() or out.strip()}"
        )
