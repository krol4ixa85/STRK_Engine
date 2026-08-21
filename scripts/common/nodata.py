#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
common/nodata.py · v1.0 · 21.08.2026
STRK ENGINE · отсутствие данных как явное состояние

ЗАЧЕМ
-----
Правило платформы: никаких выдуманных чисел. На практике оно нарушалось
системно — в коллекторах и в движке отсутствие данных сплошь и рядом
превращалось в утверждение о рынке:

  нет фандинга  → rate = 0    → «фандинг близок к нулю, перекоса нет»
  нет режима    → ×0.8        → размер урезан на 20% «потому что»
  нет качества  → {"ok": True} → sanity-фильтр молча пропускает всё
  нет блока     → 0.0 в сумме → балл компаса разбавлен к нейтрали
  нет цены      → 0           → «стоп $0.0000 (-2%)» в Telegram

Каждый из этих нулей выглядит как измерение. Отличить «померили и
получили ноль» от «не смогли померить» после этого невозможно —
ни человеку, ни следующему модулю, ни бэктесту.

ЧТО ДАЁТ
--------
  MISSING           единственный маркер отсутствия
  is_missing(x)     проверка
  require(...)      достать значение или получить MISSING
  weighted(...)     взвешенная сумма ТОЛЬКО по присутствующим частям,
                    с честным процентом покрытия
  explain(...)      человеческая строка про то, чего не хватило

ПРИНЦИП
-------
Отсутствие данных не голосует. Оно не бычье и не медвежье, оно не
нейтральное — его просто нет, и это должно быть видно в выходном JSON
и на экране. Модуль, который не может посчитать, обязан сказать
«не могу», а не выдать правдоподобное число.

ИСПОЛЬЗОВАНИЕ
-------------
    from common.nodata import MISSING, is_missing, require, weighted

    mult = require(regime, "multiplier")
    if is_missing(mult):
        notes.append("Режим рынка неизвестен — размер не корректируем.")
        mult = 1.0            # нейтральный элемент, а не выдуманный 0.8
"""


class _Missing:
    """
    Единственный экземпляр. Ложный в булевом контексте, чтобы
    `if value:` вёл себя предсказуемо, но отличим от 0 и None
    через is_missing().
    """
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __bool__(self):
        return False

    def __repr__(self):
        return "MISSING"

    def __str__(self):
        return "нет данных"


MISSING = _Missing()


def is_missing(value):
    """True только для MISSING. Ноль, None и пустая строка — не оно."""
    return value is MISSING


def present(value):
    """Обратное is_missing. Читается лучше в условиях."""
    return value is not MISSING


def require(source, *keys, cast=None):
    """
    Достаёт вложенное значение или возвращает MISSING.

    require(row, "position", "above_kind")

    None внутри данных тоже считается отсутствием: коллекторы пишут
    None именно там, где не смогли посчитать. А вот 0 и False —
    настоящие значения и проходят как есть.
    """
    cur = source
    for k in keys:
        if not isinstance(cur, dict):
            return MISSING
        if k not in cur:
            return MISSING
        cur = cur[k]
    if cur is None:
        return MISSING
    if cast is not None:
        try:
            return cast(cur)
        except (TypeError, ValueError):
            return MISSING
    return cur


def weighted(parts, min_coverage=0.7):
    """
    Взвешенная сумма по ЧАСТЯМ, КОТОРЫЕ ЕСТЬ.

    parts: список (значение, вес, имя). Значение может быть MISSING.

    Возвращает словарь:
      score           сумма, перенормированная на присутствующие веса
      coverage_pct    какая доля веса реально измерена
      enough          хватает ли покрытия для суждения
      missing         имена отсутствующих частей

    Зачем перенормировка. Раньше отсутствующий блок входил в сумму
    нулём и всё равно умножался на свой вес: токен с ончейн-баллом
    -1.00 и без двух других блоков получал -40 («шорт») вместо -100
    («сильный шорт»). Отсутствие данных работало как голос за
    нейтральность — ровно то, чего быть не должно.
    """
    total_w = 0.0
    used_w = 0.0
    acc = 0.0
    missing = []

    for value, weight, name in parts:
        total_w += weight
        if is_missing(value):
            missing.append(name)
            continue
        used_w += weight
        acc += value * weight

    if total_w <= 0:
        return {"score": MISSING, "coverage_pct": 0, "enough": False,
                "missing": missing}

    coverage = used_w / total_w
    if used_w <= 0:
        return {"score": MISSING, "coverage_pct": 0, "enough": False,
                "missing": missing}

    return {
        "score": acc / used_w,
        "coverage_pct": round(coverage * 100),
        "enough": coverage >= min_coverage,
        "missing": missing,
    }


def explain(missing_names, subject="оценки"):
    """Человеческая строка о том, чего не хватило."""
    if not missing_names:
        return ""
    if len(missing_names) == 1:
        return f"Для {subject} не хватает данных: {missing_names[0]}."
    return f"Для {subject} не хватает данных: {', '.join(missing_names)}."