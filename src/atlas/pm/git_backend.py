"""Git и GitLab бэкенды для atlas projects git ...

Модуль содержит:
- ``run(cmd, cwd=...)`` — единая обёртка над ``subprocess.run``, возвращает
  кортеж ``(returncode, stdout, stderr)``. Все subprocess вызовы проходят через
  него, что даёт одну точку для мокинга в тестах.
- ``LocalGitOps`` — тонкий обёрточный слой вокруг локального ``git`` CLI
  (init, add+commit, remote add, set default branch, push, status).
- ``GitBackend`` (Protocol) — интерфейс провайдера remote-хостинга.
- ``GitLabBackend`` — реализация ``GitBackend`` через ``glab`` CLI
  (создаёт remote, переносит репо между группами, читает remote status).

Дизайн-принципы:
- Нет глобальной авторизации в коде: ``glab`` уже знает токен из env
  ``GITLAB_TOKEN``. Если токена нет — ``glab`` сам ругнётся, мы пробрасываем.
- Все subprocess — через ``run()``, чтобы тестам нужно было замокать одну
  функцию, а не ``subprocess.run`` напрямую.
- ``LocalGitOps`` принимает ``Path`` (или строку) как cwd и не знает про БД.
- Никаких ``shell=True``: всегда список args.
"""
from __future__ import annotations

import json
import shlex
import subprocess
from pathlib import Path
from typing import Any, Optional, Protocol, Sequence, Union

PathLike = Union[str, Path]


# --------------------------------------------------------------------------- #
# subprocess helper                                                           #
# --------------------------------------------------------------------------- #


def run(
    cmd: Sequence[str],
    *,
    cwd: Optional[PathLike] = None,
) -> tuple[int, str, str]:
    """Выполнить команду и вернуть ``(returncode, stdout, stderr)``.

    Не raise-ит на ненулевой returncode — caller сам решает, что делать.
    Всегда text mode + capture_output (encoding по локали ОС).
    """
    proc = subprocess.run(
        list(cmd),
        cwd=str(cwd) if cwd is not None else None,
        text=True,
        capture_output=True,
        check=False,
    )
    return proc.returncode, proc.stdout or "", proc.stderr or ""


# --------------------------------------------------------------------------- #
# Local git operations                                                        #
# --------------------------------------------------------------------------- #


class LocalGitOps:
    """Тонкая обёртка вокруг локального git CLI.

    Все методы принимают ``path`` (рабочая директория для git-команды).
    Возврат:
    - ``init`` → None
    - ``add_all_commit`` → SHA нового коммита (str).
    - ``add_remote`` → None.
    - ``set_default_branch`` → None.
    - ``push`` → None (raise на ошибку).
    - ``status`` → dict ``{branch, ahead, behind, dirty, last_sha}``.
    """

    def init(self, path: PathLike) -> None:
        """Инициализировать репозиторий в ``path``."""
        rc, _, err = run(["git", "init"], cwd=path)
        if rc != 0:
            raise RuntimeError(f"git init failed (rc={rc}): {err}")

    def add_all_commit(self, path: PathLike, message: str) -> str:
        """``git add -A`` + ``git commit -m`` + вернуть SHA HEAD."""
        rc, _, err = run(["git", "add", "-A"], cwd=path)
        if rc != 0:
            raise RuntimeError(f"git add failed (rc={rc}): {err}")

        rc, _, err = run(["git", "commit", "-m", message], cwd=path)
        if rc != 0:
            raise RuntimeError(f"git commit failed (rc={rc}): {err}")

        rc, out, err = run(["git", "rev-parse", "HEAD"], cwd=path)
        if rc != 0:
            raise RuntimeError(f"git rev-parse HEAD failed (rc={rc}): {err}")
        return out.strip()

    def add_remote(self, path: PathLike, name: str, url: str) -> None:
        """``git remote add <name> <url>``."""
        rc, _, err = run(["git", "remote", "add", name, url], cwd=path)
        if rc != 0:
            raise RuntimeError(
                f"git remote add {name} {url} failed (rc={rc}): {err}"
            )

    def set_default_branch(self, path: PathLike, name: str) -> None:
        """Установить default branch.

        Реализация: ``git symbolic-ref HEAD refs/heads/<name>`` — работает
        даже на пустом репо, не требует коммитов.
        """
        rc, _, err = run(
            ["git", "symbolic-ref", "HEAD", f"refs/heads/{name}"], cwd=path
        )
        if rc != 0:
            raise RuntimeError(
                f"git symbolic-ref refs/heads/{name} failed (rc={rc}): {err}"
            )

    def push(self, path: PathLike, branch: str = "main") -> None:
        """``git push -u origin <branch>``."""
        rc, _, err = run(
            ["git", "push", "-u", "origin", branch], cwd=path
        )
        if rc != 0:
            raise RuntimeError(
                f"git push origin {branch} failed (rc={rc}): {err}"
            )

    def status(self, path: PathLike) -> dict[str, Any]:
        """Прочитать состояние локального репо.

        Парсит ``git status --branch --porcelain=v2`` и ``git rev-parse HEAD``.
        Если HEAD ещё нет (пустой repo) — last_sha=None, dirty=True/False по
        наличию нестейдженных изменений.
        """
        rc, out, err = run(
            ["git", "status", "--branch", "--porcelain=v2"], cwd=path
        )
        if rc != 0:
            raise RuntimeError(f"git status failed (rc={rc}): {err}")

        branch: Optional[str] = None
        ahead = 0
        behind = 0
        dirty = False

        for raw_line in out.splitlines():
            if not raw_line:
                continue
            if raw_line.startswith("# branch.head "):
                branch = raw_line[len("# branch.head ") :].strip()
            elif raw_line.startswith("# branch.ab "):
                # пример: "# branch.ab +2 -1"
                parts = raw_line[len("# branch.ab ") :].strip().split()
                for p in parts:
                    if p.startswith("+"):
                        try:
                            ahead = int(p[1:])
                        except ValueError:
                            ahead = 0
                    elif p.startswith("-"):
                        try:
                            behind = int(p[1:])
                        except ValueError:
                            behind = 0
            elif raw_line[:1] in ("1", "2", "u", "?"):
                # entries про tracked changes / unstaged / untracked.
                dirty = True

        # last_sha
        rc, sha_out, _ = run(["git", "rev-parse", "HEAD"], cwd=path)
        last_sha = sha_out.strip() if rc == 0 else None

        return {
            "branch": branch,
            "ahead": ahead,
            "behind": behind,
            "dirty": dirty,
            "last_sha": last_sha,
        }


# --------------------------------------------------------------------------- #
# GitBackend Protocol                                                         #
# --------------------------------------------------------------------------- #


class GitBackend(Protocol):
    """Provider-agnostic интерфейс для удалённого git-хостинга."""

    def create_remote(
        self,
        group_path: str,
        repo_name: str,
        *,
        private: bool = True,
    ) -> str:
        """Создать репозиторий в группе ``group_path``. Вернуть URL."""

    def transfer_to_group(
        self,
        repo_full_path: str,
        new_group_path: str,
    ) -> str:
        """Перенести репо в новую группу. Вернуть новый URL."""

    def get_remote_status(self, repo_full_path: str) -> dict[str, Any]:
        """Получить мета-информацию по remote (URL, default_branch, visibility)."""


# --------------------------------------------------------------------------- #
# GitLabBackend                                                               #
# --------------------------------------------------------------------------- #


class GitLabBackend:
    """Реализация ``GitBackend`` через ``glab`` CLI.

    Авторизация: env ``GITLAB_TOKEN`` (User scope). glab сам читает env.
    Не пытаемся передавать токен через флаги — secret hygiene.
    """

    def create_remote(
        self,
        group_path: str,
        repo_name: str,
        *,
        private: bool = True,
    ) -> str:
        """``glab repo create <group_path>/<repo_name> [-p|-public]``.

        Возвращает URL созданного репо. Если glab вернул ненулевой код —
        бросаем ``RuntimeError`` со stderr.
        """
        full_path = f"{group_path}/{repo_name}"
        cmd: list[str] = ["glab", "repo", "create", full_path]
        if private:
            cmd.append("--private")
        else:
            cmd.append("--public")

        rc, out, err = run(cmd)
        if rc != 0:
            raise RuntimeError(
                f"glab repo create {full_path} failed (rc={rc}): {err.strip() or out.strip()}"
            )

        # glab печатает URL на stdout (точный формат может варьироваться);
        # ищем первую подстроку, начинающуюся на http(s).
        url = _extract_url(out) or _extract_url(err) or full_path
        return url.strip()

    def transfer_to_group(
        self,
        repo_full_path: str,
        new_group_path: str,
    ) -> str:
        """``glab repo transfer <repo_full_path> --group <new_group_path>``.

        Возвращает новый URL после переноса (или собранный
        ``new_group_path/<repo_name>``, если glab не печатает URL).
        """
        repo_name = repo_full_path.rsplit("/", 1)[-1]
        cmd = [
            "glab",
            "repo",
            "transfer",
            repo_full_path,
            "--group",
            new_group_path,
        ]
        rc, out, err = run(cmd)
        if rc != 0:
            raise RuntimeError(
                f"glab repo transfer {repo_full_path} → {new_group_path} "
                f"failed (rc={rc}): {err.strip() or out.strip()}"
            )

        url = _extract_url(out) or _extract_url(err)
        if url:
            return url.strip()
        return f"{new_group_path}/{repo_name}"

    def get_remote_status(self, repo_full_path: str) -> dict[str, Any]:
        """``glab repo view <repo_full_path> -F json`` → распарсенный dict.

        Если JSON не парсится (или glab вернул ошибку) — RuntimeError.
        """
        cmd = ["glab", "repo", "view", repo_full_path, "-F", "json"]
        rc, out, err = run(cmd)
        if rc != 0:
            raise RuntimeError(
                f"glab repo view {repo_full_path} failed (rc={rc}): "
                f"{err.strip() or out.strip()}"
            )
        try:
            data = json.loads(out)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"glab repo view вернул не-JSON: {out!r} ({exc})"
            ) from exc
        if not isinstance(data, dict):
            raise RuntimeError(
                f"glab repo view вернул не-dict JSON: {data!r}"
            )
        return data


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


def _extract_url(text: str) -> Optional[str]:
    """Найти первую URL-подобную подстроку в тексте (http(s)://...)."""
    for token in shlex.split(text.replace("\n", " ").replace("\r", " ")):
        if token.startswith("http://") or token.startswith("https://"):
            # Без trailing punctuation (точка/запятая часто прилипают).
            return token.rstrip(".,;:")
    return None
