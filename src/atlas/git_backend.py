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
        """``git remote add <name> <url>`` — idempotent.

        Если remote уже существует, обновляем URL через
        ``git remote set-url`` (W45-32b: addiction safety).
        """
        rc, _, err = run(["git", "remote", "add", name, url], cwd=path)
        if rc == 0:
            return
        if "already exists" in (err or ""):
            rc2, _, err2 = run(
                ["git", "remote", "set-url", name, url], cwd=path
            )
            if rc2 != 0:
                raise RuntimeError(
                    f"git remote set-url {name} {url} failed (rc={rc2}): {err2}"
                )
            return
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
        """Создать репозиторий в group_path через ``glab api POST projects``.

        W45-32a: ранее использовали ``glab repo create <group>/<repo>``, но
        glab/GitLab отвергал full-path namespace для subgroup'ов
        (``400 {namespace: }``). Корректный путь — POST /projects с
        ``namespace_id`` (целочисленным id группы).

        Алгоритм:
        1. ``glab api groups/<encoded_path>`` → получить namespace id.
        2. ``glab api -X POST projects -F namespace_id=<id> -F name=<repo>
           -F visibility=<private/public> -F default_branch=main``.

        Возвращает ``http_url_to_repo``.
        """
        # 1. Получить namespace_id для group_path.
        encoded_group = group_path.replace("/", "%2F")
        rc, out, err = run(["glab", "api", f"groups/{encoded_group}"])
        if rc != 0:
            raise RuntimeError(
                f"glab api groups/{group_path} failed (rc={rc}): "
                f"{err.strip() or out.strip()}"
            )
        try:
            group_data = json.loads(out)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"glab api groups/{group_path} вернул не-JSON: {out!r}"
            ) from exc
        namespace_id = group_data.get("id")
        if not namespace_id:
            raise RuntimeError(
                f"glab api groups/{group_path}: в ответе нет id: {group_data!r}"
            )

        # 2. Создать project через POST.
        visibility = "private" if private else "public"
        cmd: list[str] = [
            "glab", "api", "--method", "POST", "projects",
            "--field", f"name={repo_name}",
            "--field", f"namespace_id={namespace_id}",
            "--field", f"visibility={visibility}",
            "--field", "default_branch=main",
        ]
        rc, out, err = run(cmd)
        if rc != 0:
            raise RuntimeError(
                f"glab api POST projects {group_path}/{repo_name} "
                f"failed (rc={rc}): {err.strip() or out.strip()}"
            )
        try:
            proj_data = json.loads(out)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"glab api POST projects вернул не-JSON: {out!r}"
            ) from exc
        url = proj_data.get("http_url_to_repo") or proj_data.get(
            "web_url"
        )
        if not url:
            raise RuntimeError(
                f"glab api POST projects: в ответе нет URL: {proj_data!r}"
            )
        # GitLab возвращает http_url_to_repo с .git, web_url без — используем
        # http_url_to_repo как канон.
        if not url.endswith(".git"):
            url = url + ".git"
        return url.strip()

    def transfer_to_group(
        self,
        repo_full_path: str,
        new_group_path: str,
    ) -> str:
        """Перенести репо в другую группу через ``glab api PUT /transfer``.

        W45-32j: ``glab repo transfer ... --group ...`` не имеет флага
        ``--group`` — gh CLI не принимает его. Использовать API напрямую:
        ``PUT /projects/<id>/transfer`` с ``namespace=<group_id>``.

        Алгоритм:
        1. Получить project_id для repo_full_path.
        2. Получить namespace_id для new_group_path.
        3. PUT /projects/<id>/transfer.
        """
        repo_name = repo_full_path.rsplit("/", 1)[-1]

        # 1. project_id.
        encoded_repo = repo_full_path.replace("/", "%2F")
        rc, out, err = run(["glab", "api", f"projects/{encoded_repo}"])
        if rc != 0:
            raise RuntimeError(
                f"glab api projects/{repo_full_path} failed (rc={rc}): "
                f"{err.strip() or out.strip()}"
            )
        try:
            proj_data = json.loads(out)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"glab api projects/{repo_full_path} вернул не-JSON: {out!r}"
            ) from exc
        project_id = proj_data.get("id")
        if not project_id:
            raise RuntimeError(
                f"glab api projects/{repo_full_path}: в ответе нет id: "
                f"{proj_data!r}"
            )

        # 2. namespace_id.
        encoded_group = new_group_path.replace("/", "%2F")
        rc, out, err = run(["glab", "api", f"groups/{encoded_group}"])
        if rc != 0:
            raise RuntimeError(
                f"glab api groups/{new_group_path} failed (rc={rc}): "
                f"{err.strip() or out.strip()}"
            )
        try:
            group_data = json.loads(out)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"glab api groups/{new_group_path} вернул не-JSON: {out!r}"
            ) from exc
        namespace_id = group_data.get("id")
        if not namespace_id:
            raise RuntimeError(
                f"glab api groups/{new_group_path}: в ответе нет id: "
                f"{group_data!r}"
            )

        # 3. PUT /projects/<id>/transfer.
        rc, out, err = run([
            "glab", "api", "--method", "PUT",
            f"projects/{project_id}/transfer",
            "--field", f"namespace={namespace_id}",
        ])
        if rc != 0:
            raise RuntimeError(
                f"glab api PUT projects/{project_id}/transfer "
                f"failed (rc={rc}): {err.strip() or out.strip()}"
            )
        try:
            transferred = json.loads(out)
        except json.JSONDecodeError:
            transferred = {}
        url = transferred.get("http_url_to_repo")
        if url:
            if not url.endswith(".git"):
                url = url + ".git"
            return url.strip()
        return f"https://gitlab.com/{new_group_path}/{repo_name}.git"

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
# GitHubBackend                                                               #
# --------------------------------------------------------------------------- #


class GitHubBackend:
    """Реализация ``GitBackend`` через ``gh`` CLI (GitHub).

    Авторизация: ``gh auth login`` (хранит токен сам) или env ``GH_TOKEN`` /
    ``GITHUB_TOKEN``. Не передаём токен флагами — secret hygiene, как у glab.

    Модель namespacing у GitHub плоская: ``group_path`` = **owner** (личный
    аккаунт или организация), без вложенных подгрупп как в GitLab. Поэтому
    ``derive_group_path`` (gitlab-вложенность) для github не применяется —
    owner резолвится отдельно (``atlas.git_paths.resolve_github_owner``).
    """

    def create_remote(
        self,
        group_path: str,
        repo_name: str,
        *,
        private: bool = True,
    ) -> str:
        """Создать репозиторий ``<owner>/<repo>`` через ``gh repo create``.

        ``group_path`` — owner (user/org). Команда:
        ``gh repo create <owner>/<repo> --private|--public``. URL детерминирован
        (``https://github.com/<owner>/<repo>.git``) — gh печатает его в stdout,
        но конструируем сами как канон (на случай иного формата вывода).
        """
        owner = (group_path or "").strip("/")
        full = f"{owner}/{repo_name}" if owner else repo_name
        cmd = [
            "gh", "repo", "create", full,
            "--private" if private else "--public",
        ]
        rc, out, err = run(cmd)
        if rc != 0:
            raise RuntimeError(
                f"gh repo create {full} failed (rc={rc}): "
                f"{err.strip() or out.strip()}"
            )
        # gh печатает URL в stdout — попробуем взять его, иначе сконструируем.
        url = _extract_url(out) or _extract_url(err)
        if not url and owner:
            url = f"https://github.com/{owner}/{repo_name}"
        if not url:
            raise RuntimeError(
                f"gh repo create {full}: не удалось определить URL "
                f"(stdout={out!r})"
            )
        url = url.rstrip("/")
        if not url.endswith(".git"):
            url = url + ".git"
        return url

    def transfer_to_group(
        self,
        repo_full_path: str,
        new_group_path: str,
    ) -> str:
        """Перенести репо новому owner через ``gh api .../transfer``.

        У GitHub перенос = смена owner (``POST /repos/<owner>/<repo>/transfer``
        с ``new_owner``), без вложенных групп. ``new_group_path`` = новый owner.
        """
        repo_name = repo_full_path.rsplit("/", 1)[-1]
        new_owner = new_group_path.strip("/").rsplit("/", 1)[-1]
        rc, out, err = run([
            "gh", "api", "--method", "POST",
            f"repos/{repo_full_path}/transfer",
            "--field", f"new_owner={new_owner}",
        ])
        if rc != 0:
            raise RuntimeError(
                f"gh api transfer {repo_full_path} → {new_owner} "
                f"failed (rc={rc}): {err.strip() or out.strip()}"
            )
        return f"https://github.com/{new_owner}/{repo_name}.git"

    def get_remote_status(self, repo_full_path: str) -> dict[str, Any]:
        """``gh repo view <repo> --json …`` → dict, нормализованный под общий ключ.

        Возвращает ``web_url`` (как у GitLab-бэкенда, чтобы sync-from-remote был
        provider-agnostic) + ``default_branch`` + ``visibility`` (lower).
        """
        cmd = [
            "gh", "repo", "view", repo_full_path,
            "--json", "name,url,defaultBranchRef,visibility",
        ]
        rc, out, err = run(cmd)
        if rc != 0:
            raise RuntimeError(
                f"gh repo view {repo_full_path} failed (rc={rc}): "
                f"{err.strip() or out.strip()}"
            )
        try:
            data = json.loads(out)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"gh repo view вернул не-JSON: {out!r} ({exc})"
            ) from exc
        if not isinstance(data, dict):
            raise RuntimeError(f"gh repo view вернул не-dict JSON: {data!r}")
        branch_ref = data.get("defaultBranchRef") or {}
        return {
            "web_url": data.get("url"),
            "name": data.get("name"),
            "default_branch": (
                branch_ref.get("name") if isinstance(branch_ref, dict) else None
            ),
            "visibility": str(data.get("visibility") or "").lower(),
        }


# --------------------------------------------------------------------------- #
# Backend factory                                                             #
# --------------------------------------------------------------------------- #

#: Поддерживаемые git-провайдеры (совпадает с CHECK-constraint в models.py).
SUPPORTED_PROVIDERS: frozenset[str] = frozenset({"gitlab", "github"})


def get_backend(provider: str) -> GitBackend:
    """Фабрика провайдера remote-хостинга по строке (``gitlab``/``github``).

    Raises ``ValueError`` для неизвестного провайдера — caller конвертит в
    typer.Exit. Регистронезависимо; пустое → gitlab (исторический дефолт).
    """
    p = (provider or "gitlab").strip().lower()
    if p == "gitlab":
        return GitLabBackend()
    if p == "github":
        return GitHubBackend()
    raise ValueError(
        f"Неизвестный git-провайдер '{provider}'. "
        f"Поддерживаются: {', '.join(sorted(SUPPORTED_PROVIDERS))}."
    )


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
