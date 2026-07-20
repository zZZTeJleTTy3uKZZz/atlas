"""Фоновый sync-демон через Windows Task Scheduler.

Регистрирует задачу `atlas-sync-watch`, запускающую `atlas sync watch` без окна
(headless VBS, как scripts/backup/register_task.ps1) при входе пользователя,
с авто-рестартом при падении. Не-Windows → {"ok": False}.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

TASK_NAME = "atlas-sync-watch"

_BASH_CANDIDATES = (
    r"C:\Program Files\Git\bin\bash.exe",
    r"C:\Program Files (x86)\Git\bin\bash.exe",
)


def _resolve_profile(profile: str | None) -> str | None:
    """Активный профиль: явный аргумент или env ``ATLAS_PROFILE`` (его ставит
    корневой callback при ``atlas --profile <p> ...``)."""
    return profile or os.environ.get("ATLAS_PROFILE") or None


def _task_name(profile: str | None) -> str:
    """Имя задачи планировщика. С профилем — суффикс, чтобы профили (owner
    «мои задачи» / admin «все») имели независимые демоны и не затирали друг друга."""
    return f"{TASK_NAME}-{profile}" if profile else TASK_NAME


def _vbs_name(profile: str | None) -> str:
    return f"sync_watch_headless-{profile}.vbs" if profile else "sync_watch_headless.vbs"


def _bash_exe() -> str | None:
    for p in _BASH_CANDIDATES:
        if Path(p).exists():
            return p
    local = os.environ.get("LOCALAPPDATA")
    if local:
        cand = Path(local) / "Programs" / "Git" / "bin" / "bash.exe"
        if cand.exists():
            return str(cand)
    return None


def _atlas_root() -> Path:
    """Корень репозитория atlas (каталог с pyproject.toml).

    Был жёсткий ``parents[4]`` с комментарием про несуществующий путь
    ``src/atlas/pm/sync/daemon.py``: после переименования каталога pm→sync
    исчез уровень вложенности, и индекс стал указывать НА УРОВЕНЬ ВЫШЕ корня —
    демон (`sync watch`) стартовал из чужой папки (аудит [6]). Ищем маркер вверх
    по дереву — устойчиво к будущим переносам файла.
    """
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "pyproject.toml").exists():
            return parent
    return here.parents[3]  # .../src/atlas/sync/daemon.py → корень


def _run_ps(script: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True, text=True,
    )


def _to_unix(bash: str, win_path: Path) -> str:
    try:
        r = subprocess.run([bash, "-c", f"cygpath '{win_path}'"],
                           capture_output=True, text=True)
        out = r.stdout.strip()
        if out:
            return out
    except Exception:  # noqa: BLE001
        pass
    return str(win_path).replace("\\", "/")


def install(*, profile: str | None = None, run=None) -> dict:
    """Зарегистрировать и запустить демон (идемпотентно).

    ``profile`` (или env ``ATLAS_PROFILE``) пробрасывается в фоновую команду как
    ``atlas --profile <p> sync watch`` — так демон крутит long-poll в нужном
    профиле (``owner`` → scope=personal, ``admin`` → scope=all). У каждого
    профиля своя задача планировщика (имя с суффиксом)."""
    if os.name != "nt":
        return {"ok": False, "error": "демон поддерживается только на Windows"}
    run = run or _run_ps
    bash = _bash_exe()
    if bash is None:
        return {"ok": False, "error": "git-bash не найден (установи Git for Windows)"}
    profile = _resolve_profile(profile)
    task = _task_name(profile)
    root = _atlas_root()
    root_unix = _to_unix(bash, root)
    profile_flag = f"--profile {profile} " if profile else ""

    vbs = root / "scripts" / _vbs_name(profile)
    vbs.parent.mkdir(parents=True, exist_ok=True)
    vbs.write_text(
        "' sync_watch_headless.vbs - AUTOGEN by daemon.install, do not edit.\n"
        "' Runs 'atlas sync watch' via git-bash with no window (Run style 0 = SW_HIDE).\n"
        "Option Explicit\n"
        "Dim sh\n"
        'Set sh = CreateObject("WScript.Shell")\n'
        f'sh.Run "{bash} -l -c ""cd \'{root_unix}\' && uv run atlas {profile_flag}--text sync watch""", 0, False\n',
        encoding="ascii",
    )

    ps = f"""
$ErrorActionPreference='Stop'
$Action = New-ScheduledTaskAction -Execute "wscript.exe" -Argument '"{vbs}"'
$Trigger = New-ScheduledTaskTrigger -AtLogOn
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
    -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries `
    -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit (New-TimeSpan -Days 3650)
Get-ScheduledTask -TaskName "{task}" -ErrorAction SilentlyContinue | Unregister-ScheduledTask -Confirm:$false
Register-ScheduledTask -TaskName "{task}" -Description "atlas: фоновый long-poll синк с хабом" -Action $Action -Trigger $Trigger -Settings $Settings | Out-Null
Start-ScheduledTask -TaskName "{task}"
Write-Output "installed"
""".strip()
    res = run(ps)
    return {
        "ok": res.returncode == 0,
        "task": task,
        "profile": profile,
        "stdout": res.stdout.strip(),
        "stderr": res.stderr.strip(),
    }


def uninstall(*, profile: str | None = None, run=None) -> dict:
    if os.name != "nt":
        return {"ok": False, "error": "только Windows"}
    run = run or _run_ps
    task = _task_name(_resolve_profile(profile))
    ps = (
        f'Get-ScheduledTask -TaskName "{task}" -ErrorAction SilentlyContinue '
        f'| Unregister-ScheduledTask -Confirm:$false; Write-Output "removed"'
    )
    res = run(ps)
    return {"ok": res.returncode == 0, "task": task, "stdout": res.stdout.strip()}


def status(*, profile: str | None = None, run=None) -> dict:
    if os.name != "nt":
        return {"ok": False, "error": "только Windows"}
    run = run or _run_ps
    task = _task_name(_resolve_profile(profile))
    ps = (
        f'$t = Get-ScheduledTask -TaskName "{task}" -ErrorAction SilentlyContinue; '
        f'if ($t) {{ Write-Output $t.State }} else {{ Write-Output "NOT_INSTALLED" }}'
    )
    res = run(ps)
    state = (res.stdout or "").strip()
    return {"ok": res.returncode == 0, "task": task,
            "installed": state != "NOT_INSTALLED", "state": state}


__all__ = ["install", "uninstall", "status", "TASK_NAME"]
