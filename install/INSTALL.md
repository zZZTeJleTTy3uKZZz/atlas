# Установка atlas

CLI-команда — `atlas`; PyPI-пакет — `atlas-pm`. Требуется Python ≥ 3.11 (ставится вместе с `uv`, если его нет).

## Одной командой (ставит `uv` при необходимости)

**Linux / macOS:**
```bash
curl -fsSL https://raw.githubusercontent.com/zZZTeJleTTy3uKZZz/atlas/master/install/install.sh | sh
```

**Windows (PowerShell):**
```powershell
irm https://raw.githubusercontent.com/zZZTeJleTTy3uKZZz/atlas/master/install/install.ps1 | iex
```

Скрипт ставит `uv` (Astral), если его нет, затем `uv tool install atlas-pm` (изолированное окружение, свой Python). Скрипты намеренно **ASCII-only** — иначе `irm | iex` / `| sh` могут испортить не-ASCII символы.

## Другие способы

- **skillery** — ставит CLI и заодно навык для Claude-агента:
  ```bash
  skillery install atlas
  ```
- **uv / pipx** напрямую:
  ```bash
  uv tool install atlas-pm      # или: pipx install atlas-pm
  ```
- **Из исходников** (разработка / editable):
  ```bash
  git clone https://github.com/zZZTeJleTTy3uKZZz/atlas.git
  cd atlas && uv tool install --editable .
  ```

## Первый запуск

```bash
atlas config set owner <you>   # владелец стора (актор аудита, владелец новых проектов)
atlas init                     # прописать Atlas-дисциплину в агентские файлы (CLAUDE.md/AGENTS.md)
atlas project init             # применить миграции БД + справочники
```

## Обновление

```bash
atlas update           # self-update с PyPI (детектит uv/pipx/pip)
atlas update --check   # только показать текущую/доступную версию
```
