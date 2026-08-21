#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
decision_engine.py · v1.1 · 21.08.2026
STRK ENGINE · единственный автор торговых решений

ЧТО ИЗМЕНИЛОСЬ ОТ v1.0
----------------------
1. ФАЗА из Dune-скана (phase_verdict), а не выведенная заново.
2. УСКОРЕНИЕ ПОТОКА из phase_analysis.json (ACCEL_UP / ACCEL_DOWN / ...).
3. SANITY-ФИЛЬТР битых данных (ARB +1 717 040% больше не проходит).
4. ДИВЕРГЕНЦИЯ цена vs капитал как самостоятельный сигнал.
"""

import os, sys, json, glob, argparse
from datetime import datetime, timezone, timedelta

CACHE = "data/cache"
HISTORY = "data/history"
DECISIONS_FILE = os.path.join(CACHE, "decisions.json")
ACCURACY_FILE = os.path.join(CACHE, "decision_accuracy.json")
LOG_FILE = os.path.join(HISTORY, "decision_log.jsonl")

ENGINE_VERSION = "1.1"
VERIFY_AFTER_DAYS = 7
MOVE_THRESHOLD_PCT = 3.0
MIN_N_FOR_ACCURACY = 20

PHASE_MAP = {
    "MID_ACCUMULATION_STRONG":     ("ВХОД ЧАСТЬЮ", 66, "Зрелое накопление подтверждено сигналами силы"),
    "LATE_ACCUMULATION_OR_MARKUP": ("ВХОД ЧАСТЬЮ", 75, "Поздняя фаза или начало markup — движение близко"),
    "MID_ACCUMULATION":            ("ВХОД ЧАСТЬЮ", 50, "Устойчивое накопление, серия положительных недель"),
    "ACCUMULATION_PHASE_B":        ("ВХОД ЧАСТЬЮ", 33, "Wyckoff Phase B — построение базы"),
    "EARLY_ACCUMULATION":          ("ВХОД ЧАСТЬЮ", 25, "Ранняя фаза — приток только начал появляться"),
    "MIXED_OR_NEUTRAL":            ("ЖДАТЬ", 0, "Без явного перевеса — вход это ставка на удачу"),
    "WEAKENING":                   ("НЕ ВХОДИТЬ", 0, "Ослабление — приток исчерпывается"),
    "MARKDOWN":                    ("НЕ ВХОДИТЬ", 0, "Фаза распределения / падения"),
    "DISTRIBUTION_ACTIVE":         ("НЕ ВХОДИТЬ", 0, "Активное распределение"),
}
DEFAULT_PHASE = ("ЖДАТЬ", 0, "Фаза не распознана")


def load(name, default=None):
    try:
        with open(os.path.join(CACHE, name), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def load_token_scan(token):
    return load(f"token_scan/{token}.json")


def load_all_signals():
    return {
        "phase_analysis": load("phase_analysis.json", {}) or {},
        "unified": load("unified_reading.json", {}) or {},
        "regime": load("market_regime.json", {}) or {},
        "funding": load("funding_per_token.json", {}) or {},
        "news": load("surf_events.json", {}) or {},
        "health": load("hive_token_health.json", {}) or {},
        "sector": load("dune_sector_netflow.json", {}) or {},
        "prices": load("hive_prices.json", {}) or {},
        "phase_total": load("total_phase.json", {}) or {},
    }


def discover_tokens():
    out = []
    for p in sorted(glob.glob(os.path.join(CACHE, "token_scan", "*.json"))):
        name = os.path.basename(p)[:-5].upper()
        if name and name not in ("INDEX", "_META"):
            out.append(name)
    return out


def apply_flow_regime(base_size, regime_code, notes, rules):
    if regime_code == "ACCEL_UP" and base_size > 0:
        adjusted = min(100, round(base_size * 1.25))
        notes.append(f"Поток ускоряется → размер {base_size}% → {adjusted}%.")
        rules.append("flow_accel_up_boost")
        return adjusted
    if regime_code == "ACCEL_DOWN" and base_size > 0:
        notes.append("Отток ускоряется — вето несмотря на фазу.")
        rules.append("flow_accel_down_veto")
        return 0
    if regime_code == "STALLING_UP" and base_size > 0:
        adjusted = round(base_size * 0.5)
        notes.append(f"Приток есть, но темп падает — вершина возможна. Размер {base_size}% → {adjusted}%.")
        rules.append("flow_stalling_cut")
        return adjusted
    if regime_code == "FLIPPING_UP" and base_size > 33:
        notes.append("Только что развернулось из оттока в приток — пробная часть.")
        rules.append("flow_flipping_up_cap")
        return 33
    if regime_code == "FLIPPING_DOWN" and base_size > 0:
        notes.append("Поток развернулся из плюса в минус — сигнал выхода.")
        rules.append("flow_flipping_down_veto")
        return 0
    if regime_code == "STALLING_DOWN":
        notes.append("Отток замедляется — возможное дно, но подтверждения ещё нет.")
        rules.append("flow_stalling_down_note")
    return base_size


def find_conflicts(token, phase_verdict, base_size, phase_analysis, sig):
    out = []
    news = ((sig["news"].get("per_asset_top") or {}).get(token)) or []
    size_down = [e for e in news if (e.get("action_hint") or {}).get("field") == "SIZE"
                 and (e.get("action_hint") or {}).get("action") == "down"]
    fomo = [e for e in news if e.get("fomo_risk")]
    bullish = base_size > 0
    regime_code = (phase_analysis.get("regime") or {}).get("code")

    if bullish and regime_code == "ACCEL_DOWN":
        out.append({"rule": "phase_vs_flow", "level": "warn",
                    "text": f"Dune видит {phase_verdict}, delta показывает ускорение оттока."})
    if size_down and bullish:
        out.append({"rule": "news_vs_flow", "level": "warn",
                    "text": f"{len(size_down)} новостей требуют урезать размер."})
    if len(fomo) >= 2 and bullish:
        out.append({"rule": "fomo_wave", "level": "warn",
                    "text": f"{len(fomo)} новостей помечены FOMO-риском."})
    f = (sig["funding"].get("tokens") or {}).get(token) or {}
    if bullish and f.get("bias") == "LONGS_PAY":
        out.append({"rule": "crowded_long", "level": "warn",
                    "text": "Перекос в лонги — толпа уже здесь."})
    h = (sig["health"].get("tokens") or {}).get(token) or {}
    depth = h.get("depth") or {}
    if bullish and depth.get("depth_grade") == "THIN":
        out.append({"rule": "thin_book", "level": "warn",
                    "text": "Стакан тонкий относительно оборота."})
    oi = f.get("open_interest_usd")
    if oi is not None and oi < 5e6:
        out.append({"rule": "no_derivatives", "level": "info",
                    "text": f"OI ${oi/1e6:.1f}M — фандинг слабый сигнал."})
    return out, size_down, fomo


def regime_multiplier(sig):
    reg = sig["regime"] or {}
    name = reg.get("regime")
    score = reg.get("weighted_score")
    table = {
        "STRONG_BULL": (1.0, "сильный бычий"), "BULL_EARLY": (1.0, "ранний бычий"),
        "BULL_BIAS": (0.9, "уклон вверх"), "NEUTRAL_MIXED": (0.7, "смешанные сигналы"),
        "BEAR_BIAS": (0.5, "уклон вниз"), "BEAR_DEVELOPING": (0.3, "медвежий развивается"),
        "STRONG_BEAR": (0.0, "сильный медвежий — новых позиций не открываем"),
    }
    mult, why = table.get(name, (0.8, "режим неизвестен"))
    phase = sig.get("phase_total") or {}
    btcd = phase.get("btc_dominance") or phase.get("btc_d")
    if isinstance(btcd, (int, float)) and btcd > 57:
        mult *= 0.8
        why += f"; доминация BTC {btcd:.1f}% — альты под давлением"
    return round(mult, 2), name, score, why


def decide(token, sig):
    scan = load_token_scan(token)
    if not scan:
        return {"token": token, "action": "НЕТ ДАННЫХ", "size_pct": 0,
                "notes": ["Скан Dune отсутствует."], "conflicts": [], "triggers": [],
                "invalidations": [], "rules_fired": []}

    pa_row = (sig["phase_analysis"].get("tokens") or {}).get(token) or {}
    data_quality = pa_row.get("data_quality") or {"ok": True, "flags": []}

    if not data_quality.get("ok"):
        return {
            "token": token, "action": "ДАННЫЕ ПОДОЗРИТЕЛЬНЫ", "size_pct": 0,
            "stage": "DATA_SUSPICIOUS",
            "notes": ["Данные не прошли sanity-фильтр — сигнал отвергнут:"] +
                     [f"  {fl.get('reason','')}" for fl in data_quality.get("flags", [])],
            "conflicts": [], "triggers": [], "invalidations": [],
            "rules_fired": ["data_quality_reject"],
            "data_quality_flags": data_quality.get("flags", []),
        }

    phase_verdict = scan.get("phase_verdict")
    action, base_size, phase_note = PHASE_MAP.get(phase_verdict, DEFAULT_PHASE)
    size = base_size
    notes = [f"Фаза Dune: {phase_verdict}. {phase_note}"]
    rules = [f"phase:{phase_verdict}"]

    regime = pa_row.get("regime") or {}
    regime_code = regime.get("code")
    if regime_code:
        notes.append(f"Динамика потока: {regime.get('text_ru', regime_code)}.")
        size = apply_flow_regime(size, regime_code, notes, rules)

    div = pa_row.get("divergence") or {}
    div_code = div.get("code")
    if div_code == "BEARISH_DIV" and size > 0:
        action, size = "НЕ ВХОДИТЬ", 0
        notes.append(f"Дивергенция: {div.get('text_ru', '')} — вето.")
        rules.append("bearish_divergence_veto")
    elif div_code == "BULLISH_DIV":
        notes.append(f"Дивергенция в нашу пользу: {div.get('text_ru', '')}.")
        rules.append("bullish_divergence_note")

    conflicts, size_down, fomo = find_conflicts(token, phase_verdict, size, pa_row, sig)

    if size > 0 and size_down:
        cut = min(50, len(size_down) * 25)
        new_size = round(size * (1 - cut / 100))
        notes.append(f"Новости срезали размер на {cut}%.")
        rules.append("news_size_down")
        size = new_size

    h = (sig["health"].get("tokens") or {}).get(token) or {}
    depth = h.get("depth") or {}
    holders = h.get("holders") or {}

    if size > 0 and depth.get("depth_grade") == "THIN":
        size = round(size * 0.5)
        notes.append("Стакан тонкий — размер урезан вдвое.")
        rules.append("thin_book_cut")

    if size > 0 and holders.get("concentration_grade") == "HIGH_CONCENTRATION":
        contracts = sum(1 for x in (holders.get("top_holders") or []) if x.get("is_contract"))
        if contracts < 3:
            size = round(size * 0.75)
            notes.append("Высокая концентрация у живых кошельков.")
            rules.append("concentration_cut")

    mult, reg_name, reg_score, reg_why = regime_multiplier(sig)
    if size > 0 and mult < 1.0:
        before = size
        size = round(size * mult)
        notes.append(f"Режим рынка ({reg_name}): {reg_why}. {before}% → {size}%.")
        rules.append(f"regime:{reg_name}")

    uv = next((v for v in (sig["unified"].get("per_asset_verdicts") or [])
               if v.get("asset") == token), None)
    if uv and any(k in str(uv.get("verdict", "")).upper()
                  for k in ("FALSE_RALLY", "AVOID", "EXIT")):
        action, size = "НЕ ВХОДИТЬ", 0
        notes.append(f"Общая картина ставит вето: {uv['verdict']}.")
        rules.append("unified_veto")

    hard = sum(1 for c in conflicts if c["level"] == "warn")
    if size > 0 and hard >= 3:
        action, size = "ЖДАТЬ", 0
        notes.append(f"{hard} расхождения — дешевле подождать.")
        rules.append("conflict_gate")

    if size == 0 and action == "ВХОД ЧАСТЬЮ":
        action = "ЖДАТЬ"

    triggers = []
    if regime_code in ("STALLING_UP", "STEADY_UP") and size == 0:
        triggers.append("Появится ACCEL_UP")
    if regime_code == "STALLING_DOWN":
        triggers.append("Поток развернётся в плюс (FLIPPING_UP)")
    if phase_verdict in ("MIXED_OR_NEUTRAL", "ACCUMULATION_PHASE_B"):
        triggers.append("Dune-фаза перейдёт в MID_ACCUMULATION")
    if size_down:
        triggers.append("Новостной фон перестанет требовать урезания")
    if len(fomo) >= 2:
        triggers.append("Волна FOMO-заголовков спадёт")
    if mult < 0.8:
        triggers.append("Режим рынка станет BULL_BIAS или лучше")

    invalidations = [
        "Ускорение потока перевернётся в ACCEL_DOWN",
        "Появится дивергенция BEARISH_DIV",
        "Dune-фаза перейдёт в WEAKENING или MARKDOWN",
    ]

    price = None
    prow = (sig["prices"].get("prices") or {}).get(token) or {}
    if prow.get("price_usd"):
        price = prow["price_usd"]
    elif scan.get("price_now"):
        price = scan["price_now"]

    return {
        "token": token, "action": action, "size_pct": size,
        "stage": phase_verdict, "phase_verdict_dune": phase_verdict,
        "flow_regime": regime_code, "flow_regime_text": regime.get("text_ru"),
        "divergence": div_code,
        "conflicts": conflicts, "conflicts_warn": hard,
        "notes": notes, "triggers": triggers, "invalidations": invalidations,
        "rules_fired": rules,
        "regime": {"name": reg_name, "score": reg_score, "multiplier": mult},
        "unified_verdict": (uv or {}).get("verdict"),
        "price_at_decision": price,
        "flow_accel_4w_usd": (pa_row.get("flow") or {}).get("flow_accel_4w_usd"),
    }


def log_decision(d, now):
    if d["action"] in ("НЕТ ДАННЫХ", "ДАННЫЕ ПОДОЗРИТЕЛЬНЫ") or not d.get("price_at_decision"):
        return None
    expect = "UP" if d["action"] == "ВХОД ЧАСТЬЮ" else ("DOWN_OR_FLAT" if d["action"] == "НЕ ВХОДИТЬ" else "FLAT")
    rec = {"id": f"{d['token']}-{now.strftime('%Y%m%dT%H%M%SZ')}",
           "token": d["token"], "issued_at": now.isoformat(),
           "verify_after": (now + timedelta(days=VERIFY_AFTER_DAYS)).isoformat(),
           "engine_version": ENGINE_VERSION, "action": d["action"],
           "size_pct": d["size_pct"], "stage_code": d["stage"],
           "flow_regime": d.get("flow_regime"),
           "rules_fired": d["rules_fired"], "conflicts_warn": d["conflicts_warn"],
           "regime": d["regime"]["name"], "price_at_decision": d["price_at_decision"],
           "expectation": expect, "threshold_pct": MOVE_THRESHOLD_PCT,
           "status": "PENDING", "outcome": None,
           "price_at_verify": None, "evaluated_at": None}
    os.makedirs(HISTORY, exist_ok=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec["id"]


def read_log():
    recs = []
    try:
        with open(LOG_FILE, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    try: recs.append(json.loads(line.strip()))
                    except json.JSONDecodeError: pass
    except FileNotFoundError: pass
    return recs


def write_log(recs):
    os.makedirs(HISTORY, exist_ok=True)
    with open(LOG_FILE, "w", encoding="utf-8") as f:
        for r in recs: f.write(json.dumps(r, ensure_ascii=False) + "\n")


def verify():
    recs = read_log()
    if not recs:
        print("Лог пуст."); return
    now = datetime.now(timezone.utc)
    prices = (load("hive_prices.json", {}) or {}).get("prices", {})
    closed = 0
    for r in recs:
        if r.get("status") != "PENDING": continue
        try: va = datetime.fromisoformat(r["verify_after"])
        except Exception: continue
        if va > now: continue
        row = prices.get(r["token"]) or {}
        p_now = row.get("price_usd") or (load_token_scan(r["token"]) or {}).get("price_now")
        if not p_now or not r.get("price_at_decision"):
            r["status"] = "EXPIRED_UNCLEAR"; r["evaluated_at"] = now.isoformat()
            r["outcome"] = "нет цены для оценки"; closed += 1; continue
        change = (p_now - r["price_at_decision"]) / r["price_at_decision"] * 100
        thr = r.get("threshold_pct", MOVE_THRESHOLD_PCT)
        if r["expectation"] == "UP":
            ok, miss = change >= thr, change <= -thr
        elif r["expectation"] == "DOWN_OR_FLAT":
            ok, miss = change <= thr, change > thr
        else:
            ok, miss = (abs(change) < thr or change < 0), change >= thr
        r["status"] = "HIT" if ok else ("MISS" if miss else "NEUTRAL")
        r["price_at_verify"] = p_now; r["price_change_pct"] = round(change, 2)
        r["evaluated_at"] = now.isoformat()
        r["outcome"] = f"цена {change:+.2f}% за {VERIFY_AFTER_DAYS} дней"
        closed += 1
        print(f"  {r['token']:8} {r['action']:12} → {r['status']:8} ({change:+.2f}%)")
    write_log(recs)
    print(f"\nЗакрыто: {closed}")
    build_accuracy(recs)


def build_accuracy(recs):
    by_rule = {}
    for r in recs:
        if r.get("status") not in ("HIT", "MISS", "NEUTRAL"): continue
        for rule in r.get("rules_fired", []):
            b = by_rule.setdefault(rule, {"hit": 0, "miss": 0, "neutral": 0})
            b[r["status"].lower()] += 1
    out = {"computed_at": datetime.now(timezone.utc).isoformat(),
           "engine_version": ENGINE_VERSION, "min_n_required": MIN_N_FOR_ACCURACY,
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
            entry["reason"] = f"нужно ≥{MIN_N_FOR_ACCURACY}, есть {n}"
        out["rules"][rule] = entry
    os.makedirs(CACHE, exist_ok=True)
    with open(ACCURACY_FILE, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    ready = sum(1 for v in out["rules"].values() if v["enough_data"])
    print(f"Правил: {len(out['rules'])} · с выборкой: {ready}")


def run(only=None):
    print(f"=== Decision Engine v{ENGINE_VERSION} ===\n")
    sig = load_all_signals()
    if not sig["phase_analysis"]:
        print("⚠ phase_analysis.json отсутствует — сначала запусти phase_analyzer\n")
    tokens = only or discover_tokens()
    if not tokens:
        print("✗ Не найдено скан-файлов"); return
    now = datetime.now(timezone.utc)
    mult, reg_name, reg_score, reg_why = regime_multiplier(sig)
    print(f"Режим рынка: {reg_name} · ×{mult}")
    print(f"   {reg_why}\n")

    decisions, logged, suspicious, actionable = {}, 0, 0, 0
    for t in tokens:
        d = decide(t, sig)
        decisions[t] = d
        if d["action"] == "ДАННЫЕ ПОДОЗРИТЕЛЬНЫ": suspicious += 1
        elif d["size_pct"] > 0: actionable += 1
        if log_decision(d, now): logged += 1
        size = f"{d['size_pct']}%" if d["size_pct"] else "—"
        flow = d.get("flow_regime") or "—"
        print(f"  {t:8} {d['action']:22} {size:>5}  фаза: {(d.get('stage') or '—'):28} поток: {flow}")

    out = {"computed_at": now.isoformat(), "engine_version": ENGINE_VERSION,
           "verify_after_days": VERIFY_AFTER_DAYS,
           "regime": {"name": reg_name, "score": reg_score,
                      "multiplier": mult, "why": reg_why},
           "tokens_evaluated": len(decisions), "tokens_actionable": actionable,
           "tokens_suspicious_data": suspicious,
           "rules_status": "HYPOTHESIS · пороги вручную, на истории не проверены",
           "decisions": decisions}
    os.makedirs(CACHE, exist_ok=True)
    with open(DECISIONS_FILE, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    print(f"\n  Оценено: {len(decisions)} · actionable: {actionable} · отвергнуто: {suspicious} · записано: {logged}")
    print(f"\n✓ {DECISIONS_FILE}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--token", type=str, default="")
    a = ap.parse_args()
    if a.verify: verify()
    else:
        only = [s.strip().upper() for s in a.token.split(",") if s.strip()] or None
        run(only)
