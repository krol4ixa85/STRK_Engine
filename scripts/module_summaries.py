#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
module_summaries.py · v1.0 · 21.08.2026
STRK ENGINE · короткий вывод по каждому модулю

ЗАЧЕМ
-----
Дашборд показывает много данных и почти нигде не говорит, что они значат.
Человек видит TVL, фандинг, доминацию, стейблы — и должен сам собрать
из этого вывод. Каждый раз заново.

Здесь для каждого модуля строится один абзац: что происходит и что это
значит для решений. Тот же тон, что в модалке актива.

ПОЧЕМУ В PYTHON
---------------
По правилу из ПРАВИЛО_вычисления.md: браузер рисует, Python считает.
Вывод — это решение о том, что важно, а что нет. Значит он должен быть
записан в файл и проверяем, а не собран на лету в браузере.

ЧТО ДЕЛАЕТ
----------
Читает существующие кэши и пишет для каждого модуля:

  headline   одна строка — суть
  body       два-четыре предложения — что происходит
  action     что это значит для входов и выходов
  tone       positive / caution / negative / neutral, для цвета

Модули:
  macro       глобальный контекст: ставки, доллар, VIX, золото
  market      фаза крипторынка: BTC.D, доминации, markup/markdown
  regime      взвешенный режим и его составляющие
  rotation    куда идёт капитал по секторам
  universe    сводка по всем токенам: сколько к входу, сколько к выходу

ЗАПУСК
------
  python3 scripts/module_summaries.py

ВЫХОД
-----
  data/cache/module_summaries.json
"""

import os
import json
from datetime import datetime, timezone
from collections import Counter

CACHE = "data/cache"
OUT_FILE = os.path.join(CACHE, "module_summaries.json")


def load(name, default=None):
    try:
        with open(os.path.join(CACHE, name), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default if default is not None else {}


def pct(v, digits=1):
    if v is None:
        return "—"
    return f"{v:+.{digits}f}%"


# ─────────────────────────────────────────────────────────────
# МАКРО · ставки, доллар, риск
# ─────────────────────────────────────────────────────────────

def summarize_macro(macro):
    m = (macro or {}).get("metrics") or {}
    reg = (macro or {}).get("regime") or {}

    def val(key, field="value"):
        row = m.get(key) or {}
        return row.get(field)

    def chg(key):
        row = m.get(key) or {}
        for f in ("change_pct", "change_1d_pct", "pct_change"):
            if row.get(f) is not None:
                return row[f]
        return None

    fed = val("fed_rate")
    us10y = val("us10y")
    dxy_chg = chg("dxy")
    vix = val("vix")
    gold_chg = chg("gold")

    parts = []
    risk_on, risk_off = 0, 0

    if us10y is not None:
        u_chg = chg("us10y")
        if u_chg is not None and u_chg > 0.5:
            parts.append(f"доходность десятилеток растёт до {us10y:.2f}%")
            risk_off += 1
        elif u_chg is not None and u_chg < -0.5:
            parts.append(f"доходность десятилеток падает до {us10y:.2f}%")
            risk_on += 1
        else:
            parts.append(f"десятилетки стабильны около {us10y:.2f}%")

    if dxy_chg is not None:
        if dxy_chg < -1:
            parts.append(f"доллар слабеет ({pct(dxy_chg)})")
            risk_on += 1
        elif dxy_chg > 1:
            parts.append(f"доллар крепнет ({pct(dxy_chg)})")
            risk_off += 1

    if vix is not None:
        if vix > 25:
            parts.append(f"страх на рынках высокий (VIX {vix:.1f})")
            risk_off += 1
        elif vix < 15:
            parts.append(f"рынки спокойны (VIX {vix:.1f})")
            risk_on += 1

    if gold_chg is not None and gold_chg > 2:
        parts.append(f"золото растёт ({pct(gold_chg)}) — деньги ищут защиту")
        risk_off += 1

    if risk_on > risk_off:
        tone = "positive"
        headline = "Внешний фон помогает рисковым активам"
        action = ("Макро не мешает входам. Это не повод покупать само по себе, "
                  "но убирает один из тормозов.")
    elif risk_off > risk_on:
        tone = "caution"
        headline = "Внешний фон против риска"
        action = ("Даже хорошие сигналы по отдельным активам будут отрабатывать "
                  "медленнее. Размер стоит держать меньше обычного.")
    else:
        tone = "neutral"
        headline = "Внешний фон нейтрален"
        action = ("Макро сейчас не двигает крипту ни в одну сторону. "
                  "Смотрим на сигналы самих активов.")

    body = ". ".join(p.capitalize() if i == 0 else p
                     for i, p in enumerate(parts)) + "." if parts else \
        "Данных по макро недостаточно."

    if fed is not None:
        body += f" Ставка ФРС {fed:.2f}%."

    return {"headline": headline, "body": body, "action": action, "tone": tone}


# ─────────────────────────────────────────────────────────────
# ФАЗА КРИПТОРЫНКА
# ─────────────────────────────────────────────────────────────

def summarize_market(total):
    if not total:
        return None

    btc_d = total.get("btc_dominance")
    eth_d = total.get("eth_dominance")
    t3_d = total.get("total3_dominance")
    mcap_t = total.get("total_mcap_t")
    signal = total.get("market_signal")
    btc_phase = (total.get("btc_phase") or {}).get("phase")
    btc_chg = (total.get("btc_phase") or {}).get("change_30d_pct")
    eth_phase = (total.get("eth_phase") or {}).get("phase")
    eth_chg = (total.get("eth_phase") or {}).get("change_30d_pct")

    bits = []
    if mcap_t:
        bits.append(f"Весь крипторынок стоит ${mcap_t:.1f} трлн")
    if btc_phase:
        p_ru = {"MARKUP": "растёт", "MARKDOWN": "падает",
                "ACCUMULATION": "накапливается", "DISTRIBUTION": "распределяется"}
        b = p_ru.get(btc_phase, btc_phase.lower())
        s = f"биткоин {b}"
        if btc_chg is not None:
            s += f" ({pct(btc_chg, 0)} за месяц)"
        bits.append(s)
    if eth_phase and eth_chg is not None:
        bits.append(f"эфир {pct(eth_chg, 0)}")

    body = ", ".join(bits) + "."

    # Доминация решает, попадут ли деньги в альты
    if btc_d is not None:
        if btc_d > 57:
            tone = "caution"
            headline = f"Биткоин забирает деньги, альты под давлением"
            body += (f" Доминация биткоина {btc_d:.1f}% — это высоко. "
                     f"Пока она не начнёт падать, альты будут отставать "
                     f"даже при хороших собственных сигналах.")
            action = ("Альты покупать можно, но размером меньше обычного. "
                      "Разворот доминации вниз — сигнал увеличивать.")
        elif btc_d < 50:
            tone = "positive"
            headline = "Деньги перетекают в альты"
            body += (f" Доминация биткоина {btc_d:.1f}% и снижается — "
                     f"капитал уходит из BTC в остальной рынок.")
            action = "Условия для альтов лучшие. Размер можно держать полный."
        else:
            tone = "neutral"
            headline = "Рынок без явного перевеса"
            body += f" Доминация биткоина {btc_d:.1f}% — середина диапазона."
            action = "Смотрим на сигналы конкретных активов, общий фон нейтрален."
    else:
        tone, headline, action = "neutral", "Фаза рынка", "—"

    if signal == "BULL_MARKET" and btc_d and btc_d > 57:
        headline = "Бычий рынок, но деньги сидят в биткоине"

    return {"headline": headline, "body": body, "action": action, "tone": tone}


# ─────────────────────────────────────────────────────────────
# ВЗВЕШЕННЫЙ РЕЖИМ
# ─────────────────────────────────────────────────────────────

def summarize_regime(regime):
    if not regime:
        return None

    score = regime.get("weighted_score")
    name = regime.get("regime")
    comps = regime.get("component_scores") or {}

    label = {
        "STRONG_BULL": "сильный бычий",
        "BULL_EARLY": "ранний бычий",
        "BULL_BIAS": "уклон вверх",
        "NEUTRAL_MIXED": "смешанные сигналы",
        "BEAR_BIAS": "уклон вниз",
        "BEAR_DEVELOPING": "медвежий развивается",
        "STRONG_BEAR": "сильный медвежий",
    }.get(name, name)

    # Кто тянет вверх, кто вниз
    ru = {"macro": "макро", "phase": "фаза рынка", "news": "новости",
          "stables": "стейблы", "funding": "фандинг"}
    up = [ru.get(k, k) for k, v in comps.items() if isinstance(v, (int, float)) and v > 20]
    down = [ru.get(k, k) for k, v in comps.items() if isinstance(v, (int, float)) and v < -20]

    body = f"Общий балл {score:+.0f} из 100 — {label}."
    if up:
        body += f" За рост: {', '.join(up)}."
    if down:
        body += f" Против: {', '.join(down)}."
    if up and down:
        body += " Составляющие спорят между собой, и это главное здесь."

    if score is None:
        tone, action = "neutral", "—"
    elif score >= 40:
        tone = "positive"
        action = "Условия благоприятные. Размер позиций можно держать полный."
    elif score >= 10:
        tone = "neutral"
        action = "Лёгкий перевес вверх. Размер чуть ниже обычного."
    elif score > -10:
        tone = "neutral"
        action = ("Режим не даёт преимущества ни одной стороне. "
                  "Движок автоматически режет размеры, и это правильно.")
    else:
        tone = "negative"
        action = "Режим против риска. Новые входы только с сильным сигналом и малым размером."

    return {
        "headline": f"Рынок: {label}",
        "body": body,
        "action": action,
        "tone": tone,
    }


# ─────────────────────────────────────────────────────────────
# СВОДКА ПО ЮНИВЕРСУ · главный вывод дня
# ─────────────────────────────────────────────────────────────

def summarize_universe(decisions, compass, vp):
    dec = (decisions or {}).get("decisions") or {}
    comp = (compass or {}).get("tokens") or {}
    vps = (vp or {}).get("tokens") or {}

    if not dec:
        return None

    actions = Counter(d.get("action") for d in dec.values())
    n_total = len(dec)
    n_enter = actions.get("ВХОД ЧАСТЬЮ", 0)
    n_avoid = actions.get("НЕ ВХОДИТЬ", 0)
    n_wait = actions.get("ЖДАТЬ", 0)
    n_bad = actions.get("ДАННЫЕ ПОДОЗРИТЕЛЬНЫ", 0)

    # Сколько токенов выше зоны объёма — признак перегретости
    above = sum(1 for v in vps.values()
                if (v.get("position") or {}).get("code") == "ABOVE_VALUE")
    below = sum(1 for v in vps.values()
                if (v.get("position") or {}).get("code") == "BELOW_VALUE")
    n_vp = sum(1 for v in vps.values() if v.get("status") == "OK")

    # Распределение компаса
    scores = [c.get("score") for c in comp.values()
              if c.get("score") is not None]
    n_long = sum(1 for s in scores if s > 10)
    n_short = sum(1 for s in scores if s < -10)

    bits = [f"Из {n_total} токенов движок допускает вход в {n_enter}"]
    if n_avoid:
        bits.append(f"{n_avoid} прямо запрещает")
    if n_wait:
        bits.append(f"{n_wait} велит ждать")
    body = ", ".join(bits) + "."

    if n_vp and above / n_vp > 0.8:
        body += (f" {above} из {n_vp} торгуются выше зоны реального объёма — "
                 f"то есть выше цен, где на самом деле шли сделки.")

    if scores and n_short > n_long * 2:
        body += (f" Компас показывает перевес в шорт у {n_short} активов "
                 f"против {n_long} в лонг.")

    # Главный вывод
    if n_enter == 0:
        tone = "negative"
        headline = "Входов нет ни по одному активу"
        action = ("Это не сбой — сигналы честно против. Лучшее действие "
                  "сегодня: ничего не делать и ждать смены картины.")
    elif n_vp and above / n_vp > 0.8 and n_short > n_long:
        tone = "caution"
        headline = "Альты в конце локального роста"
        action = ("Вход дорог: цены ушли выше зон объёма, поддержки снизу нет. "
                  "Не крах, но фаза, где входить надо малым размером "
                  "и держать стопы близко.")
    elif n_enter >= n_total * 0.3:
        tone = "positive"
        headline = f"Широкий выбор входов: {n_enter} активов"
        action = "Условия хорошие. Выбирай по силе сигнала, а не по количеству."
    else:
        tone = "neutral"
        headline = f"Выборочные входы: {n_enter} из {n_total}"
        action = ("Работают немногие активы. Концентрируйся на них, "
                  "остальное не трогай.")

    if n_bad:
        body += f" У {n_bad} активов данные забракованы фильтром качества."

    return {"headline": headline, "body": body, "action": action, "tone": tone}


# ─────────────────────────────────────────────────────────────
# РОТАЦИЯ ПО СЕКТОРАМ
# ─────────────────────────────────────────────────────────────

def summarize_rotation(sector):
    rows = (sector or {}).get("rows") or []
    if not rows:
        return None

    flows = []
    for r in rows:
        s = r.get("sector")
        f = r.get("net_flow_m_usd")
        if s and isinstance(f, (int, float)):
            flows.append((s, f))
    if not flows:
        return None

    flows.sort(key=lambda x: -x[1])
    inflow = [f for f in flows if f[1] > 0]
    outflow = [f for f in flows if f[1] < 0]

    top = ", ".join(f"{s} {f:+.1f}M" for s, f in flows[:3])
    bot = ", ".join(f"{s} {f:+.1f}M" for s, f in flows[-2:])

    body = f"Приток: {top}."
    if outflow:
        body += f" Отток: {bot}."

    total_net = sum(f for _, f in flows)

    if len(inflow) == 0:
        tone = "negative"
        headline = "Деньги уходят изо всех секторов"
        action = "Это широкий risk-off. Не время искать, куда войти."
    elif total_net > 0 and len(inflow) >= len(outflow):
        tone = "positive"
        headline = f"Капитал заходит, лидер — {flows[0][0]}"
        action = f"Ищи входы прежде всего в {flows[0][0]}, там сейчас деньги."
    else:
        tone = "caution"
        headline = f"Ротация: из {flows[-1][0]} в {flows[0][0]}"
        action = ("Общий приток слабый, деньги перекладываются между секторами. "
                  "Это не рост рынка, а перестановка.")

    return {"headline": headline, "body": body, "action": action, "tone": tone}


# ─────────────────────────────────────────────────────────────
# СБОРКА
# ─────────────────────────────────────────────────────────────

def main():
    print("=== Module Summaries v1.0 ===\n")

    macro = load("macro_narratives.json")
    total = load("total_phase.json")
    regime = load("market_regime.json")
    decisions = load("decisions.json")
    compass = load("asset_compass.json")
    vp = load("volume_profile.json")
    sector = load("dune_sector_netflow.json")

    mods = {
        "macro": summarize_macro(macro),
        "market": summarize_market(total),
        "regime": summarize_regime(regime),
        "universe": summarize_universe(decisions, compass, vp),
        "rotation": summarize_rotation(sector),
    }
    mods = {k: v for k, v in mods.items() if v}

    for name, s in mods.items():
        print(f"  ═══ {name.upper()} · {s['tone']} ═══")
        print(f"  {s['headline']}")
        print(f"    {s['body']}")
        print(f"    → {s['action']}")
        print()

    out = {
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "note": ("Выводы строятся из тех же кэшей, что показывают модули. "
                 "Это интерпретация, а не отдельный источник данных."),
        "modules": mods,
    }

    os.makedirs(CACHE, exist_ok=True)
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)

    print(f"  Собрано выводов: {len(mods)}")
    print(f"\n✓ {OUT_FILE}")


if __name__ == "__main__":
    main()
