#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
position_advisor.py · v1.1 · 21.08.2026
STRK ENGINE · советы по ОТКРЫТОЙ позиции

ЗАЧЕМ
-----
Модалка актива говорит «не заходи, выйди если стоишь». Это совет для
того, у кого позиции нет или есть лонг. Для шорта он прямо вреден.

21.08 это было видно наглядно: открыт шорт AAVE от $111, все сигналы
медвежьи (компас −76, фаза WEAKENING, отток ускоряется). Позиция
идёт правильно, а модалка советовала выйти.

Причина проста: движок ничего не знает о позициях. Он отвечает на
вопрос «стоит ли покупать», а не «что делать с тем, что уже открыто».
Это разные вопросы.

ЧТО ДЕЛАЕТ
----------
Читает открытые позиции из журнала и сигналы по тем же токенам,
и для каждой позиции даёт совет С УЧЁТОМ НАПРАВЛЕНИЯ:

  сигнал медвежий + шорт  → позиция по сигналу, держать, цель такая-то
  сигнал медвежий + лонг  → позиция против сигнала, сокращать
  сигнал бычий + лонг     → держать
  сигнал бычий + шорт     → против сигнала, риск

Плюс считает то, чего не даёт ни один другой модуль:
  сколько прошло от входа
  где ближайшая цель по направлению позиции
  где уровень, на котором позиция сломана
  соотношение оставшегося хода к риску

СРОЧНОСТЬ
---------
  critical  сигнал развернулся против позиции — решать сегодня
  warning   позиция подошла к уровню или сигнал слабеет
  ok        всё идёт по плану, ничего не делать
  info      данных мало для суждения

ЗАПУСК
------
  python3 scripts/position_advisor.py

ВЫХОД
-----
  data/cache/position_advice.json
"""

import os
import json
from datetime import datetime, timezone
from collections import defaultdict

CACHE = "data/cache"
HISTORY = "data/history"
TRADES_FILE = os.path.join(HISTORY, "trades.jsonl")
OUT_FILE = os.path.join(CACHE, "position_advice.json")

OPEN_ACTIONS = {"LONG", "SHORT", "BUY"}
CLOSE_ACTIONS = {"CLOSE", "SELL"}
ADD_ACTIONS = {"ADD"}

# Ниже этого балла компаса считаем сигнал медвежьим, выше — бычьим.
# Между — нейтраль, и тогда позицию ведём по своему плану.
BEARISH_BELOW = -20
BULLISH_ABOVE = 20

# Позиция считается «у цели», когда до неё меньше этого расстояния
NEAR_TARGET_PCT = 1.5


def load(name, default=None):
    try:
        with open(os.path.join(CACHE, name), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default if default is not None else {}


def read_trades():
    out = []
    try:
        with open(TRADES_FILE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        out.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
    except FileNotFoundError:
        pass
    return out


def parse_ts(s):
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except Exception:
        return None


def build_open_positions(trades):
    """Собирает открытые позиции из событий журнала."""
    trades = sorted(trades, key=lambda t: t.get("ts") or "")
    open_pos = {}

    for t in trades:
        token = (t.get("token") or "").upper()
        action = (t.get("action") or "").upper()
        price = t.get("price")
        if not token or not action or not price:
            continue

        if action in OPEN_ACTIONS or action in ADD_ACTIONS:
            if token in open_pos:
                p = open_pos[token]
                old = p.get("size_usd") or 0
                new = t.get("size_usd") or 0
                tot = old + new
                if tot > 0:
                    p["entry_price"] = (p["entry_price"] * old + price * new) / tot
                    p["size_usd"] = tot
            elif action in OPEN_ACTIONS:
                open_pos[token] = {
                    "token": token,
                    "side": action,
                    "entry_price": price,
                    "entry_ts": t.get("ts"),
                    "size_usd": t.get("size_usd"),
                    "note": t.get("note", ""),
                }
        elif action in CLOSE_ACTIONS:
            open_pos.pop(token, None)

    return list(open_pos.values())


def signal_direction(compass_score, decision_action):
    """Куда смотрят сигналы: вниз, вверх или никуда."""
    if compass_score is None:
        if decision_action == "ВХОД ЧАСТЬЮ":
            return "UP"
        if decision_action == "НЕ ВХОДИТЬ":
            return "DOWN"
        return "FLAT"
    if compass_score <= BEARISH_BELOW:
        return "DOWN"
    if compass_score >= BULLISH_ABOVE:
        return "UP"
    return "FLAT"


def fmt_price(p):
    if p is None:
        return "—"
    if p < 0.01:
        return f"{p:.6f}"
    if p < 1:
        return f"{p:.4f}"
    return f"{p:.2f}"


def build_plain(out, sig, dec, pa, vp, comp, hl, targets):
    """
    Четыре вопроса обычными словами.

    Зачем отдельный блок. Уровни сами по себе не говорят, что делать —
    они говорят, где цена раньше торговалась. Действие появляется, когда
    к уровням добавлены три вещи: что ближе, жива ли причина входа, и
    что именно тебя опровергнет. Раньше система отвечала на вопрос
    «стоит ли покупать» — а человек с открытой позицией спрашивает
    другое.

    Здесь нет ни одного совета вида «закрывай» или «держи»: только
    числа и то, что из них следует. Решение принимает человек.
    """
    is_short = out["side"] == "SHORT"
    side_ru = "шорт" if is_short else "лонг"
    price = out.get("current_price")
    blocks = []

    # ── 1. Что у тебя ────────────────────────────────────────
    lines = [f"{side_ru.capitalize()} от ${fmt_price(out['entry_price'])}, "
             f"сейчас ${fmt_price(price)}."]
    if out.get("size_usd"):
        lines.append(f"Объём ${out['size_usd']:,.0f}".replace(",", " ") + ".")
    if out.get("hours_open") is not None:
        h = out["hours_open"]
        lines.append(f"В позиции {int(h)} ч." if h < 48
                     else f"В позиции {h/24:.1f} дн.")
    if out.get("pnl_pct") is not None:
        znak = "плюсе" if out["pnl_pct"] >= 0 else "минусе"
        s = f"Сейчас в {znak} на {abs(out['pnl_pct']):.2f}%"
        if out.get("pnl_usd") is not None:
            s += f" (${abs(out['pnl_usd']):,.0f})".replace(",", " ")
        lines.append(s + ".")
    blocks.append({"q": "Что у тебя", "tone": "muted", "lines": lines})

    # ── 2. Что ближе — помеха или выгода ─────────────────────
    up = targets.get("nearest_up") or []
    down = targets.get("nearest_down") or []
    helps, hurts = (down, up) if is_short else (up, down)

    lines = []
    tone = "muted"
    d_hurt = d_help = None
    if hurts:
        h0 = hurts[0]
        d_hurt = abs(h0["distance_pct"])
        lines.append(f"Против тебя ближайшее — ${fmt_price(h0['price'])} "
                     f"({d_hurt:.1f}%), {h0['label']}.")
    if helps:
        g0 = helps[0]
        d_help = abs(g0["distance_pct"])
        lines.append(f"За тебя ближайшее — ${fmt_price(g0['price'])} "
                     f"({d_help:.1f}%), {g0['label']}.")

    if d_hurt is not None and d_help is not None:
        if d_help > d_hurt:
            tone = "red"
            lines.append(f"Помеха ближе выгоды: {d_hurt:.1f}% против "
                         f"{d_help:.1f}%. Идти позиции дальше, чем "
                         f"до первого сопротивления.")
        else:
            tone = "green"
            lines.append(f"Выгода ближе помехи: {d_help:.1f}% против "
                         f"{d_hurt:.1f}%.")
    if len(hurts) > 1:
        h1 = hurts[1]
        lines.append(f"Следующее против тебя — ${fmt_price(h1['price'])} "
                     f"({abs(h1['distance_pct']):.1f}%), {h1['label']}.")
    if not lines:
        lines = ["Уровней по этому активу нет — расстояния посчитать не из чего."]
    else:
        lines.append("Это не стоп: здесь только расстояния до цен, где "
                     "раньше проходил объём. Стоп ставишь ты.")
    blocks.append({"q": "Что ближе — помеха или выгода",
                   "tone": tone, "lines": lines})

    # ── 3. Жива ли причина входа ─────────────────────────────
    za, protiv = [], []

    shape = ((pa.get("flow") or {}).get("shape")) or {}
    if shape.get("text_ru"):
        code = shape.get("code") or ""
        bearish_shape = code.startswith("DISTRIBUTION") and not shape.get("short_flipped")
        bullish_shape = code.startswith("ACCUMULATION") and not shape.get("short_flipped")
        txt = f"форма потока: {shape['text_ru']}"
        if shape.get("short_flipped"):
            (protiv if is_short and code.startswith("DISTRIBUTION")
             else protiv if not is_short and code.startswith("ACCUMULATION")
             else za).append(txt)
        elif bearish_shape:
            (za if is_short else protiv).append(txt)
        elif bullish_shape:
            (protiv if is_short else za).append(txt)

    reg = (pa.get("regime") or {})
    rc = reg.get("code") or ""
    if reg.get("text_ru"):
        txt = f"движение денег: {reg['text_ru']}"
        if rc in ("ACCEL_DOWN", "FLIPPING_DOWN", "STEADY_DOWN"):
            (za if is_short else protiv).append(txt)
        elif rc in ("ACCEL_UP", "FLIPPING_UP", "STEADY_UP"):
            (protiv if is_short else za).append(txt)

    vpos = vp.get("position") or {}
    kind = vpos.get("above_kind")
    if vpos.get("code") == "ABOVE_VALUE" and kind:
        if kind == "MARKUP":
            txt = (f"объём подтверждает выход цены вверх "
                   f"(×{vpos.get('vol_ratio_recent')} к среднему по рынку)")
            (protiv if is_short else za).append(txt)
        else:
            txt = "цена ушла вверх, объём этого не подтверждает"
            (za if is_short else protiv).append(txt)
    elif vpos.get("code") == "BELOW_VALUE":
        (za if is_short else protiv).append("цена ниже зоны объёма")

    score = out.get("compass_score")
    if score is not None:
        txt = f"компас {score:+.0f}"
        if score <= BEARISH_BELOW:
            (za if is_short else protiv).append(txt)
        elif score >= BULLISH_ABOVE:
            (protiv if is_short else za).append(txt)

    lines = []
    if za:
        lines.append("За твою сторону: " + "; ".join(za) + ".")
    if protiv:
        lines.append("Против: " + "; ".join(protiv) + ".")
    if not za and not protiv:
        lines.append("Ни один слой не высказался определённо — "
                     "картина нейтральная.")
        status, tone = "neutral", "muted"
    elif za and not protiv:
        lines.append("Причина, по которой позиция открыта, подтверждается.")
        status, tone = "alive", "green"
    elif protiv and not za:
        lines.append("Причина, по которой позиция открыта, сейчас "
                     "не подтверждается ни одним слоем.")
        status, tone = "broken", "red"
    else:
        lines.append(f"Слои спорят: {len(za)} за, {len(protiv)} против. "
                     f"Это не то же самое, что уверенный сигнал.")
        status, tone = "mixed", "yellow"
    blocks.append({"q": "Жива ли причина входа", "tone": tone,
                   "lines": lines, "status": status})

    # ── 4. Что тебя опровергнет ──────────────────────────────
    lines = []
    if hurts:
        h0 = hurts[0]
        napr = "выше" if is_short else "ниже"
        lines.append(f"Ближайшая проверка — ${fmt_price(h0['price'])}. "
                     f"Дневное закрытие {napr} этой цены значит, что "
                     f"ближайшая защита пройдена.")
    if len(hurts) > 1:
        h1 = hurts[1]
        lines.append(f"Структурная — ${fmt_price(h1['price'])} ({h1['label']}). "
                     f"За ней уровней рядом нет.")
    lines.append("Если уровень не назван заранее, решение принимается "
                 "заново при каждом взгляде на экран — и почти всегда "
                 "в пользу «подожду ещё немного».")
    blocks.append({"q": "Что тебя опровергнет", "tone": "muted", "lines": lines})

    return blocks


def advise(pos, sig):
    """
    Главная функция. Совет строится от НАПРАВЛЕНИЯ позиции,
    а не от абстрактного «покупать или нет».
    """
    token = pos["token"]
    side = pos["side"]
    entry = pos["entry_price"]

    vp = (sig["vp"].get("tokens") or {}).get(token) or {}
    comp = (sig["compass"].get("tokens") or {}).get(token) or {}
    dec = (sig["decisions"].get("decisions") or {}).get(token) or {}
    pa = (sig["phase"].get("tokens") or {}).get(token) or {}
    hl = (sig["hl"].get("tokens") or {}).get(token) or {}

    price = vp.get("current_price")
    if not price:
        pref = (sig["prices"].get("tokens") or {}).get(token) or {}
        price = pref.get("price_now")

    out = {
        "token": token,
        "side": side,
        "entry_price": entry,
        "current_price": price,
        "size_usd": pos.get("size_usd"),
        "note": pos.get("note"),
    }

    # Сколько держим
    t0 = parse_ts(pos.get("entry_ts"))
    if t0:
        hours = (datetime.now(timezone.utc) - t0).total_seconds() / 3600
        out["hours_open"] = round(hours, 1)
        out["days_open"] = round(hours / 24, 1)

    if not price:
        out["urgency"] = "info"
        out["headline"] = "Нет свежей цены"
        out["body"] = "Не могу оценить позицию без цены."
        return out

    # Результат с учётом направления
    is_short = side == "SHORT"
    pnl_pct = ((entry / price - 1) if is_short else (price / entry - 1)) * 100
    out["pnl_pct"] = round(pnl_pct, 2)
    if pos.get("size_usd"):
        out["pnl_usd"] = round(pos["size_usd"] * pnl_pct / 100, 2)

    score = comp.get("score")
    action = dec.get("action")
    direction = signal_direction(score, action)
    out["compass_score"] = score
    out["signal_direction"] = direction
    out["decision_action"] = action

    pos_dir = "DOWN" if is_short else "UP"
    aligned = direction == pos_dir
    against = direction != "FLAT" and direction != pos_dir
    out["aligned"] = aligned

    # Цели по направлению позиции и уровень слома
    targets = vp.get("targets") or {}
    if is_short:
        to_target = targets.get("nearest_down") or []
        to_risk = targets.get("nearest_up") or []
    else:
        to_target = targets.get("nearest_up") or []
        to_risk = targets.get("nearest_down") or []

    if to_target:
        t = to_target[0]
        out["next_target"] = {
            "price": t["price"],
            "distance_pct": abs(t["distance_pct"]),
            "label": t["label"],
        }
    if to_risk:
        r = to_risk[0]
        out["invalidation"] = {
            "price": r["price"],
            "distance_pct": abs(r["distance_pct"]),
            "label": r["label"],
        }

    # Сколько хода осталось против того, чем рискуем
    if out.get("next_target") and out.get("invalidation"):
        reward = out["next_target"]["distance_pct"]
        risk = out["invalidation"]["distance_pct"]
        if risk > 0:
            out["remaining_rr"] = round(reward / risk, 2)

    near_target = (out.get("next_target") or {}).get("distance_pct", 99) < NEAR_TARGET_PCT

    # ── СОВЕТ ──
    side_ru = "шорт" if is_short else "лонг"
    parts = []

    if aligned:
        # Сигналы за позицию
        if near_target:
            out["urgency"] = "warning"
            out["headline"] = f"Держи {side_ru}, но цель рядом"
            t = out["next_target"]
            parts.append(f"Сигналы за твою позицию, но до ближайшего уровня "
                         f"${t['price']:.4f} осталось {t['distance_pct']:.1f}%.")
            parts.append("Разумно снять часть у уровня и перевести стоп в безубыток.")
            out["action_ru"] = "Снять часть у цели"
        else:
            out["urgency"] = "ok"
            out["headline"] = f"Держи {side_ru} — сигналы за тебя"
            if score is not None:
                parts.append(f"Компас {score:+.0f}, направление совпадает с позицией.")
            phase = dec.get("stage")
            if phase:
                parts.append(f"Фаза {phase}.")
            reg = (pa.get("regime") or {}).get("text_ru")
            if reg:
                parts.append(reg.capitalize() + ".")
            out["action_ru"] = "Ничего не делать"

    elif against:
        out["urgency"] = "critical"
        out["headline"] = f"Сигналы развернулись против твоего {side_ru}а"
        if score is not None:
            parts.append(f"Компас {score:+.0f} — это в другую сторону от позиции.")
        if action:
            parts.append(f"Движок по активу: {action}.")
        parts.append("Позиция против потока. Решать сегодня: сокращать или "
                     "закрывать целиком.")
        out["action_ru"] = "Сокращать или закрывать"

    else:
        out["urgency"] = "info"
        out["headline"] = f"Сигналы нейтральны, {side_ru} ведём по плану"
        if score is not None:
            parts.append(f"Компас {score:+.0f} — без перевеса.")
        parts.append("Система не даёт повода ни держать, ни закрывать. "
                     "Работает твой первоначальный план.")
        out["action_ru"] = "По своему плану"

    # Перегрев деривативов — отдельная строка, она про срочность
    prem = hl.get("premium_pct")
    if prem is not None:
        if is_short and prem > 0.15:
            parts.append(f"Премия HL +{prem:.3f}%: толпа в лонгах, "
                         f"их вынос сыграет за твой шорт.")
        elif is_short and prem < -0.15:
            parts.append(f"Премия HL {prem:.3f}%: шорты переплачивают, "
                         f"возможен вынос вверх против тебя.")
        elif not is_short and prem > 0.15:
            parts.append(f"Премия HL +{prem:.3f}%: лонги перегреты, "
                         f"риск каскада вниз.")

    out["body"] = " ".join(parts)

    # Человеческий разбор из четырёх вопросов. Считается здесь, в Python,
    # и кладётся готовым — браузеру остаётся только вывести строки.
    try:
        out["plain"] = build_plain(out, sig, dec, pa, vp, comp, hl, targets)
    except Exception as e:
        out["plain"] = [{"q": "Разбор позиции", "tone": "muted",
                         "lines": [f"не удалось собрать: {e}"]}]

    # ── Сверка заголовка с разбором ──────────────────────────
    # Заголовок строился только по компасу и вердикту движка и мог
    # сказать «сигналы за тебя · ok» там, где разбор ниже показывает
    # спор слоёв и помеху ближе выгоды. Два противоположных вывода
    # в одной карточке — это ровно та болезнь, которую мы лечили
    # на дашборде. Заголовок понижается до того, что видно в разборе.
    _by_q = {b.get("q"): b for b in (out.get("plain") or [])}
    _thesis = _by_q.get("Жива ли причина входа") or {}
    _dist = _by_q.get("Что ближе — помеха или выгода") or {}
    _rank = {"ok": 0, "info": 1, "warning": 2, "critical": 3}

    def _raise_to(level, headline=None, why=None):
        if _rank.get(level, 0) > _rank.get(out.get("urgency", "ok"), 0):
            out["urgency"] = level
            if headline:
                out["headline"] = headline
            if why:
                out["body"] = (out.get("body", "") + " " + why).strip()

    if _thesis.get("status") == "broken":
        _raise_to("critical", f"Причина входа в {side_ru} больше не подтверждается",
                  "Ни один слой сейчас не говорит в сторону позиции.")
    elif _thesis.get("status") == "mixed":
        _raise_to("warning", f"Слои спорят — {side_ru} держится не на всех сигналах",
                  "Часть слоёв развернулась против позиции.")

    if _dist.get("tone") == "red":
        _raise_to("warning", None,
                  "До ближайшей помехи ближе, чем до ближайшей выгоды.")

    # Соотношение хода к риску считалось как «до цели / до ближайшего
    # уровня против». При цене вплотную к уровню знаменатель крошечный,
    # и число вылетало в 15-20 — выглядело отличным там, где ситуация
    # ровно обратная. Оставляю поле для совместимости, но помечаю.
    if out.get("remaining_rr") is not None:
        out["remaining_rr_note"] = ("расстояние до уровней, а не до стопа; "
                                    "большое число рядом с уровнем обманчиво")
    return out


def main():
    print("=== Position Advisor v1.1 ===\n")

    trades = read_trades()
    positions = build_open_positions(trades)

    if not positions:
        out = {
            "computed_at": datetime.now(timezone.utc).isoformat(),
            "open_positions": 0,
            "note": "открытых позиций нет",
            "by_token": {},
        }
        os.makedirs(CACHE, exist_ok=True)
        with open(OUT_FILE, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=2, ensure_ascii=False)
        print("  Открытых позиций нет")
        print(f"\n✓ {OUT_FILE}")
        return

    sig = {
        "vp": load("volume_profile.json"),
        "compass": load("asset_compass.json"),
        "decisions": load("decisions.json"),
        "phase": load("phase_analysis.json"),
        "hl": load("hl_perps.json"),
        "prices": load("price_reference.json"),
    }

    by_token = {}
    urgent = []
    for p in positions:
        a = advise(p, sig)
        by_token[a["token"]] = a
        if a.get("urgency") == "critical":
            urgent.append(a["token"])

        pnl = a.get("pnl_pct")
        pnl_s = f"{pnl:+.2f}%" if pnl is not None else "—"
        cur = a.get("current_price")
        cur_s = f"${cur:.4f}" if cur else "цены нет"
        print(f"  {a['token']:8} {a['side']:6} вход ${a['entry_price']:.4f} · "
              f"сейчас {cur_s} · {pnl_s}")
        print(f"           [{a['urgency']}] {a['headline']}")
        print(f"           {a['body'][:150]}")
        if a.get("next_target"):
            t = a["next_target"]
            print(f"           цель ${t['price']:.4f} ({t['distance_pct']:.1f}%) · {t['label']}")
        if a.get("invalidation"):
            v = a["invalidation"]
            print(f"           слом ${v['price']:.4f} ({v['distance_pct']:.1f}%) · {v['label']}")
        print()

    out = {
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "open_positions": len(positions),
        "urgent_tokens": urgent,
        "note": ("Советы даются С УЧЁТОМ направления позиции. Движок отвечает "
                 "на вопрос «стоит ли покупать», здесь — «что делать с тем, "
                 "что уже открыто». Это разные вопросы."),
        "by_token": by_token,
    }

    os.makedirs(CACHE, exist_ok=True)
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)

    print(f"  Позиций: {len(positions)}")
    if urgent:
        print(f"  Требуют решения сегодня: {', '.join(urgent)}")
    print(f"\n✓ {OUT_FILE}")


if __name__ == "__main__":
    main()
