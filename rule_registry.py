#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
rule_registry.py · v1.0 · 22.08.2026
STRK ENGINE · слой правил между коллекторами и решением

ЗАЧЕМ
-----
Сейчас в репозитории 32 детектора (16 из них по инвентарю сломаны), и
у каждого своя логика внутри своего же файла. Понять, какое правило
сработало, почему и имеет ли оно право влиять на вердикт — нельзя:
правила живут в коде вперемешку со сбором данных.

Здесь правило — это ДАННЫЕ (config/rules.json), а не код. Формат
списан с детектив-инженерии (Sigma, Elastic detection-rules), где та
же задача решена двадцать лет назад: тысячи детекторов, у каждого
статус зрелости, список требуемых полей и список ложных срабатываний.

ТРИ ПРАВИЛА ЭТОГО СЛОЯ
----------------------
1. Нет данных — правило НЕ голосует. Не «false», не «0», а
   отдельное состояние NO_DATA с указанием, какой именно фичи нет.
   Отсутствие доказательства перестаёт выглядеть как доказательство.

2. Данные протухли — то же самое. Срок годности объявлен в
   config/features.json рядом с адресом фичи, а не зашит в детекторе.

3. Правило голосует, только если ИЗМЕРЕНО и порог пройден
   (policy.min_dsr_to_vote, policy.min_n_to_vote). Написать правило и
   получить голос — теперь разные события.

ЗАПУСК
------
  python3 scripts/rule_registry.py                # все токены
  python3 scripts/rule_registry.py --token LINK   # один
  python3 scripts/rule_registry.py --explain flow.accel_up

ВХОД
----
  config/features.json   контракт коллекторов: имя фичи → адрес в кэше
  config/rules.json      каталог правил
  data/cache/*.json      то, что уже собирают существующие коллекторы

ВЫХОД
-----
  data/cache/rules_fired.json

ЗАВИСИМОСТЬ
-----------
  pip install rule-engine     (BSD-3-Clause)
"""

import os
import sys
import json
import argparse
from datetime import datetime, timezone

try:
    import rule_engine
except ImportError:
    raise SystemExit("ERROR: pip install rule-engine")

CACHE_DIR = "data/cache"
FEATURES_FILE = "config/features.json"
RULES_FILE = "config/rules.json"
TOKENS_FILE = "config/tokens.json"
OUT_FILE = "data/cache/rules_fired.json"

FIRED = "FIRED"
NOT_FIRED = "NOT_FIRED"
NO_DATA = "NO_DATA"
STALE = "STALE"
ERROR = "ERROR"


# ─────────────────────────────────────────────────────────────
# загрузка

def load_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def dig(obj, dotted):
    cur = obj
    for part in dotted.split("."):
        if isinstance(cur, dict):
            if part not in cur:
                return None
            cur = cur[part]
        elif isinstance(cur, list):
            try:
                cur = cur[int(part)]
            except Exception:
                return None
        else:
            return None
    return cur


def parse_utc(s):
    if not isinstance(s, str) or not s:
        return None
    t = s.replace("Z", "+00:00")
    try:
        d = datetime.fromisoformat(t)
    except Exception:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


class FeatureStore:
    """Читает кэши один раз, отдаёт фичи по имени с проверкой срока годности.

    include_globals=True добавляет в набор фичи из секции "globals" —
    те, что не разбиты по токенам (фаза Wyckoff, счёт гейта, HHI).
    """

    def __init__(self, spec, now=None, include_globals=False):
        self.spec = dict(spec.get("features", {}))
        if include_globals:
            g = {k: v for k, v in (spec.get("globals") or {}).items()
                 if isinstance(v, dict)}
            self.spec.update(g)
        self.now = now or datetime.now(timezone.utc)
        self._files = {}
        self.file_age_h = {}

    def _file(self, name):
        if name not in self._files:
            self._files[name] = load_json(os.path.join(CACHE_DIR, name))
        return self._files[name]

    def age_hours(self, fname, stamp_path):
        if fname in self.file_age_h:
            return self.file_age_h[fname]
        d = self._file(fname)
        age = None
        if isinstance(d, dict) and stamp_path:
            ts = parse_utc(dig(d, stamp_path))
            if ts:
                age = (self.now - ts).total_seconds() / 3600.0
        self.file_age_h[fname] = age
        return age

    def get(self, name, token, keep_stale=False):
        """
        → (value, state, reason). state: OK | NO_DATA | STALE

        keep_stale=True возвращает протухшее значение вместе с меткой
        STALE, а не выбрасывает его. Для правил выбрасывать правильно:
        решение на старых данных принимать нельзя. Для СОХРАНЕНИЯ —
        наоборот: выброшенное значение исчезает навсегда, а поток Dune
        задним числом не скачать. Фильтровать надо при анализе, не при
        записи.
        """
        s = self.spec.get(name)
        if not s:
            return None, NO_DATA, f"фича {name} не объявлена в {FEATURES_FILE}"

        d = self._file(s["file"])
        if d is None:
            return None, NO_DATA, f"нет файла {s['file']} — коллектор {s.get('collector', '?')} не отработал"

        age = self.age_hours(s["file"], s.get("stamp"))
        max_age = s.get("max_age_h")
        is_stale = age is not None and max_age and age > max_age
        if is_stale and not keep_stale:
            return None, STALE, f"{s['file']} обновлён {age:.0f} ч назад при сроке {max_age} ч"

        # Табличный источник: выгрузка Dune и подобные, где данные лежат
        # списком строк, а не деревом. Ищем строку по полю match.
        if s.get("rows"):
            rows = dig(d, s["rows"])
            if not isinstance(rows, list):
                return None, NO_DATA, f"в {s['file']} нет таблицы {s['rows']}"
            mf, vf = s.get("match", "token"), s.get("value")
            for r in rows:
                if not isinstance(r, dict):
                    continue
                if str(r.get(mf, "")).upper() == token.upper():
                    v = r.get(vf)
                    if v is not None:
                        return v, (STALE if is_stale else "OK"), (
                            f"возраст {age:.0f} ч" if is_stale else None)
            return None, NO_DATA, f"в {s['file']} нет строки для {token}"

        # Ключи токенов в кэшах не согласованы: где-то LINK, где-то link.
        # Пробуем оба регистра, а не полагаемся на то, как коллектор
        # решил их записать сегодня.
        for key in ("path", "fallback_path"):
            p = s.get(key)
            if not p:
                continue
            for form in (token.upper(), token.lower()):
                v = dig(d, p.replace("{T}", form).replace("{t}", form))
                if v is not None:
                    return v, (STALE if is_stale else "OK"), (
                        f"возраст {age:.0f} ч" if is_stale else None)

        return None, NO_DATA, f"в {s['file']} нет значения для {token}"

    def is_irrecoverable(self, name):
        """Можно ли эту фичу скачать задним числом. False — значит нельзя."""
        return bool((self.spec.get(name) or {}).get("irrecoverable"))

    def names(self):
        return sorted(self.spec)

    def unit(self, name):
        return (self.spec.get(name) or {}).get("unit", "")


# ─────────────────────────────────────────────────────────────
# право голоса

def vote_right(rule, policy):
    """→ (bool, причина словами)"""
    st = rule.get("status")
    if st == "deprecated":
        return False, "правило выключено"
    if st == "measured_fail":
        m = rule.get("measured") or {}
        return False, f"проверено на истории, DSR {m.get('dsr', '?')} — порог не пройден"
    if st in ("draft", "testing"):
        if policy.get("unmeasured_can_vote"):
            return True, "непроверенным разрешено голосовать (policy)"
        return False, "не проверено на истории"
    if st == "measured_pass":
        m = rule.get("measured") or {}
        dsr, n = m.get("dsr"), m.get("n")
        if dsr is None or n is None:
            return False, "статус measured_pass, но нет чисел в measured"
        if dsr < policy.get("min_dsr_to_vote", 0.95):
            return False, f"DSR {dsr} ниже порога {policy['min_dsr_to_vote']}"
        if n < policy.get("min_n_to_vote", 30):
            return False, f"наблюдений {n}, нужно {policy['min_n_to_vote']}"
        return True, f"DSR {dsr} на {n} наблюдениях"
    return False, f"неизвестный статус {st!r}"


# ─────────────────────────────────────────────────────────────
# вычисление

def eval_rule(rule, store, token, policy):
    ctx = {}
    missing = []
    for f in rule.get("requires", []):
        v, state, why = store.get(f, token)
        if state != "OK":
            missing.append({"feature": f, "state": state, "reason": why})
        ctx[f] = v

    can_vote, vote_why = vote_right(rule, policy)

    if missing:
        return {
            "rule": rule["id"],
            "title": rule.get("title"),
            "state": missing[0]["state"],
            "can_vote": False,
            "why": missing[0]["reason"],
            "missing": missing,
            "vote_reason": vote_why,
        }

    try:
        r = rule_engine.Rule(rule["condition"])
        hit = bool(r.matches(ctx))
    except Exception as e:
        return {
            "rule": rule["id"],
            "title": rule.get("title"),
            "state": ERROR,
            "can_vote": False,
            "why": f"условие не вычислилось: {e}",
            "vote_reason": vote_why,
        }

    ev = {}
    if hit:
        for f in rule.get("evidence", rule.get("requires", [])):
            v, state, _ = store.get(f, token)
            if state == "OK":
                ev[f] = {"value": v, "unit": store.unit(f)}

    return {
        "rule": rule["id"],
        "title": rule.get("title"),
        "state": FIRED if hit else NOT_FIRED,
        "side": rule.get("side"),
        "horizon_days": rule.get("horizon_days"),
        "status": rule.get("status"),
        "can_vote": bool(hit and can_vote),
        "vote_reason": vote_why,
        "evidence": ev,
        "measured": rule.get("measured"),
        "note_ru": rule.get("note_ru"),
    }


def tokens_from_config():
    d = load_json(TOKENS_FILE, {})
    if isinstance(d, dict):
        for key in ("tokens", "watchlist", "symbols"):
            v = d.get(key)
            if isinstance(v, list) and v:
                return [x if isinstance(x, str) else x.get("symbol") for x in v]
            if isinstance(v, dict) and v:
                return list(v)
    if isinstance(d, list):
        return [x if isinstance(x, str) else x.get("symbol") for x in d]
    vp = load_json(os.path.join(CACHE_DIR, "volume_profile.json"), {})
    return sorted((vp.get("tokens") or {}))


# ─────────────────────────────────────────────────────────────

def main(only_tokens, explain, quiet):
    spec = load_json(FEATURES_FILE)
    cat = load_json(RULES_FILE)
    if not spec or not cat:
        print(f"  Нет {FEATURES_FILE} или {RULES_FILE}")
        return 1

    policy = cat.get("policy", {})
    rules = cat.get("rules", [])
    store = FeatureStore(spec)

    if explain:
        r = next((x for x in rules if x["id"] == explain), None)
        if not r:
            print(f"  Правила {explain} нет")
            return 1
        can, why = vote_right(r, policy)
        print(f"\n{r['id']} · {r.get('title')}")
        print(f"  статус       {r.get('status')}")
        print(f"  условие      {r['condition']}")
        print(f"  нужны фичи   {', '.join(r.get('requires', []))}")
        print(f"  право голоса {'ДА' if can else 'НЕТ'} — {why}")
        if r.get("measured"):
            print(f"  измерено     {json.dumps(r['measured'], ensure_ascii=False)}")
        if r.get("note_ru"):
            print(f"  примечание   {r['note_ru']}")
        if r.get("falsepositives"):
            for fp in r["falsepositives"]:
                print(f"  ложное срабатывание: {fp}")
        print()
        return 0

    toks = [t.upper() for t in (only_tokens or tokens_from_config()) if t]
    if not toks:
        print("  Нет списка токенов")
        return 1

    voting = sum(1 for r in rules if vote_right(r, policy)[0])
    print("=== Слой правил ===")
    print(f"    правил в каталоге: {len(rules)} · "
          f"имеют право голоса: {voting} · токенов: {len(toks)}\n")

    out, totals = {}, {FIRED: 0, NOT_FIRED: 0, NO_DATA: 0, STALE: 0, ERROR: 0}
    for t in toks:
        res = [eval_rule(r, store, t, policy) for r in rules]
        for r in res:
            totals[r["state"]] = totals.get(r["state"], 0) + 1
        fired = [r for r in res if r["state"] == FIRED]
        votes = [r for r in fired if r["can_vote"]]
        out[t] = {
            "fired": [r["rule"] for r in fired],
            "voting": [r["rule"] for r in votes],
            "rules": res,
        }
        if not quiet:
            nd = sum(1 for r in res if r["state"] in (NO_DATA, STALE))
            names = ", ".join(r["rule"] for r in fired) or "—"
            print(f"  {t:8} сработало {len(fired):>2} · голосуют {len(votes):>2} · "
                  f"нет данных {nd:>2}   {names}")

    print(f"\n  Итого по всем токенам:")
    print(f"    сработало      {totals[FIRED]}")
    print(f"    не сработало   {totals[NOT_FIRED]}")
    print(f"    нет данных     {totals[NO_DATA]}")
    print(f"    протухло       {totals[STALE]}")
    print(f"    ошибка условия {totals[ERROR]}")

    all_votes = sum(len(v["voting"]) for v in out.values())
    print(f"\n  Сработавших правил С ПРАВОМ ГОЛОСА: {all_votes}")
    if all_votes == 0:
        print("  Ни одно сработавшее правило не проходит порог проверки.")
        print("  Это не поломка слоя — это его первый честный ответ.")

    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump({
            "computed_at": datetime.now(timezone.utc).isoformat(),
            "rules_version": cat.get("version"),
            "features_version": spec.get("version"),
            "policy": policy,
            "rules_total": len(rules),
            "rules_with_vote_right": voting,
            "totals": totals,
            "tokens": out,
        }, f, indent=2, ensure_ascii=False)
    print(f"\n✓ {OUT_FILE}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--token", type=str, default="")
    ap.add_argument("--explain", type=str, default="")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()
    only = [x.strip() for x in a.token.split(",") if x.strip()]
    sys.exit(main(only, a.explain, a.quiet))