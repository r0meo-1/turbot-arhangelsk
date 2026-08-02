"""Какой именно код сейчас выполняется.

«Задеплоил, а изменений не видно» — вопрос, который должен закрываться одним
curl, а не сессией по SSH. Причин обычно три: pull не прошёл, сервис не
перезапущен, или процесс держит в памяти старый импорт. Все три различимы по
паре «коммит + время старта».

Читается из .git напрямую: подпроцесс к git тянул бы зависимость в юнит с
урезанными правами ради строки, которая лежит в текстовом файле.
"""

from __future__ import annotations

import os
import time
from typing import Optional

_START_TS = time.time()


def _read(path: str) -> Optional[str]:
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return None


def git_revision(base_dir: Optional[str] = None) -> str:
    """Короткий SHA HEAD или "unknown". Никогда не бросает.

    Диагностика не имеет права ронять health-check: эндпойнт, падающий при
    ответе на вопрос «жив ли ты», хуже отсутствующего.
    """
    root = base_dir or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    git_dir = os.path.join(root, ".git")

    head = _read(os.path.join(git_dir, "HEAD"))
    if not head:
        return "unknown"
    if not head.startswith("ref: "):
        return head[:7]

    ref = head[5:].strip()
    sha = _read(os.path.join(git_dir, ref))
    if sha:
        return sha[:7]

    # Свежий клон держит ссылки упакованными, отдельного файла для ветки нет.
    packed = _read(os.path.join(git_dir, "packed-refs")) or ""
    for line in packed.splitlines():
        if line.startswith("#") or " " not in line:
            continue
        value, name = line.split(" ", 1)
        if name.strip() == ref:
            return value[:7]
    return "unknown"


def uptime_seconds() -> int:
    """Секунды с импорта модуля — то есть с реального старта процесса."""
    return int(time.time() - _START_TS)


REVISION = git_revision()
