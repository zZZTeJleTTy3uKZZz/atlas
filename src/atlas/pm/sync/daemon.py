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
    # .../src/atlas/pm/sync/daemon.py → корень проекта (4 уровня вверх от файла)
    return Path(__file__).resolve().parents[4]


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


def install(*, run=None) -> dict:
    """Зарегистрировать и запустить демон (идемпотентно)."""
    if os.name != "nt":
        return {"ok": False, "error": "демон поддерживается только на Windows"}
    run = run or _run_ps
    bash = _bash_exe()
    if bash is None:
        return {"ok": False, "error": "git-bash не найден (установи Git for Windows)"}
    root = _atlas_root()
    root_unix = _to_unix(bash, root)

    vbs = root / "scripts" / "sync_watch_headless.vbs"
    vbs.parent.mkdir(parents=True, exist_ok=True)
    vbs.write_text(
        "' sync_watch_headless.vbs — АВТОГЕН daemon.install, не редактировать.\n"
        "' Запускает atlas sync watch через git-bash без окна (Run стиль 0 = SW_HIDE).\n"
        "Option Explicit\n"
        "Dim sh\n"
        'Set sh = CreateObject("WScript.Shell")\n'
        f'sh.Run "{bash} -l -c ""cd \'{root_unix}\' && uv run atlas --text sync watch""", 0, False\n',
        encoding="cp1251",
    )

    ps = f"""
$ErrorActionPreference='Stop'
$Action = New-ScheduledTaskAction -Execute "wscript.exe" -Argument '"{vbs}"'
$Trigger = New-ScheduledTaskTrigger -AtLogOn
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
    -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries `
    -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit (New-TimeSpan -Days 3650)
Get-ScheduledTask -TaskName "{TASK_NAME}" -ErrorAction SilentlyContinue | Unregister-ScheduledTask -Confirm:$false
Register-ScheduledTask -TaskName "{TASK_NAME}" -Description "atlas: фоновый long-poll синк с хабом" -Action $Action -Trigger $Trigger -Settings $Settings | Out-Null
Start-ScheduledTask -TaskName "{TASK_NAME}"
Write-Output "installed"
""".strip()
    res = run(ps)
    return {
        "ok": res.returncode == 0,
        "task": TASK_NAME,
        "stdout": res.stdout.strip(),
        "stderr": res.stderr.strip(),
    }


def uninstall(*, run=None) -> dict:
    if os.name != "nt":
        return {"ok": False, "error": "только Windows"}
    run = run or _run_ps
    ps = (
        f'Get-ScheduledTask -TaskName "{TASK_NAME}" -ErrorAction SilentlyContinue '
        f'| Unregister-ScheduledTask -Confirm:$false; Write-Output "removed"'
    )
    res = run(ps)
    return {"ok": res.returncode == 0, "task": TASK_NAME, "stdout": res.stdout.strip()}


def status(*, run=None) -> dict:
    if os.name != "nt":
        return {"ok": False, "error": "только Windows"}
    run = run or _run_ps
    ps = (
        f'$t = Get-ScheduledTask -TaskName "{TASK_NAME}" -ErrorAction SilentlyContinue; '
        f'if ($t) {{ Write-Output $t.State }} else {{ Write-Output "NOT_INSTALLED" }}'
    )
    res = run(ps)
    state = (res.stdout or "").strip()
    return {"ok": res.returncode == 0, "task": TASK_NAME,
            "installed": state != "NOT_INSTALLED", "state": state}


__all__ = ["install", "uninstall", "status", "TASK_NAME"]
