#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
trade_journal.py · v1.1 · 21.08.2026
STRK ENGINE · журнал сделок и сверка с движком

ЗАЧЕМ
-----
Движок пишет прогнозы и проверяет их по цене. Но он не знает, что делала
ты. Если он прав в 70% случаев, а ты входишь ровно в те 30%, где он
ошибался — результат будет плохим, и система об этом не узнает.

Здесь закрывается последний разрыв: сопоставление «что советовал движок»
и «что я реально сделала».

ВАЖНО · ЭТО ТОЛЬКО УЧЁТ
-----------------------
Скрипт ничего не исполняет и не может. Он читает то, что ты записала
руками, и считает. Никаких ключей, никаких ордеров, никакого доступа
к бирже.

ФОРМАТ ЗАПИСИ
-------------
data/history/trades.jsonl · одна строка на событие:

  {"ts": "2026-08-21T10:30:00Z", "token": "LINK", "action": "LONG",
   "price": 11.08, "size_usd": 500, "note": "по сигналу движка"}

  {"ts": "2026-08-28T14:00:00Z", "token": "LINK", "action": "CLOSE",
   "price": 12.30, "note": "первая цель"}

Действия:
  LONG   открыть длинную позицию (перпы)
  SHORT  открыть короткую позицию (перпы)
  BUY    купить спот
  SELL   продать спот (или закрыть часть)
  CLOSE  закрыть позицию целиком
  ADD    добавить к существующей позиции

ЧТО СЧИТАЕТ
-----------
1. Открытые позиции · что сейчас в работе, по какой цене, какой PnL
2. Закрытые сделки · итоговый PnL, длительность, попадание в цель
3. Сверка с движком · четыре категории:

   FOLLOWED       вошла когда движок говорил ВХОД
   IGNORED        движок говорил ВХОД, ты не вошла
   AGAINST        вошла в ЛОНГ когда движок говорил НЕ ВХОДИТЬ или ЖДАТЬ
   OWN_CALL       шорт — движок коротких сигналов не выдаёт вообще,
                  поэтому такие сделки меряются отдельно, а не
                  засчитываются как согласие с ним
   NO_SIGNAL      вошла когда у движка не было данных

   Третья категория самая ценная. Систематическое расхождение значит
   либо правило плохое, либо ты ему не доверяешь. Оба случая стоит
   разобрать, а не замалчивать.

4. Упущенное · что было бы, если бы следовала всем сигналам ВХОД

ЗАПУСК
------
  python3 scripts/trade_journal.py                 полный отчёт
  python3 scripts/trade_journal.py --add           добавить сделку из аргументов
  python3 scripts/trade_journal.py --open          только открытые позиции

ДОБАВИТЬ СДЕЛКУ ИЗ КОМАНДНОЙ СТРОКИ
-----------------------------------
  python3 scripts/trade_journal.py --add \\
      --token LINK --action LONG --price 11.08 --size 500 \\
      --note "по сигналу движка"

ВЫХОД
-----
  data/cache/trade_journal.json
  data/history/trades.jsonl  (append при --add)
"""

import os
import sys
import json
import glob
import argparse
import statistics
from datetime import datetime, timezone, timedelta
from collections import defaultdict

CACHE = "data/cache"
HISTORY = "data/history"
TRADES_FILE = os.path.join(HISTORY, "trades.jsonl")
DECISION_LOG = os.path.join(HISTORY, "decision_log.jsonl")
OUT_FILE = os.path.join(CACHE, "trade_journal.json")

# Действия, открывающие позицию
OPEN_ACTIONS = {"LONG", "SHORT", "BUY"}
# Действия, закрывающие или уменьшающие
CLOSE_ACTIONS = {"CLOSE", "SELL"}
# Добавление к существующей
ADD_ACTIONS = {"ADD"}

ALL_ACTIONS = OPEN_ACTIONS | CLOSE_ACTIONS | ADD_ACTIONS

# Насколько близко по времени сделка должна быть к решению, чтобы
# считать что ты действовала по нему. Решения пересчитываются каждые
# 6 часов, берём сутки с запасом.
DECISION_MATCH_WINDOW_HOURS = 24

# Sanity-фильтр для расчёта упущенного. Движение больше этого за месяц —
# битая цена в источнике (тот же баг единиц, что давал ARB +1 717 040%).
# Без фильтра BONK показывал "было бы +1347%" и обесценивал весь раздел.
MAX_SANE_PNL_PCT = 300.0


def read_jsonl(path):
    out = []
    try:
        with open(path, encoding="utf-8") as f:
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


def load_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def parse_ts(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except Exception:
        return None


def current_price(token):
    """Свежая цена из доступных кэшей."""
    prices = (load_json(os.path.join(CACHE, "hive_prices.json"), {}) or {}).get("prices", {})
    row = prices.get(token) or {}
    if row.get("price_usd"):
        return float(row["price_usd"])

    vp = (load_json(os.path.join(CACHE, "volume_profile.json"), {}) or {}).get("tokens", {})
    r = vp.get(token) or {}
    if r.get("current_price"):
        return float(r["current_price"])

    scan = load_json(os.path.join(CACHE, "token_scan", f"{token}.json"), {}) or {}
    if scan.get("price_now"):
        return float(scan["price_now"])
    return None


# ─────────────────────────────────────────────────────────────
# ДОБАВЛЕНИЕ СДЕЛКИ
# ─────────────────────────────────────────────────────────────

def add_trade(token, action, price, size_usd=None, note=""):
    token = token.upper().strip()
    action = action.upper().strip()

    if action not in ALL_ACTIONS:
        print(f"✗ Неизвестное действие: {action}")
        print(f"  Доступные: {', '.join(sorted(ALL_ACTIONS))}")
        return False

    try:
        price = float(price)
    except (TypeError, ValueError):
        print(f"✗ Некорректная цена: {price}")
        return False

    if price <= 0:
        print("✗ Цена должна быть больше нуля")
        return False

    rec = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "token": token,
        "action": action,
        "price": price,
        "note": note or "",
    }
    if size_usd is not None:
        try:
            rec["size_usd"] = float(size_usd)
        except (TypeError, ValueError):
            pass

    os.makedirs(HISTORY, exist_ok=True)
    with open(TRADES_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    size_s = f" · ${rec.get('size_usd', 0):,.0f}" if rec.get("size_usd") else ""
    print(f"✓ Записано: {token} {action} @ ${price}{size_s}")
    if note:
        print(f"  Заметка: {note}")
    return True


# ─────────────────────────────────────────────────────────────
# СБОРКА ПОЗИЦИЙ ИЗ СОБЫТИЙ
# ─────────────────────────────────────────────────────────────

def build_positions(trades):
    """
    Проходит события по порядку и собирает позиции.
    Одна позиция = от открытия до закрытия по одному токену.
    """
    trades = sorted(trades, key=lambda t: t.get("ts") or "")
    open_pos = {}       # token -> позиция в работе
    closed = []

    for t in trades:
        token = (t.get("token") or "").upper()
        action = (t.get("action") or "").upper()
        price = t.get("price")
        if not token or not action or not price:
            continue

        if action in OPEN_ACTIONS:
            if token in open_pos:
                # Уже есть позиция — трактуем как добавление
                p = open_pos[token]
                old_size = p.get("size_usd") or 0
                new_size = t.get("size_usd") or 0
                total = old_size + new_size
                if total > 0:
                    # средняя цена входа по объёму
                    p["entry_price"] = (p["entry_price"] * old_size + price * new_size) / total
                    p["size_usd"] = total
                p["events"].append(t)
            else:
                open_pos[token] = {
                    "token": token,
                    "side": action,
                    "entry_price": price,
                    "entry_ts": t.get("ts"),
                    "size_usd": t.get("size_usd"),
                    "note": t.get("note", ""),
                    "events": [t],
                }

        elif action in ADD_ACTIONS:
            if token in open_pos:
                p = open_pos[token]
                old_size = p.get("size_usd") or 0
                new_size = t.get("size_usd") or 0
                total = old_size + new_size
                if total > 0:
                    p["entry_price"] = (p["entry_price"] * old_size + price * new_size) / total
                    p["size_usd"] = total
                p["events"].append(t)

        elif action in CLOSE_ACTIONS:
            if token in open_pos:
                p = open_pos.pop(token)
                p["exit_price"] = price
                p["exit_ts"] = t.get("ts")
                p["exit_note"] = t.get("note", "")
                p["events"].append(t)

                # PnL с учётом направления
                entry = p["entry_price"]
                if p["side"] == "SHORT":
                    pnl_pct = (entry / price - 1) * 100
                else:
                    pnl_pct = (price / entry - 1) * 100
                p["pnl_pct"] = round(pnl_pct, 2)
                if p.get("size_usd"):
                    p["pnl_usd"] = round(p["size_usd"] * pnl_pct / 100, 2)

                # длительность
                t0, t1 = parse_ts(p.get("entry_ts")), parse_ts(p.get("exit_ts"))
                if t0 and t1:
                    p["duration_days"] = round((t1 - t0).total_seconds() / 86400, 1)

                closed.append(p)

    # Незакрытые позиции — считаем плавающий результат
    for token, p in open_pos.items():
        cur = current_price(token)
        if cur:
            entry = p["entry_price"]
            if p["side"] == "SHORT":
                pnl_pct = (entry / cur - 1) * 100
            else:
                pnl_pct = (cur / entry - 1) * 100
            p["current_price"] = cur
            p["unrealized_pnl_pct"] = round(pnl_pct, 2)
            if p.get("size_usd"):
                p["unrealized_pnl_usd"] = round(p["size_usd"] * pnl_pct / 100, 2)
        t0 = parse_ts(p.get("entry_ts"))
        if t0:
            p["days_open"] = round((datetime.now(timezone.utc) - t0).total_seconds() / 86400, 1)

    return list(open_pos.values()), closed


# ─────────────────────────────────────────────────────────────
# СВЕРКА С ДВИЖКОМ
# ─────────────────────────────────────────────────────────────

def decision_at(decisions, token, ts):
    """
    Какое решение движок выдавал по токену ближе всего ДО момента ts.
    Ищем именно до, а не после — иначе получим заглядывание в будущее.
    """
    t = parse_ts(ts)
    if not t:
        return None

    best, best_gap = None, None
    for d in decisions:
        if (d.get("token") or "").upper() != token:
            continue
        dt = parse_ts(d.get("issued_at"))
        if not dt or dt > t:
            continue
        gap = (t - dt).total_seconds() / 3600
        if gap > DECISION_MATCH_WINDOW_HOURS:
            continue
        if best_gap is None or gap < best_gap:
            best, best_gap = d, gap

    if best:
        best = dict(best)
        best["_gap_hours"] = round(best_gap, 1)
    return best


def classify_trade(pos, decisions):
    """Одна из четырёх категорий соответствия."""
    d = decision_at(decisions, pos["token"], pos.get("entry_ts"))
    if not d:
        return {"category": "NO_SIGNAL",
                "text_ru": "движок молчал — не было свежего решения по этому токену",
                "decision": None}

    action = d.get("action")
    side = pos["side"]

    # Покупка/лонг при сигнале входа
    if action == "ВХОД ЧАСТЬЮ" and side in ("LONG", "BUY"):
        return {"category": "FOLLOWED",
                "text_ru": f"вошла по сигналу (движок советовал {d.get('size_pct')}%)",
                "decision": d}

    # ФИКС 21.08.2026 · раньше здесь стояла категория FOLLOWED с текстом
    # «шорт при сигнале НЕ ВХОДИТЬ — согласуется с движком».
    #
    # Это неверно. «НЕ ВХОДИТЬ» значит НЕ ПОКУПАЙ. Это не сигнал шортить.
    # Отсутствие повода купить и наличие повода продать — разные вещи,
    # и путать их — ровно та же ошибка, за которую мы переписывали
    # confluence_gate: отсутствие улики засчитывалось как улика.
    #
    # Практическая цена ошибки: шорт AAVE $111 → $117.76 (−5.74%) попал
    # в статистику как «следовала сигналу». Накопится десяток таких —
    # и сверка с движком станет ложно оптимистичной именно там, где
    # она должна предупреждать.
    #
    # Движок сейчас вообще не выдаёт коротких сигналов: у него три
    # исхода — ВХОД ЧАСТЬЮ, ЖДАТЬ, НЕ ВХОДИТЬ. Пока их нет, шорт всегда
    # решение человека, а не движка, и меряться должен отдельно.
    if action in ("НЕ ВХОДИТЬ", "ЖДАТЬ") and side == "SHORT":
        return {"category": "OWN_CALL",
                "text_ru": (f"шорт при сигнале {action} — движок коротких "
                            f"сигналов не даёт, это твоё решение"),
                "decision": d}

    # Действие против сигнала
    if action in ("НЕ ВХОДИТЬ", "ЖДАТЬ") and side in ("LONG", "BUY"):
        return {"category": "AGAINST",
                "text_ru": f"вошла в лонг, хотя движок говорил {action}",
                "decision": d}

    if action == "ВХОД ЧАСТЬЮ" and side == "SHORT":
        return {"category": "AGAINST",
                "text_ru": "шорт при бычьем сигнале движка",
                "decision": d}

    if action == "ДАННЫЕ ПОДОЗРИТЕЛЬНЫ":
        return {"category": "AGAINST",
                "text_ru": "вошла на данных, которые движок забраковал",
                "decision": d}

    return {"category": "NO_SIGNAL",
            "text_ru": f"движок выдавал {action} — прямого соответствия нет",
            "decision": d}


def find_ignored(decisions, trades, since_days=30):
    """
    Сигналы ВХОД, по которым сделки не было. Считаем что было бы.
    Это обратная сторона сверки: не только «что я сделала не так»,
    но и «что я пропустила».
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=since_days)
    traded_tokens = set()
    for t in trades:
        ts = parse_ts(t.get("ts"))
        if ts and ts >= cutoff:
            traded_tokens.add((t.get("token") or "").upper())

    ignored = []
    seen = set()
    for d in decisions:
        if d.get("action") != "ВХОД ЧАСТЬЮ":
            continue
        dt = parse_ts(d.get("issued_at"))
        if not dt or dt < cutoff:
            continue
        token = (d.get("token") or "").upper()
        if token in traded_tokens:
            continue
        # по одному сигналу на токен — самый свежий
        if token in seen:
            continue
        seen.add(token)

        entry = d.get("price_at_decision")
        cur = current_price(token)
        row = {
            "token": token,
            "issued_at": d.get("issued_at"),
            "size_pct": d.get("size_pct"),
            "price_at_signal": entry,
            "current_price": cur,
        }
        if entry and cur:
            pnl = (cur / entry - 1) * 100
            if abs(pnl) > MAX_SANE_PNL_PCT:
                row["data_suspicious"] = True
                row["would_be_pnl_pct"] = None
                row["note"] = f"движение {pnl:.0f}% — данные подозрительны, не учитываем"
            else:
                row["would_be_pnl_pct"] = round(pnl, 2)
        ignored.append(row)

    ignored.sort(key=lambda r: (r.get("would_be_pnl_pct") is not None,
                               r.get("would_be_pnl_pct") or 0), reverse=True)
    return ignored


# ─────────────────────────────────────────────────────────────
# ОТЧЁТ
# ─────────────────────────────────────────────────────────────

def build_report():
    trades = read_jsonl(TRADES_FILE)
    decisions = read_jsonl(DECISION_LOG)

    open_pos, closed = build_positions(trades)

    # Сверка по каждой позиции
    by_category = defaultdict(list)
    for p in open_pos + closed:
        m = classify_trade(p, decisions)
        p["match"] = m
        by_category[m["category"]].append(p)

    # Статистика по закрытым
    stats = {}
    if closed:
        pnls = [p["pnl_pct"] for p in closed if p.get("pnl_pct") is not None]
        if pnls:
            wins = [p for p in pnls if p > 0]
            stats = {
                "n_closed": len(pnls),
                "win_rate_pct": round(len(wins) / len(pnls) * 100, 1),
                "avg_pnl_pct": round(statistics.mean(pnls), 2),
                "median_pnl_pct": round(statistics.median(pnls), 2),
                "best_pct": round(max(pnls), 2),
                "worst_pct": round(min(pnls), 2),
            }
            total_usd = sum(p.get("pnl_usd") or 0 for p in closed)
            if total_usd:
                stats["total_pnl_usd"] = round(total_usd, 2)

    # Статистика отдельно по категориям соответствия
    cat_stats = {}
    for cat, positions in by_category.items():
        pnls = [p.get("pnl_pct") for p in positions
                if p.get("pnl_pct") is not None]
        entry = {"n": len(positions), "n_closed": len(pnls)}
        if pnls:
            entry["avg_pnl_pct"] = round(statistics.mean(pnls), 2)
            entry["win_rate_pct"] = round(
                sum(1 for x in pnls if x > 0) / len(pnls) * 100, 1)
        cat_stats[cat] = entry

    ignored = find_ignored(decisions, trades)

    return {
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "trades_recorded": len(trades),
        "open_positions": open_pos,
        "closed_positions": closed,
        "stats": stats,
        "match_stats": cat_stats,
        "ignored_signals": ignored[:10],
        "note": ("Журнал заполняется вручную и ничего не исполняет. "
                 "Сверка сопоставляет твои записи с решениями движка "
                 "в окне 24 часа до сделки."),
    }


def print_report(r):
    print("=== Журнал сделок ===\n")
    print(f"  Записей в журнале: {r['trades_recorded']}")

    op = r["open_positions"]
    print(f"\n  ОТКРЫТЫЕ ПОЗИЦИИ: {len(op)}")
    for p in op:
        pnl = p.get("unrealized_pnl_pct")
        pnl_s = f"{pnl:+.2f}%" if pnl is not None else "—"
        size_s = f"${p['size_usd']:,.0f}" if p.get("size_usd") else "размер не указан"
        days = p.get("days_open")
        days_s = f"{days:.0f}д" if days is not None else "?"
        print(f"    {p['token']:8} {p['side']:6} вход ${p['entry_price']:.4f} · "
              f"{size_s} · {days_s} · {pnl_s}")
        print(f"             {p['match']['text_ru']}")

    cl = r["closed_positions"]
    print(f"\n  ЗАКРЫТЫЕ СДЕЛКИ: {len(cl)}")
    for p in cl[-8:]:
        pnl = p.get("pnl_pct")
        pnl_s = f"{pnl:+.2f}%" if pnl is not None else "—"
        dur = p.get("duration_days")
        dur_s = f"{dur:.0f}д" if dur is not None else "?"
        print(f"    {p['token']:8} {p['side']:6} ${p['entry_price']:.4f} → "
              f"${p.get('exit_price', 0):.4f} · {dur_s} · {pnl_s}")
        print(f"             {p['match']['text_ru']}")

    s = r["stats"]
    if s:
        print(f"\n  ИТОГИ ПО ЗАКРЫТЫМ:")
        print(f"    сделок {s['n_closed']} · доля прибыльных {s['win_rate_pct']}%")
        print(f"    средняя {s['avg_pnl_pct']:+.2f}% · медиана {s['median_pnl_pct']:+.2f}%")
        print(f"    лучшая {s['best_pct']:+.2f}% · худшая {s['worst_pct']:+.2f}%")
        if s.get("total_pnl_usd"):
            print(f"    суммарно ${s['total_pnl_usd']:+,.2f}")

    ms = r["match_stats"]
    if ms:
        labels = {
            "FOLLOWED": "следовала сигналу",
            "AGAINST": "против сигнала",
            "OWN_CALL": "своё решение (движок шортов не даёт)",
            "NO_SIGNAL": "сигнала не было",
            "IGNORED": "проигнорировала",
        }
        print(f"\n  СВЕРКА С ДВИЖКОМ:")
        for cat, e in sorted(ms.items()):
            lbl = labels.get(cat, cat)
            wr = f" · прибыльных {e['win_rate_pct']}%" if e.get("win_rate_pct") is not None else ""
            avg = f" · средняя {e['avg_pnl_pct']:+.2f}%" if e.get("avg_pnl_pct") is not None else ""
            print(f"    {lbl:22} {e['n']:>3} позиций{wr}{avg}")

    ig = r["ignored_signals"]
    if ig:
        print(f"\n  СИГНАЛЫ БЕЗ СДЕЛКИ (что было бы):")
        for x in ig[:6]:
            w = x.get("would_be_pnl_pct")
            if x.get("data_suspicious"):
                w_s = "данные битые"
            else:
                w_s = f"{w:+.2f}%" if w is not None else "нет цены"
            print(f"    {x['token']:8} сигнал {x['size_pct']}% · было бы {w_s}")

    print(f"\n✓ {OUT_FILE}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--add", action="store_true", help="добавить сделку")
    ap.add_argument("--token", type=str, default="")
    ap.add_argument("--action", type=str, default="",
                    help="LONG / SHORT / BUY / SELL / CLOSE / ADD")
    ap.add_argument("--price", type=float, default=0)
    ap.add_argument("--size", type=float, default=None, help="размер в USD")
    ap.add_argument("--note", type=str, default="")
    ap.add_argument("--open", action="store_true", help="только открытые позиции")
    a = ap.parse_args()

    if a.add:
        if not a.token or not a.action or not a.price:
            print("✗ Нужны --token, --action и --price")
            print("  Пример: --add --token LINK --action LONG --price 11.08 --size 500")
            sys.exit(1)
        ok = add_trade(a.token, a.action, a.price, a.size, a.note)
        if not ok:
            sys.exit(1)
        print()

    r = build_report()

    os.makedirs(CACHE, exist_ok=True)
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(r, f, indent=2, ensure_ascii=False)

    if a.open:
        print("=== Открытые позиции ===\n")
        for p in r["open_positions"]:
            pnl = p.get("unrealized_pnl_pct")
            pnl_s = f"{pnl:+.2f}%" if pnl is not None else "—"
            print(f"  {p['token']:8} {p['side']:6} ${p['entry_price']:.4f} · {pnl_s}")
        if not r["open_positions"]:
            print("  Открытых позиций нет")
    else:
        print_report(r)


if __name__ == "__main__":
    main()
