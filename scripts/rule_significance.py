#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
rule_significance.py · v1.0 · 22.08.2026
STRK ENGINE · отличимо ли правило от случайности

ЗАЧЕМ
-----
`decision_backtest.py` считает попадания правил. Но попадание само по
себе ничего не значит: если базовая линия даёт 25% роста, то правило
с 25% попаданий — это не «слабое правило», это ровно ничего.

Хуже: правил проверено одиннадцать. Лучшее из одиннадцати случайных
всегда выглядит хорошо. Именно это и называется подгонкой, и именно
на это отвечает deflated Sharpe ratio — он спрашивает не «хорош ли
результат», а «хорош ли он НАСТОЛЬКО, чтобы не объясняться тем, что
мы перебрали одиннадцать вариантов».

ЧТО СЧИТАЕТ
-----------
  Sharpe   отношение средней доходности к её разбросу
  PSR      вероятность, что настоящий Sharpe больше нуля
  DSR      та же вероятность С ПОПРАВКОЙ на число проверенных правил
  MinTRL   сколько наблюдений нужно для вывода с 95% уверенностью
  PBO      вероятность, что бэктест обманывает (нужны конфигурации,
           работающие в одни и те же периоды)

ЧИТАТЬ ТАК
----------
  DSR > 0.95   правило отличимо от случайности
  DSR 0.75-0.95 возможно, нужно больше наблюдений
  DSR < 0.75   не отличимо от случайности

ОГОВОРКА · ЭТО НЕ ПОРТФЕЛЬНЫЙ SHARPE
------------------------------------
Здесь Sharpe считается по распределению форвардных доходностей в
точках срабатывания правила, а не по кривой капитала стратегии.
Формулы Бэйли и Лопеса де Прадо выведены для второго. Сравнение
правил между собой корректно — они обработаны одинаково; абсолютные
значения интерпретировать как доходность портфеля нельзя.

ЗАВИСИМОСТЬ
-----------
  pip install purgedcv   (MIT)

ЗАПУСК
------
  python3 scripts/rule_significance.py
"""

import sys, json, importlib.util
import numpy as np
sys.path.insert(0,'scripts')
import purgedcv as pcv

spec=importlib.util.spec_from_file_location('db','scripts/decision_backtest.py')
db=importlib.util.module_from_spec(spec); spec.loader.exec_module(db)

scans = db.load_scans()
HOR = 4  # 4 недели — свинг-горизонт

# (правило, неделя) -> список форвардных доходностей
by_rule = {}
panel = {}   # неделя -> правило -> средняя доходность
weeks_all = set()

for token, scan in scans.items():
    weekly = scan if isinstance(scan, list) else (scan.get("weekly_history") or [])
    if len(weekly) < HOR + 9:
        continue
    for t in range(8, len(weekly) - HOR):
        ret = db.forward_return(weekly, t, HOR)
        if ret is None or abs(ret) > 300:
            continue
        wk = weekly[t].get("week")
        weeks_all.add(wk)
        fired, _st = db.rules_fired_at(weekly, t)
        for r in fired:
            by_rule.setdefault(r, []).append(ret)
            panel.setdefault(wk, {}).setdefault(r, []).append(ret)

# базовая линия — все наблюдения
base = [r for v in by_rule.values() for r in v]
rules = sorted([r for r, v in by_rule.items() if len(v) >= 10])
n_trials = len(by_rule)

print(f"наблюдений: {len(base)} · правил проверено: {n_trials} · с n>=10: {len(rules)}\n")

def sharpe(x):
    x = np.asarray(x, float)
    return float(x.mean() / x.std(ddof=1)) if len(x) > 1 and x.std(ddof=1) > 0 else 0.0

sr_all = [sharpe(by_rule[r]) for r in rules]
var_sr = float(np.var(sr_all, ddof=1)) if len(sr_all) > 1 else 0.0

print(f"{'ПРАВИЛО':26}{'n':>5}{'Sharpe':>9}{'PSR':>8}{'DSR':>8}  вердикт")
print("─"*76)
rows=[]
for r in rules:
    x = np.asarray(by_rule[r], float) / 100.0
    sr = sharpe(x)
    psr = pcv.probabilistic_sharpe_ratio(x, 0.0)
    d = pcv.deflated_sharpe_ratio_full(x, n_trials=n_trials, var_sharpe=var_sr)
    dsr = float(getattr(d, "probability", getattr(d, "dsr", 0.0)))
    verdict = "преимущество" if dsr > 0.95 else ("возможно" if dsr > 0.75 else "не отличимо от случайности")
    rows.append((r, len(x), sr, psr, dsr, verdict))
for r, n, sr, psr, dsr, v in sorted(rows, key=lambda z: -z[4]):
    print(f"{r:26}{n:>5}{sr:>9.3f}{psr:>8.2f}{dsr:>8.2f}  {v}")

# PBO: матрица периоды × конфигурации
wk_sorted = sorted(w for w in weeks_all if w)
mat=[]
for wk in wk_sorted:
    row=[]
    ok=True
    for r in rules:
        vals = panel.get(wk, {}).get(r)
        if not vals: ok=False; break
        row.append(float(np.mean(vals))/100.0)
    if ok: mat.append(row)
M=np.array(mat)
print(f"\nматрица для PBO: {M.shape[0]} недель × {M.shape[1]} правил")
if M.shape[0] >= 8 and M.shape[1] >= 2:
    n_splits = 8 if M.shape[0] < 16 else 16
    pbo = pcv.probability_of_backtest_overfitting(M, n_splits=n_splits)
    val = float(getattr(pbo, "pbo", pbo)) if not isinstance(pbo, float) else pbo
    print(f"PBO = {val:.2f}  ({'высокий риск подгонки' if val>0.5 else 'приемлемо'})")
else:
    print("недостаточно полных недель для PBO")

# сколько наблюдений нужно, чтобы лучшему правилу можно было верить
best = max(rows, key=lambda z: z[2])
x = np.asarray(by_rule[best[0]], float)/100.0
from scipy.stats import skew, kurtosis
need = pcv.min_track_record_length(best[2], 0.0, 0.05, float(skew(x)), float(kurtosis(x, fisher=False)))
print(f"\nлучшее по Sharpe: {best[0]} (n={best[1]}, SR={best[2]:.3f})")
print(f"нужно наблюдений для вывода с 95% уверенностью: {need:.0f} (есть {best[1]})")