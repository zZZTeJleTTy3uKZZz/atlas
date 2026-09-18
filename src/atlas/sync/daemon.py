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


def _log_name(profile: str | None) -> str:
    """Свой лог у каждого профиля — иначе два демона пишут в один файл вперемешку."""
    return f"sync-watch{('-' + profile) if profile else ''}.log"


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

    # Вывод демона идёт В ФАЙЛ, и это не удобство, а условие работоспособности.
    # Окно скрыто (Run style 0), поэтому без лога у фонового синка нет НИКАКОГО
    # следа: он может падать на первой секунде, а планировщик будет показывать
    # «задача выполнена успешно». Разбирались с этим вслепую — демон числился
    # живым, очередь копилась, и понять причину было нечем.
    лог = root / "logs" / _log_name(profile)
    лог.parent.mkdir(parents=True, exist_ok=True)
    лог_unix = _to_unix(bash, лог)

    vbs = root / "scripts" / _vbs_name(profile)
    vbs.parent.mkdir(parents=True, exist_ok=True)
    vbs.write_text(
        "' sync_watch_headless.vbs - AUTOGEN by daemon.install, do not edit.\n"
        "' Runs 'atlas sync watch' via git-bash with no window (Run style 0 = SW_HIDE).\n"
        "Option Explicit\n"
        "Dim sh\n"
        'Set sh = CreateObject("WScript.Shell")\n'
        # Путь к bash ОБЯЗАН быть в кавычках: git-bash лежит в «C:\\Program
        # Files\\Git», и без кавычек WScript.Shell берёт за имя программы
        # «C:\\Program» — команда не запускается вовсе. Снаружи это выглядело
        # как «задача выполнена успешно, а демона нет»: планировщик отчитывался
        # нулём, лога не появлялось, очередь копилась. Демон не стартовал ни разу.
        f'sh.Run """{bash}"" -l -c ""cd \'{root_unix}\' && uv run atlas {profile_flag}--text sync watch '
        # Последний параметр — ЖДАТЬ завершения (True), и это принципиально.
        # С False wscript запускал bash и сразу выходил; планировщик видел
        # «задача завершилась успешно» и по своим правилам запускал её снова —
        # демоны множились, за вечер набралось одиннадцать штук, и все они
        # дёргали хаб наперегонки. С True задача остаётся Running ровно столько,
        # сколько живёт синк, и второй экземпляр не стартует.
        f'>> \'{лог_unix}\' 2>&1""", 0, True\n',
        encoding="ascii",
    )

    # Автозапуск — через папку автозагрузки пользователя, а НЕ через планировщик.
    #
    # Планировщик выглядел естественным выбором и три часа обманывал: задача
    # числилась Running, процессы жили, а синка не было. Причина обнаружилась
    # только диагностикой изнутри — процесс, порождённый планировщиком, видит
    # каталог настроек В УРЕЗАННОМ ВИДЕ: `config.toml` для него не существует
    # (в листинге лежит один `Cache`). Конфиг не читается, base_url падает на
    # умолчание `http://localhost:8000`, и каждый запрос даёт ConnectError,
    # который снаружи выглядит как сетевая беда.
    #
    # Тот же VBS, запущенный из пользовательской сессии, работает без единой
    # правки. Поэтому ярлык кладётся в Startup: он стартует в полноценном
    # сеансе человека, со всеми его настройками и хранилищем ключей.
    #
    # Плата: демон поднимается при входе в систему, а не «всегда». Для рабочей
    # машины это ровно то, что нужно, — синк нужен, когда за ней работают.
    автозагрузка = (
        Path(os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming")))
        / "Microsoft"
        / "Windows"
        / "Start Menu"
        / "Programs"
        / "Startup"
    )
    ярлык = автозагрузка / _vbs_name(profile)

    ps = f"""
$ErrorActionPreference='Stop'
# Прежние экземпляры гасятся ЯВНО и ВСЕЙ цепочкой: wscript запускает bash, тот —
# uv, uv — atlas, atlas — python, и цикл крутит именно python. Пока гасили один
# bash, осиротевшие python продолжали работать: за вечер их набралось шесть
# штук, каждый со своим состоянием, и все дёргали хаб наперегонки.
# Себя и оболочку, выполняющую этот скрипт, исключаем: её командная строка тоже
# содержит искомую подстроку.
Get-CimInstance Win32_Process |
    Where-Object {{ $_.ProcessId -ne $PID -and $_.Name -ne 'powershell.exe' -and $_.CommandLine -like '*sync watch*' }} |
    ForEach-Object {{ Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }}

# Старая задача планировщика снимается, если осталась от прежних версий.
Get-ScheduledTask -TaskName "{task}" -ErrorAction SilentlyContinue | Unregister-ScheduledTask -Confirm:$false

New-Item -ItemType Directory -Force -Path "{автозагрузка}" | Out-Null
Copy-Item -Path "{vbs}" -Destination "{ярлык}" -Force
Start-Process -FilePath "wscript.exe" -ArgumentList '"{vbs}"' -WindowStyle Hidden
Write-Output "installed"
""".strip()
    res = run(ps)
    итог = {
        "ok": res.returncode == 0,
        "task": task,
        "profile": profile,
        "autostart": str(ярлык),
        "log": str(лог),
        "stdout": res.stdout.strip(),
        "stderr": res.stderr.strip(),
    }
    if not итог["ok"]:
        # Человеку нужно СЛОВО о том, что демона нет: без него очередь копится
        # молча, и обнаруживается это случайно, спустя сотни операций.
        итог["error"] = "демон НЕ установлен — синк останется ручным (atlas sync push)"
        if "denied" in res.stderr.lower() or "0x80070005" in res.stderr:
            итог["error"] += "; отказ прав при записи автозапуска"
    return итог


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
