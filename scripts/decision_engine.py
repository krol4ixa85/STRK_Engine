#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
decision_engine.py · v1.0 · 20.08.2026
STRK ENGINE · единственный производитель торговых решений

ЗАЧЕМ ЭТОТ ФАЙЛ
---------------
До него решение собиралось в JavaScript внутри index.html, прямо в браузере.
У этого три неустранимых дефекта:

  1. Решение нигде не сохранялось. Через неделю невозможно узнать, что
     движок советовал вчера — значит нельзя проверить, был ли он прав.
  2. Решение нельзя было прогнать по истории. Бэктест не может открыть
     браузер и кликнуть на актив.
  3. Правила жили в одном месте, а digest в Telegram считал по-своему.
     Два ответа на один вопрос.

Здесь решение считается один раз, на сервере, и записывается в файл.
Дашборд, Telegram-бот и FULL RUN становятся ЧИТАТЕЛЯМИ, а не авторами.

ЗАМКНУТЫЙ КОНТУР
----------------
    решить  →  записать прогноз  →  дождаться срока  →  проверить  →
    посчитать точность по правилу  →  (в будущем) подстроить пороги

Первые три шага делает этот файл. Четвёртый — он же, в режиме --verify.
Пятый появляется сам, когда накопится выборка.

Без записи прогнозов калибровка невозможна в принципе. Поэтому лог
пишется с первого дня, даже пока правила заведомо сырые.

ЧЕСТНО О СТАТУСЕ ПРАВИЛ
-----------------------
Все пороги ниже заданы вручную и на истории НЕ проверены. Это структура
для дисциплины, а не измеренное преимущество. Точность появится в
decision_accuracy.json после того, как наберётся 20+ закрытых решений
на правило, и до тех пор цифры точности не печатаются.

ЗАПУСК
------
  python3 scripts/decision_engine.py            # решить по всем токенам
  python3 scripts/decision_engine.py --verify   # проверить старые решения
  python3 scripts/decision_engine.py --token LINK

ВЫХОД
-----
  data/cache/decisions.json          текущие решения (читает дашборд)
  data/history/decision_log.jsonl    append-only лог прогнозов
  data/cache/decision_accuracy.json  точность по правилам
"""

import os
import sys
import json
import glob
import argparse
from datetime import datetime, timezone, timedelta

CACHE = "data/cache"
HISTORY = "data/history"
DECISIONS_FILE = os.path.join(CACHE, "decisions.json")
ACCURACY_FILE = os.path.join(CACHE, "decision_accuracy.json")
LOG_FILE = os.path.join(HISTORY, "decision_log.jsonl")

ENGINE_VERSION = "1.0"

# Горизонт проверки прогноза. 7 дней — потому что решения принимаются
# на свинг-горизонте, а недельное окно уже покрывает типичное
# разрешение фазы, но ещё не размывается рыночным шумом месяца.
VERIFY_AFTER_DAYS = 7

# Порог успеха: движение цены, которое считаем подтверждением.
# Ниже него результат считается нейтральным, а не победой.
MOVE_THRESHOLD_PCT = 3.0

# Минимум закрытых решений на правило, ниже которого точность не печатается.
MIN_N_FOR_ACCURACY = 20


# ─────────────────────────────────────────────────────────────
# ЗАГРУЗКА
# ─────────────────────────────────────────────────────────────

def load(name, default=None):
    try:
        with open(os.path.join(CACHE, name), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def load_all_signals():
    """Единая точка чтения. Всё, на чём строится решение."""
    return {
        "health": load("hive_token_health.json", {}) or {},
        "funding": load("funding_per_token.json", {}) or {},
        "news": load("surf_events.json", {}) or {},
        "unified": load("unified_reading.json", {}) or {},
        "regime": load("market_regime.json", {}) or {},
        "sector": load("dune_sector_netflow.json", {}) or {},
        "prices": load("hive_prices.json", {}) or {},
    }


def load_token_scan(token):
    return load(f"token_scan/{token}.json")


def discover_tokens():
    """Токены, по которым есть скан Dune."""
    out = []
    for p in sorted(glob.glob(os.path.join(CACHE, "token_scan", "*.json"))):
        name = os.path.basename(p)[:-5].upper()
        if name and name not in ("INDEX", "_META"):
            out.append(name)
    return out


# ─────────────────────────────────────────────────────────────
# СЛОЙ 1 · СТРУКТУРА ПОТОКА (фаза)
# ─────────────────────────────────────────────────────────────

def read_flow(scan):
    """
    Читает фазу по потокам Dune.

    Все три величины меряются на ОДНОМ окне с вердиктом скана
    (8 недель для сигналов, 180 дней для доли и свежести).
    Смешивание окон однажды уже дало противоречие: вердикт говорил
    «середина накопления», а полоса прогресса — «поздняя стадия»,
    потому что считала сигналы за всю историю.
    """
    if not scan:
        return None

    pos = float(scan.get("pct_positive_weeks_180d") or 0)
    streak = int(scan.get("current_positive_streak_weeks") or 0)
    sos8 = int(scan.get("recent_sos_count") or 0)
    dist8 = int(scan.get("recent_dist_count") or 0)
    nf30 = float(scan.get("netflow_30d_usd") or 0)
    nf180 = float(scan.get("netflow_180d_usd") or 0)

    # Свежесть: доля полугодового притока, пришедшаяся на последний месяц.
    # Больше 1.0 значит, что до этого был отток — накопление молодое.
    recency = (nf30 / nf180) if nf180 > 0 else None

    if dist8 > 0 and nf30 < 0:
        stage, code = "Распределение", "DISTRIBUTION"
    elif recency is not None and recency > 0.8 and pos < 55:
        stage, code = "Ранняя стадия · приток свежий", "EARLY_ACCUM"
    elif pos >= 65 and streak >= 4:
        stage, code = "Зрелое накопление", "MATURE_ACCUM"
    elif sos8 >= 1 and pos >= 55 and streak >= 2:
        stage, code = "Поздняя стадия · близко к разрешению", "LATE_ACCUM"
    else:
        stage, code = "Неопределённая · без явного перевеса", "UNCLEAR"

    return {
        "stage": stage, "code": code,
        "pct_positive_180d": round(pos, 1),
        "streak_weeks": streak,
        "sos_8w": sos8, "dist_8w": dist8,
        "netflow_30d_usd": nf30, "netflow_180d_usd": nf180,
        "recency_ratio": round(recency, 3) if recency is not None else None,
    }


# ─────────────────────────────────────────────────────────────
# СЛОЙ 2 · ПРОТИВОРЕЧИЯ
# ─────────────────────────────────────────────────────────────

def find_conflicts(token, flow, sig, scan):
    """
    Ищет расхождения между слоями. Каждое правило именуется, чтобы
    потом можно было посчитать точность отдельно по каждому.
    """
    out = []
    news_events = ((sig["news"].get("per_asset_top") or {}).get(token)) or []
    size_down = [e for e in news_events
                 if (e.get("action_hint") or {}).get("field") == "SIZE"
                 and (e.get("action_hint") or {}).get("action") == "down"]
    fomo = [e for e in news_events if e.get("fomo_risk")]

    bullish_stage = flow and flow["code"] in ("EARLY_ACCUM", "MATURE_ACCUM", "LATE_ACCUM")

    if bullish_stage and flow["pct_positive_180d"] < 55 and flow["streak_weeks"] == 0:
        out.append({
            "rule": "short_window_only", "level": "warn",
            "text": f"Фаза бычья только на коротком окне: за полгода недель с притоком "
                    f"{flow['pct_positive_180d']:.0f}%, серия нулевая.",
        })

    if bullish_stage and flow.get("recency_ratio") and flow["recency_ratio"] > 1:
        out.append({
            "rule": "young_accumulation", "level": "info",
            "text": "Весь полугодовой приток пришёлся на последний месяц — "
                    "до этого был отток. Разворот не подтверждён временем.",
        })

    if size_down and bullish_stage:
        out.append({
            "rule": "news_vs_flow", "level": "warn",
            "text": f"{len(size_down)} новостных событий требуют урезать размер, "
                    f"пока потоки говорят о накоплении.",
        })

    if len(fomo) >= 2 and bullish_stage:
        out.append({
            "rule": "fomo_wave", "level": "warn",
            "text": f"{len(fomo)} новостей помечены FOMO-риском. Бычья фаза на волне "
                    f"хайпа чаще встречается у локальных вершин.",
        })

    f = (sig["funding"].get("tokens") or {}).get(token) or {}
    if bullish_stage and f.get("bias") == "LONGS_PAY":
        out.append({
            "rule": "crowded_long", "level": "warn",
            "text": "Накопление на споте, но в бессрочных перекос в лонги — "
                    "толпа уже стоит в ту же сторону.",
        })

    h = (sig["health"].get("tokens") or {}).get(token) or {}
    depth = h.get("depth") or {}
    if bullish_stage and depth.get("depth_grade") == "THIN":
        out.append({
            "rule": "thin_book", "level": "warn",
            "text": "Стакан тонкий относительно оборота — проскальзывание съест край.",
        })

    oi = f.get("open_interest_usd")
    if oi is not None and oi < 5e6:
        out.append({
            "rule": "no_derivatives", "level": "info",
            "text": f"Открытый интерес ${oi/1e6:.1f}M — деривативов почти нет, "
                    f"сигналам по фандингу веры мало.",
        })

    return out, size_down, fomo


# ─────────────────────────────────────────────────────────────
# СЛОЙ 3 · РЕЖИМ РЫНКА (общий множитель)
# ─────────────────────────────────────────────────────────────

def regime_multiplier(sig):
    """
    Режим рынка не переворачивает решение, но масштабирует размер.
    Смысл: при подавленных альтах правильная идея всё равно работает
    медленнее и с большей просадкой, значит стоит меньшего размера.
    """
    reg = sig["regime"] or {}
    name = reg.get("regime")
    score = reg.get("weighted_score")

    table = {
        "STRONG_BULL": (1.0, "сильный бычий рынок — размер без урезания"),
        "BULL_EARLY": (1.0, "ранний бычий рынок"),
        "BULL_BIAS": (0.9, "уклон вверх"),
        "NEUTRAL_MIXED": (0.7, "смешанные сигналы по рынку — размер урезан"),
        "BEAR_BIAS": (0.5, "уклон вниз — половина размера"),
        "BEAR_DEVELOPING": (0.3, "медвежий рынок развивается"),
        "STRONG_BEAR": (0.0, "сильный медвежий рынок — новых позиций не открываем"),
    }
    mult, why = table.get(name, (0.8, "режим рынка неизвестен — размер урезан из осторожности"))

    # Доминация BTC: подавленные альты — отдельный штраф
    phase = load("total_phase.json", {}) or {}
    btcd = phase.get("btc_dominance") or phase.get("btc_d")
    if isinstance(btcd, (int, float)) and btcd > 57:
        mult *= 0.8
        why += f"; доминация BTC {btcd:.1f}% — альты под давлением"

    return round(mult, 2), name, score, why


# ─────────────────────────────────────────────────────────────
# СЛОЙ 4 · РЕШЕНИЕ
# ─────────────────────────────────────────────────────────────

BASE_BY_STAGE = {
    "DISTRIBUTION": ("НЕ ВХОДИТЬ", 0,
                     "Фаза распределения — крупные выходят. Вход против этого потока "
                     "исторически самый дорогой."),
    "EARLY_ACCUM": ("ВХОД ЧАСТЬЮ", 33,
                    "Ранняя фаза: приток свежий, но не подтверждён временем. "
                    "Пробная часть, остальное по подтверждению."),
    "MATURE_ACCUM": ("ВХОД ЧАСТЬЮ", 66,
                     "Зрелое накопление: спрос держится много недель подряд."),
    "LATE_ACCUM": ("ВХОД ЧАСТЬЮ", 50,
                   "Поздняя фаза: разрешение близко, но риск запоздать выше."),
    "UNCLEAR": ("ЖДАТЬ", 0,
                "Структура потока без перевеса. Вход здесь — ставка на удачу, "
                "а не на фазу."),
}


def decide(token, sig):
    scan = load_token_scan(token)
    flow = read_flow(scan)

    if not flow:
        return {
            "token": token, "action": "НЕТ ДАННЫХ", "size_pct": 0,
            "stage": None, "notes": ["Скан Dune по токену отсутствует."],
            "conflicts": [], "triggers": [], "invalidations": [],
            "rules_fired": [],
        }

    conflicts, size_down, fomo = find_conflicts(token, flow, sig, scan)
    action, size, base_note = BASE_BY_STAGE[flow["code"]]
    notes = [base_note]
    rules = [f"stage:{flow['code']}"]

    h = (sig["health"].get("tokens") or {}).get(token) or {}
    depth = h.get("depth") or {}
    holders = h.get("holders") or {}

    # ── корректировки размера ──
    if size > 0 and size_down:
        cut = min(50, len(size_down) * 25)
        size = round(size * (1 - cut / 100))
        notes.append(f"Новости срезали размер на {cut}%: {len(size_down)} событий "
                     f"требуют уменьшить экспозицию.")
        rules.append("news_size_down")

    if size > 0 and depth.get("depth_grade") == "THIN":
        size = round(size * 0.5)
        notes.append("Стакан тонкий — размер урезан вдвое.")
        rules.append("thin_book_cut")

    if size > 0 and holders.get("concentration_grade") == "HIGH_CONCENTRATION":
        contracts = sum(1 for x in (holders.get("top_holders") or []) if x.get("is_contract"))
        if contracts < 3:
            size = round(size * 0.75)
            notes.append("Высокая концентрация у живых кошельков — размер урезан на четверть.")
            rules.append("concentration_cut")
        else:
            notes.append("Концентрация высокая, но верх занят контрактами проекта — "
                         "как риск не считаем.")

    # ── режим рынка ──
    mult, reg_name, reg_score, reg_why = regime_multiplier(sig)
    if size > 0 and mult < 1.0:
        before = size
        size = round(size * mult)
        notes.append(f"Режим рынка ({reg_name}): {reg_why}. Размер {before}% → {size}%.")
        rules.append(f"regime:{reg_name}")

    # ── жёсткие стопы ──
    uv = next((v for v in (sig["unified"].get("per_asset_verdicts") or [])
               if v.get("asset") == token), None)
    if uv and any(k in str(uv.get("verdict", "")).upper()
                  for k in ("FALSE_RALLY", "AVOID", "EXIT")):
        action, size = "НЕ ВХОДИТЬ", 0
        notes.append(f"Общая картина ставит вето: {uv['verdict']}. "
                     f"Это перекрывает сигнал фазы.")
        rules.append("unified_veto")

    hard = sum(1 for c in conflicts if c["level"] == "warn")
    if size > 0 and hard >= 3:
        action, size = "ЖДАТЬ", 0
        notes.append(f"{hard} серьёзных расхождения между слоями. Когда столько "
                     f"сигналов спорят, дешевле подождать ясности.")
        rules.append("conflict_gate")

    if size == 0 and action == "ВХОД ЧАСТЬЮ":
        action = "ЖДАТЬ"

    # ── что переведёт в вход ──
    triggers = []
    if flow["streak_weeks"] == 0:
        triggers.append("Появится серия из 2+ недель подряд с притоком")
    if flow["pct_positive_180d"] < 65:
        triggers.append(f"Доля недель с притоком вырастет с "
                        f"{flow['pct_positive_180d']:.0f}% до 65%+")
    if flow["sos_8w"] == 0:
        triggers.append("Появится сигнал силы (SOS) в 8-недельном окне")
    if size_down:
        triggers.append("Новостной фон перестанет требовать урезания размера")
    if len(fomo) >= 2:
        triggers.append("Волна FOMO-заголовков спадёт")
    if mult < 0.8:
        triggers.append("Режим рынка перестанет давить на альты")

    invalidations = [
        "Появится сигнал распределения (Dist) в 8-недельном окне",
        "Месячный приток станет отрицательным",
        "Цена уйдёт ниже минимума последней недели с притоком",
    ]

    price = None
    prow = (sig["prices"].get("prices") or {}).get(token) or {}
    if prow.get("price_usd"):
        price = prow["price_usd"]
    elif scan.get("price_now"):
        price = scan["price_now"]

    return {
        "token": token,
        "action": action,
        "size_pct": size,
        "stage": flow["stage"],
        "stage_code": flow["code"],
        "flow": flow,
        "conflicts": conflicts,
        "conflicts_warn": hard,
        "notes": notes,
        "triggers": triggers,
        "invalidations": invalidations,
        "rules_fired": rules,
        "regime": {"name": reg_name, "score": reg_score, "multiplier": mult},
        "unified_verdict": (uv or {}).get("verdict"),
        "price_at_decision": price,
    }


# ─────────────────────────────────────────────────────────────
# ЗАПИСЬ ПРОГНОЗА · без неё калибровка невозможна
# ─────────────────────────────────────────────────────────────

def log_decision(d, now):
    """
    Пишем прогноз с критерием проверки ЗАРАНЕЕ. Задним числом критерий
    не меняется — иначе любая система выглядит успешной.
    """
    if d["action"] == "НЕТ ДАННЫХ" or not d.get("price_at_decision"):
        return None

    # Направленное ожидание, вытекающее из действия
    if d["action"] == "ВХОД ЧАСТЬЮ":
        expect = "UP"
    elif d["action"] == "НЕ ВХОДИТЬ":
        expect = "DOWN_OR_FLAT"
    else:
        expect = "FLAT"

    rec = {
        "id": f"{d['token']}-{now.strftime('%Y%m%dT%H%M%SZ')}",
        "token": d["token"],
        "issued_at": now.isoformat(),
        "verify_after": (now + timedelta(days=VERIFY_AFTER_DAYS)).isoformat(),
        "engine_version": ENGINE_VERSION,
        "action": d["action"],
        "size_pct": d["size_pct"],
        "stage_code": d["stage_code"],
        "rules_fired": d["rules_fired"],
        "conflicts_warn": d["conflicts_warn"],
        "regime": d["regime"]["name"],
        "price_at_decision": d["price_at_decision"],
        "expectation": expect,
        "threshold_pct": MOVE_THRESHOLD_PCT,
        "status": "PENDING",
        "outcome": None,
        "price_at_verify": None,
        "evaluated_at": None,
    }

    os.makedirs(HISTORY, exist_ok=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec["id"]


def read_log():
    recs = []
    try:
        with open(LOG_FILE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        recs.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
    except FileNotFoundError:
        pass
    return recs


def write_log(recs):
    os.makedirs(HISTORY, exist_ok=True)
    with open(LOG_FILE, "w", encoding="utf-8") as f:
        for r in recs:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


# ─────────────────────────────────────────────────────────────
# ПРОВЕРКА · закрываем прогнозы, у которых наступил срок
# ─────────────────────────────────────────────────────────────

def verify():
    recs = read_log()
    if not recs:
        print("Лог решений пуст — проверять нечего.")
        return

    now = datetime.now(timezone.utc)
    prices = (load("hive_prices.json", {}) or {}).get("prices", {})

    closed = 0
    for r in recs:
        if r.get("status") != "PENDING":
            continue
        try:
            va = datetime.fromisoformat(r["verify_after"])
        except Exception:
            continue
        if va > now:
            continue

        row = prices.get(r["token"]) or {}
        p_now = row.get("price_usd")
        if not p_now:
            scan = load_token_scan(r["token"]) or {}
            p_now = scan.get("price_now")
        if not p_now or not r.get("price_at_decision"):
            r["status"] = "EXPIRED_UNCLEAR"
            r["evaluated_at"] = now.isoformat()
            r["outcome"] = "нет цены для оценки"
            closed += 1
            continue

        change = (p_now - r["price_at_decision"]) / r["price_at_decision"] * 100
        thr = r.get("threshold_pct", MOVE_THRESHOLD_PCT)

        if r["expectation"] == "UP":
            ok = change >= thr
            miss = change <= -thr
        elif r["expectation"] == "DOWN_OR_FLAT":
            ok = change <= thr
            miss = change > thr
        else:  # FLAT — «подождать» оказалось верным, если сильного роста не было
            ok = abs(change) < thr or change < 0
            miss = change >= thr

        r["status"] = "HIT" if ok else ("MISS" if miss else "NEUTRAL")
        r["price_at_verify"] = p_now
        r["price_change_pct"] = round(change, 2)
        r["evaluated_at"] = now.isoformat()
        r["outcome"] = f"цена {change:+.2f}% за {VERIFY_AFTER_DAYS} дней"
        closed += 1
        print(f"  {r['token']:8} {r['action']:12} → {r['status']:8} ({change:+.2f}%)")

    write_log(recs)
    print(f"\nЗакрыто прогнозов: {closed}")
    build_accuracy(recs)


def build_accuracy(recs):
    """
    Точность по каждому правилу. Печатается ТОЛЬКО при достаточной
    выборке — иначе это гадание, выданное за измерение.
    """
    by_rule = {}
    for r in recs:
        if r.get("status") not in ("HIT", "MISS", "NEUTRAL"):
            continue
        for rule in r.get("rules_fired", []):
            b = by_rule.setdefault(rule, {"hit": 0, "miss": 0, "neutral": 0})
            b[r["status"].lower()] += 1

    out = {"computed_at": datetime.now(timezone.utc).isoformat(),
           "engine_version": ENGINE_VERSION,
           "min_n_required": MIN_N_FOR_ACCURACY,
           "rules": {}}

    for rule, b in sorted(by_rule.items()):
        n = b["hit"] + b["miss"] + b["neutral"]
        directional = b["hit"] + b["miss"]
        entry = {"n_closed": n, "hit": b["hit"], "miss": b["miss"],
                 "neutral": b["neutral"], "enough_data": n >= MIN_N_FOR_ACCURACY}
        if n >= MIN_N_FOR_ACCURACY and directional > 0:
            entry["hit_rate_pct"] = round(b["hit"] / directional * 100, 1)
        else:
            entry["hit_rate_pct"] = None
            entry["reason"] = f"нужно ≥{MIN_N_FOR_ACCURACY} закрытых, есть {n}"
        out["rules"][rule] = entry

    os.makedirs(CACHE, exist_ok=True)
    with open(ACCURACY_FILE, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)

    ready = sum(1 for v in out["rules"].values() if v["enough_data"])
    print(f"Правил в статистике: {len(out['rules'])} · "
          f"с достаточной выборкой: {ready}")
    if ready == 0 and out["rules"]:
        print(f"Точность пока не публикуется — ни у одного правила нет "
              f"{MIN_N_FOR_ACCURACY} закрытых решений.")


# ─────────────────────────────────────────────────────────────
# ГЛАВНЫЙ ПРОГОН
# ─────────────────────────────────────────────────────────────

def run(only=None):
    print(f"=== Decision Engine v{ENGINE_VERSION} ===\n")
    sig = load_all_signals()

    missing = [k for k, v in sig.items() if not v]
    if missing:
        print(f"⚠ Нет данных: {', '.join(missing)} — решения будут беднее\n")

    tokens = only or discover_tokens()
    if not tokens:
        print("✗ Не найдено ни одного скана в data/cache/token_scan/")
        return

    now = datetime.now(timezone.utc)
    mult, reg_name, reg_score, reg_why = regime_multiplier(sig)
    print(f"Режим рынка: {reg_name} · множитель размера ×{mult}")
    print(f"   {reg_why}\n")

    decisions, logged = {}, 0
    for t in tokens:
        d = decide(t, sig)
        decisions[t] = d
        if log_decision(d, now):
            logged += 1
        size = f"{d['size_pct']}%" if d["size_pct"] else "—"
        print(f"  {t:8} {d['action']:12} {size:>5}  {d.get('stage') or ''}")

    actionable = [d for d in decisions.values() if d["size_pct"] > 0]

    out = {
        "computed_at": now.isoformat(),
        "engine_version": ENGINE_VERSION,
        "verify_after_days": VERIFY_AFTER_DAYS,
        "regime": {"name": reg_name, "score": reg_score,
                   "multiplier": mult, "why": reg_why},
        "tokens_evaluated": len(decisions),
        "tokens_actionable": len(actionable),
        "rules_status": "HYPOTHESIS · пороги заданы вручную, на истории не проверены",
        "decisions": decisions,
    }

    os.makedirs(CACHE, exist_ok=True)
    with open(DECISIONS_FILE, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)

    print(f"\n  Оценено токенов: {len(decisions)}")
    print(f"  С ненулевым размером: {len(actionable)}")
    print(f"  Записано прогнозов в лог: {logged}")
    print(f"\n✓ {DECISIONS_FILE}")
    print(f"✓ {LOG_FILE}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--verify", action="store_true",
                    help="закрыть прогнозы, у которых наступил срок")
    ap.add_argument("--token", type=str, default="",
                    help="только эти токены, через запятую")
    a = ap.parse_args()

    if a.verify:
        verify()
    else:
        only = [s.strip().upper() for s in a.token.split(",") if s.strip()] or None
        run(only)
