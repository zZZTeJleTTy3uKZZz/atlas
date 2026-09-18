"""issuekit — как правильно составить жалобу (issue/feature/handoff) + валидатор.

ЗАМОРОЖЕННАЯ vendored-копия кита ``issuekit`` (s-issuekit) — единственный
потребитель (Atlas) больше не тянет его с PyPI, код физически перенесён внутрь
``atlas`` (Atlas #2710, волна 5). Оригинал остаётся на диске в репозитории
``issuekit`` в замороженном виде (см. его README) — источник правды для этого
файла НЕ модифицируется здесь отдельно от оригинала намеренно (если понадобится
второй потребитель — расформировать обратно в отдельный кит).

Кит даёт: (1) шаблоны видов жалоб (bug / feature / handoff агент→агент) как
ДАННЫЕ; (2) ``lint`` — машинная проверка качества жалобы (каких полей чеклиста не
хватает + балл); (3) навык-гайд «как зарепортить проблему, чтобы вас не
возненавидели». Потребитель (Atlas) использует ``lint`` для блокирующей дисциплины
передачи задач между агентами — богатая обратная связь гарантирована.
"""
from __future__ import annotations

#: Версия зафиксирована на момент вендоринга (s-issuekit 0.2.0) — пакет больше
#: не публикуется отдельно, поэтому больше не читаем её из importlib.metadata
#: (дистрибутив ``s-issuekit`` в окружении atlas больше не установлен).
__version__ = "0.2.0"

from .lint import LintResult, lint
from .render import new_template, section_header
from .spec import KindSpec, Section, get_kind, list_kinds
from .submit import PROVIDERS, SubmitError, extract_title, submit_issue

__all__ = [
    "__version__",
    "lint",
    "LintResult",
    "new_template",
    "section_header",
    "get_kind",
    "list_kinds",
    "KindSpec",
    "Section",
    # отправка issue в GitHub/GitLab
    "submit_issue",
    "extract_title",
    "SubmitError",
    "PROVIDERS",
]
