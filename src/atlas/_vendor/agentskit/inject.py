"""Идемпотентная инъекция managed-блока в текст агентского файла.

Чистый текст-ин → текст-аут (без I/O) — тестируемо; запись/детект файлов — в
``onboard``/``targets``. Алгоритм перенесён из Atlas ``agent_discipline`` 1:1
(выстраданные тонкости сохранены — см. ниже) и параметризован ``namespace``.

Выстраданные тонкости (НЕ переписывать):
- ``pattern.sub(lambda _m: managed, ...)`` — lambda во избежание интерпретации
  ``\\``-групп в теле блока как backreference (RUF-safe).
- логика разделителя при дописывании (`""` / `"\\n"` / `"\\n\\n"`).
- ``re.DOTALL`` — блок может быть многострочным.
"""
from __future__ import annotations

import re

from .markers import begin_marker, end_marker


def managed_block(body: str, *, namespace: str) -> str:
    """Полный managed-блок с маркерами ``namespace`` (то, что вставляется)."""
    return f"{begin_marker(namespace)}\n{body}\n{end_marker(namespace)}"


def has_managed_block(text: str, *, namespace: str) -> bool:
    """Есть ли уже наш managed-блок (оба маркера ``namespace``) в тексте."""
    return begin_marker(namespace) in text and end_marker(namespace) in text


def inject_managed_block(
    text: str,
    body: str | None = None,
    *,
    namespace: str,
    block: str | None = None,
    begin: str | None = None,
    end: str | None = None,
) -> str:
    """Вставить/обновить managed-блок ``namespace`` в ``text``. Чужое не затирает.

    - маркеры уже есть → заменяем СОДЕРЖИМОЕ между ними (обновление);
    - маркеров нет → дописываем блок в КОНЕЦ (с корректным разделителем).

    ``body`` — тело блока (без маркеров); ``block`` — целиком готовый блок (тогда
    ``body`` игнорируется). Идемпотентно: повторный вызов с тем же содержимым →
    no-op по смыслу (текст совпадает).
    """
    b = begin if begin is not None else begin_marker(namespace)
    e = end if end is not None else end_marker(namespace)
    if block is not None:
        managed = block
    else:
        if body is None:
            raise ValueError("inject_managed_block: нужен body или block.")
        managed = managed_block(body, namespace=namespace)

    pattern = re.compile(re.escape(b) + r".*?" + re.escape(e), re.DOTALL)
    if pattern.search(text):
        # lambda во избежание интерпретации \-групп в managed как backreference.
        return pattern.sub(lambda _m: managed, text, count=1)
    if not text.strip():
        return managed + "\n"
    sep = "\n" if text.endswith("\n") else "\n\n"
    if text.endswith("\n\n"):
        sep = ""
    return text + sep + managed + "\n"


def strip_managed_block(text: str, *, namespace: str) -> str:
    """Удалить managed-блок ``namespace`` из текста (для uninstall).

    Чужие блоки других namespace не трогаются. Если блока нет — текст возвращается
    как есть. Подчищает лишний разделитель, оставшийся после вырезания.
    """
    b = begin_marker(namespace)
    e = end_marker(namespace)
    pattern = re.compile(
        r"\n*" + re.escape(b) + r".*?" + re.escape(e) + r"\n*", re.DOTALL
    )
    if not pattern.search(text):
        return text
    out = pattern.sub("\n", text, count=1)
    # не оставляем ведущий перевод строки, если блок был в начале файла.
    return out.lstrip("\n") if not text[: text.find(b)].strip() else out
