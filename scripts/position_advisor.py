#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
position_advisor.py · v1.0 · 21.08.2026
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
    return out


def main():
    print("=== Position Advisor v1.0 ===\n")

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
