#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
view_data.py · v1.2 · 21.08.2026
STRK ENGINE · готовые данные для отображения

ЗАЧЕМ
-----
По правилу из ПРАВИЛО_вычисления.md: браузер рисует, Python считает.
На 21.08 правило нарушалось в четырёх местах index.html:

  flowRead            19 использований · классификация фазы
  conflicts.push       8 · детектор противоречий
  buildDecision        2 · старый автор решения
  buildInvalidations   1 · условия отмены

Плюс я сам добавил нарушение в таблицу сигналов: сортировка по
важности и подсчёт категорий делались в браузере. Сортировка — это
суждение о том, что важнее, а не форматирование.

Хуже того, flowRead содержит ту самую классификацию, которую мы
забраковали в decision_engine v1.0: она даёт «Неопределённая» там,
где Dune видит MID_ACCUMULATION_STRONG. Браузер до сих пор показывал
устаревшую логику рядом с актуальной.

ЧТО ДЕЛАЕТ
----------
Собирает из готовых кэшей структуру, которую браузеру остаётся
только вывести. Никакой новой аналитики — только сведение и порядок.

  signals_table   строки таблицы уже отсортированы, счётчики посчитаны
  modal[TOKEN]    всё для модалки актива: фаза, конфликты, триггеры

Источники (все уже считаются другими модулями):
  decisions.json        решение, конфликты, триггеры, инвалидации
  asset_compass.json    балл и составляющие
  volume_profile.json   цели и стопы
  phase_analysis.json   динамика потока
  price_reference.json  цены с биржи
  hl_perps.json         перекос деривативов
  cvd_multi.json        агрессия

ЗАПУСК
------
  python3 scripts/view_data.py

ВЫХОД
-----
  data/cache/view_data.json
"""

import os
import json
from datetime import datetime, timezone

CACHE = "data/cache"
OUT_FILE = os.path.join(CACHE, "view_data.json")


def load(name, default=None):
    try:
        with open(os.path.join(CACHE, name), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default if default is not None else {}


# Порядок важности действий. Это суждение, поэтому оно здесь,
# а не в браузере: сначала то, что можно делать, потом то, чего нельзя.
ACTION_RANK = {
    "ВХОД ЧАСТЬЮ": 0,
    "ЖДАТЬ": 1,
    "НЕ ВХОДИТЬ": 2,
    "ДАННЫЕ ПОДОЗРИТЕЛЬНЫ": 3,
    "НЕТ ДАННЫХ": 4,
}

ACTION_COLOR = {
    "ВХОД ЧАСТЬЮ": "green",
    "ЖДАТЬ": "yellow",
    "НЕ ВХОДИТЬ": "red",
    "ДАННЫЕ ПОДОЗРИТЕЛЬНЫ": "muted",
    "НЕТ ДАННЫХ": "muted",
}

# Человеческие названия фаз — чтобы браузер не занимался переводом
PHASE_RU = {
    "MID_ACCUMULATION_STRONG": "зрелое накопление",
    "LATE_ACCUMULATION_OR_MARKUP": "поздняя стадия",
    "MID_ACCUMULATION": "накопление",
    "ACCUMULATION_PHASE_B": "база строится",
    "EARLY_ACCUMULATION": "ранняя стадия",
    "MIXED_OR_NEUTRAL": "без перевеса",
    "WEAKENING": "ослабление",
    "MARKDOWN": "падение",
    "DISTRIBUTION_ACTIVE": "распределение",
}

FLOW_RU = {
    "ACCEL_UP": "приток разгоняется",
    "STEADY_UP": "стабильный приток",
    "STALLING_UP": "приток затухает",
    "FLIPPING_UP": "развернулось вверх",
    "STABLE_ZERO": "поток около нуля",
    "FLIPPING_DOWN": "развернулось вниз",
    "STALLING_DOWN": "отток замедляется",
    "STEADY_DOWN": "стабильный отток",
    "ACCEL_DOWN": "отток ускоряется",
}

FLOW_TONE = {
    "ACCEL_UP": "green", "STEADY_UP": "green", "FLIPPING_UP": "green",
    "STALLING_DOWN": "yellow", "STALLING_UP": "yellow",
    "STABLE_ZERO": "muted",
    "FLIPPING_DOWN": "red", "STEADY_DOWN": "red", "ACCEL_DOWN": "red",
}


def fmt_price(p):
    """Форматирование здесь, чтобы одинаковое число выглядело одинаково везде."""
    if p is None:
        return None
    if p < 0.001:
        return f"{p:.8f}"
    if p < 1:
        return f"{p:.4f}"
    if p < 100:
        return f"{p:.2f}"
    return f"{p:,.2f}"


def build_whale_summary(whales):
    """Сводка по крупным игрокам для дашборда."""
    if not whales or whales.get("status") == "NOT_ENOUGH_DATA":
        return {
            "status": "NOT_ENOUGH_DATA",
            "text_ru": ("База адресов ещё копится. Сделки собираются каждые "
                        "15 минут, через сутки картина станет представительной."),
            "trades_in_log": (whales or {}).get("trades_in_log", 0),
        }

    by_token = whales.get("by_token") or {}
    active = {t: a for t, a in by_token.items() if a.get("status") == "OK"}
    divergent = whales.get("divergent_tokens") or []

    rows = []
    for t, a in active.items():
        total = (a.get("long_usd") or 0) + (a.get("short_usd") or 0)
        if total < WHALE_MIN_TOTAL_USD:
            continue
        rows.append({
            "token": t,
            "bias": a.get("bias"),
            "n_long": a.get("whales_long"),
            "n_short": a.get("whales_short"),
            "long_m": round((a.get("long_usd") or 0) / 1e6, 2),
            "short_m": round((a.get("short_usd") or 0) / 1e6, 2),
            "net_pct": a.get("net_pct"),
            "divergent": a.get("crowd_vs_whales") == "DIVERGENT",
            "crowd_vs_whales_ru": a.get("crowd_vs_whales_ru"),
            "text_ru": a.get("text_ru"),
        })
    # Крупнейшие позиции вперёд
    rows.sort(key=lambda r: -(abs(r["long_m"]) + abs(r["short_m"])))

    if divergent:
        headline = f"Толпа и крупные расходятся: {', '.join(divergent)}"
        tone = "caution"
        action = ("Расхождение обычно разрешается в пользу крупных. "
                  "По этим активам вход против них рискованнее обычного.")
    elif rows:
        headline = f"Крупные держат позиции по {len(rows)} активам"
        tone = "neutral"
        action = ("Расхождений с толпой нет — крупные и розница смотрят "
                  "в одну сторону.")
    else:
        headline = "Крупные без открытых позиций по нашим активам"
        tone = "neutral"
        action = "Смотреть не на что, ждём накопления данных."

    return {
        "status": "OK",
        "headline": headline,
        "tone": tone,
        "action": action,
        "rows": rows[:10],
        "divergent_tokens": divergent,
        "whales_directional": whales.get("whales_directional"),
        "market_makers_skipped": whales.get("market_makers_skipped"),
        "trades_in_log": whales.get("trades_in_log"),
        "computed_at": whales.get("computed_at"),
    }


def build_whale_cell(row):
    """
    Короткая ячейка для таблицы: только значимые позиции.
    Мелочь и отсутствие данных выглядят одинаково — прочерком,
    потому что и то и другое означает «сигнала здесь нет».
    """
    if not row or row.get("status") != "OK":
        return None

    long_usd = row.get("long_usd") or 0
    short_usd = row.get("short_usd") or 0
    total = long_usd + short_usd
    if total < WHALE_MIN_TOTAL_USD:
        return None

    bias = row.get("bias")
    net = long_usd - short_usd
    single = bias in ("SINGLE_WHALE_LONG", "SINGLE_WHALE_SHORT")

    if single:
        short_label, tone = "1 счёт", "yellow"
    elif bias == "WHALES_LONG":
        short_label, tone = "лонг", "green"
    elif bias == "WHALES_SHORT":
        short_label, tone = "шорт", "red"
    else:
        short_label, tone = "поровну", "muted"

    return {
        "label": short_label,
        "tone": tone,
        "net_m": round(net / 1e6, 2),
        "total_m": round(total / 1e6, 2),
        "n_long": row.get("whales_long"),
        "n_short": row.get("whales_short"),
        "divergent": row.get("crowd_vs_whales") == "DIVERGENT",
        "text_ru": row.get("text_ru"),
    }


def build_signals_table(dec, comp, vp, pa, whales=None):
    """
    Строки таблицы, уже отсортированные по важности, плюс счётчики.
    Браузеру остаётся пройти циклом и вывести.
    """
    decisions = (dec or {}).get("decisions") or {}
    compass = (comp or {}).get("tokens") or {}
    profiles = (vp or {}).get("tokens") or {}
    phases = (pa or {}).get("tokens") or {}
    whale_tokens = (whales or {}).get("by_token") or {}

    rows = []
    for token, d in decisions.items():
        c = compass.get(token) or {}
        v = profiles.get(token) or {}
        p = phases.get(token) or {}

        targets = v.get("targets") or {}
        up = (targets.get("nearest_up") or [None])[0]
        down = (targets.get("nearest_down") or [None])[0]

        action = d.get("action")
        flow = d.get("flow_regime")
        phase = d.get("stage")

        rows.append({
            "token": token,
            "action": action,
            "action_color": ACTION_COLOR.get(action, "muted"),
            "size_pct": d.get("size_pct") or 0,
            "compass_score": c.get("score"),
            "compass_verdict": c.get("verdict_ru"),
            "phase": phase,
            "phase_ru": PHASE_RU.get(phase, phase or "—"),
            "flow": flow,
            "flow_ru": FLOW_RU.get(flow, flow or "—"),
            "flow_tone": FLOW_TONE.get(flow, "muted"),
            "price": v.get("current_price"),
            "price_fmt": fmt_price(v.get("current_price")),
            "target_up": {
                "price_fmt": fmt_price(up["price"]),
                "distance_pct": up["distance_pct"],
                "label": up.get("label"),
            } if up else None,
            "target_down": {
                "price_fmt": fmt_price(down["price"]),
                "distance_pct": down["distance_pct"],
                "label": down.get("label"),
            } if down else None,
            "data_ok": (p.get("data_quality") or {}).get("ok", True),
            # Киты прямо в строке актива — отдельная карточка показывала
            # BTC и ETH, которыми мы не торгуем, и позиции по $200K
            "whales": build_whale_cell(whale_tokens.get(token)),
        })

    # Сортировка: сначала то, что можно делать; внутри — по размеру,
    # затем по баллу компаса. Это суждение о важности, поэтому в Python.
    rows.sort(key=lambda r: (
        ACTION_RANK.get(r["action"], 9),
        -(r["size_pct"] or 0),
        -(r["compass_score"] if r["compass_score"] is not None else -999),
    ))

    counts = {"enter": 0, "wait": 0, "avoid": 0, "bad_data": 0}
    for r in rows:
        a = r["action"]
        if a == "ВХОД ЧАСТЬЮ":
            counts["enter"] += 1
        elif a == "ЖДАТЬ":
            counts["wait"] += 1
        elif a == "НЕ ВХОДИТЬ":
            counts["avoid"] += 1
        elif a == "ДАННЫЕ ПОДОЗРИТЕЛЬНЫ":
            counts["bad_data"] += 1

    # Одна строка вывода — что делать сегодня
    if counts["enter"] == 0:
        headline = ("Ни одного входа сегодня. Это не сбой — сигналы честно "
                    "против. Лучшее действие: ничего не делать.")
        tone = "caution"
    elif counts["enter"] >= len(rows) * 0.3:
        headline = f"Широкий выбор: {counts['enter']} активов допущены к входу."
        tone = "positive"
    else:
        headline = (f"Выборочно: вход открыт по {counts['enter']} активам "
                    f"из {len(rows)}.")
        tone = "neutral"

    return {
        "rows": rows,
        "counts": counts,
        "total": len(rows),
        "headline": headline,
        "tone": tone,
        "engine_version": (dec or {}).get("engine_version"),
        "decisions_at": (dec or {}).get("computed_at"),
        "compass_at": (comp or {}).get("computed_at"),
    }


# Ниже этой суммы позиция не считается мнением крупного игрока.
# Один адрес с $200K — это не кит, а обычный трейдер, и показывать
# его как «крупные в лонгах» было бы враньём.
WHALE_MIN_TOTAL_USD = 1_000_000


def build_whale_view(row):
    """
    Готовая карточка по крупным игрокам. Тон и текст решаются здесь,
    потому что это суждение: перевес одного счёта и перевес шести
    адресов выглядят одинаково в цифрах, но значат разное.
    """
    if not row or row.get("status") != "OK":
        return {"status": "NONE",
                "text_ru": "никто из отслеживаемых крупных адресов "
                           "не держит позицию по этому активу"}

    total = (row.get("long_usd") or 0) + (row.get("short_usd") or 0)
    if total < WHALE_MIN_TOTAL_USD:
        return {"status": "TOO_SMALL",
                "total_usd": total,
                "text_ru": f"позиции крупных всего ${total/1e6:.2f}M — "
                           f"мало, чтобы считать это сигналом"}

    bias = row.get("bias")
    single = bias in ("SINGLE_WHALE_LONG", "SINGLE_WHALE_SHORT")

    if bias == "WHALES_LONG":
        tone = "green"
    elif bias == "WHALES_SHORT":
        tone = "red"
    elif single:
        tone = "yellow"
    else:
        tone = "muted"

    # Расхождение с толпой весит больше самого перевеса
    div = row.get("crowd_vs_whales")
    if div == "DIVERGENT":
        tone = "yellow"

    return {
        "status": "OK",
        "bias": bias,
        "tone": tone,
        "text_ru": row.get("text_ru"),
        "single_whale": single,
        "n_long": row.get("whales_long"),
        "n_short": row.get("whales_short"),
        "long_usd": row.get("long_usd"),
        "short_usd": row.get("short_usd"),
        "long_m": round((row.get("long_usd") or 0) / 1e6, 2),
        "short_m": round((row.get("short_usd") or 0) / 1e6, 2),
        "crowd_vs_whales": div,
        "crowd_vs_whales_ru": row.get("crowd_vs_whales_ru"),
        "top_long": row.get("top_long") or [],
        "top_short": row.get("top_short") or [],
    }


def build_modal_data(dec, comp, vp, pa, hl, cvd, pref, whales=None):
    """
    Всё, что нужно модалке актива, уже готовым. Раньше браузер сам
    выводил фазу через flowRead и собирал конфликты — обе логики
    дублировали decision_engine и успели от него отстать.
    """
    decisions = (dec or {}).get("decisions") or {}
    compass = (comp or {}).get("tokens") or {}
    profiles = (vp or {}).get("tokens") or {}
    phases = (pa or {}).get("tokens") or {}
    perps = (hl or {}).get("tokens") or {}
    cvds = (cvd or {}).get("tokens") or {}
    prices = (pref or {}).get("tokens") or {}
    whale_tokens = (whales or {}).get("by_token") or {}

    out = {}
    for token, d in decisions.items():
        p = phases.get(token) or {}
        v = profiles.get(token) or {}
        pr = prices.get(token) or {}
        regime = p.get("regime") or {}
        flow = p.get("flow") or {}

        # Фаза — из Dune-скана через decision_engine, а не выведенная заново
        phase = d.get("stage")

        out[token] = {
            "action": d.get("action"),
            "action_color": ACTION_COLOR.get(d.get("action"), "muted"),
            "size_pct": d.get("size_pct") or 0,
            "phase": phase,
            "phase_ru": PHASE_RU.get(phase, phase or "—"),
            "flow_code": regime.get("code"),
            "flow_ru": regime.get("text_ru"),
            "flow_tone": FLOW_TONE.get(regime.get("code"), "muted"),
            "flow_accel_4w_usd": flow.get("flow_accel_4w_usd"),
            "flow_last_4w_usd": flow.get("last_4w_usd"),
            # Конфликты, триггеры и инвалидации приходят от движка —
            # браузер больше не собирает их сам
            "conflicts": d.get("conflicts") or [],
            "conflicts_warn": d.get("conflicts_warn") or 0,
            "notes": d.get("notes") or [],
            "triggers": d.get("triggers") or [],
            "invalidations": d.get("invalidations") or [],
            "rules_fired": d.get("rules_fired") or [],
            "compass": {
                "score": (compass.get(token) or {}).get("score"),
                "verdict_ru": (compass.get(token) or {}).get("verdict_ru"),
                "components": (compass.get(token) or {}).get("components"),
                "coverage_pct": (compass.get(token) or {}).get("data_coverage_pct"),
            },
            "price": {
                "now": v.get("current_price") or pr.get("price_now"),
                "now_fmt": fmt_price(v.get("current_price") or pr.get("price_now")),
                "change_7d_pct": pr.get("change_7d_pct"),
                "change_30d_pct": pr.get("change_30d_pct"),
                "source": pr.get("source"),
            },
            "targets": v.get("targets") or {},
            "vp_position": v.get("position") or {},
            "hl": perps.get(token) or {},
            "cvd_consensus": (cvds.get(token) or {}).get("consensus") or {},
            "whales": build_whale_view(whale_tokens.get(token)),
            "data_quality": p.get("data_quality") or {"ok": True},
        }

    return out


def main():
    print("=== View Data v1.0 ===\n")

    dec = load("decisions.json")
    comp = load("asset_compass.json")
    vp = load("volume_profile.json")
    pa = load("phase_analysis.json")
    hl = load("hl_perps.json")
    cvd = load("cvd_multi.json")
    pref = load("price_reference.json")
    whales = load("hl_whale_positions.json")

    missing = [n for n, v in [("decisions", dec), ("compass", comp),
                              ("volume_profile", vp), ("phase_analysis", pa)]
               if not v]
    if missing:
        print(f"⚠ Нет данных: {', '.join(missing)}\n")

    table = build_signals_table(dec, comp, vp, pa, whales)
    whale_summary = build_whale_summary(whales)
    modal = build_modal_data(dec, comp, vp, pa, hl, cvd, pref, whales)

    out = {
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "purpose": ("Готовые данные для отображения. Браузер только выводит, "
                    "никаких вычислений на его стороне."),
        "signals_table": table,
        "whales": whale_summary,
        "modal": modal,
    }

    os.makedirs(CACHE, exist_ok=True)
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)

    c = table["counts"]
    print(f"  Таблица: {table['total']} строк")
    print(f"    вход {c['enter']} · ждать {c['wait']} · "
          f"не входить {c['avoid']} · данные битые {c['bad_data']}")
    print(f"  {table['headline']}")
    print(f"\n  Модалка: {len(modal)} активов")

    if table["rows"]:
        print("\n  Первые пять по важности:")
        for r in table["rows"][:5]:
            sc = f"{r['compass_score']:+.0f}" if r["compass_score"] is not None else "—"
            sz = f"{r['size_pct']}%" if r["size_pct"] else "—"
            print(f"    {r['token']:8} {r['action']:22} {sz:>5} компас {sc:>5}  {r['flow_ru']}")

    print(f"\n✓ {OUT_FILE}")


if __name__ == "__main__":
    main()
