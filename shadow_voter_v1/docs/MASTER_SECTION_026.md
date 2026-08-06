# §0.26 · SHADOW-ФАЗА ДЛЯ НОВЫХ МОДУЛЕЙ — ЭМПИРИЧЕСКАЯ КАЛИБРОВКА ПЕРЕД LIVE

> **Куда вставить:** в `/mnt/project/STRK_MASTER_INSTRUCTION.md` после существующей секции §0.25 (АВТОМАТИЧЕСКИЙ ПОСТ-МОРТЕМ), перед §0.3 (АРХИТЕКТУРА ПРАВИЛ).
>
> **Версия MASTER:** новую версию проставить после вставки (текущая ≥ v1.12 → бампнуть до v1.13).

---

```
ФИЛОСОФИЯ: любой новый детектор, коллектор или модуль, влияющий или
претендующий влиять на DECISION-контур, ОБЯЗАН пройти shadow-фазу.

Ошибка проекта, которую эта секция закрывает:
  · setup_score → включён в decision → потом backtest показал inverse
    поведение → переименован в extension_index и flipped.
  · funding_module → suppressed +4.48% short-squeeze move → переписан
    после обнаружения.
  · random forest classifier → in-sample AUC 1.000, walk-forward 0.478
    (хуже случайного) → отклонён после включения.
  · liquidity_shift (v1) → добавлен в digest и HTML как «7-й голос»
    БЕЗ фактического голосования нигде → orphan collector.

Общий вывод: включать модуль в decision без measured precision =
принимать decision-решения на непроверенных гипотезах.

┌─────────────────────────────────────────────────────────────────┐
│ ЧТО ТАКОЕ SHADOW-ФАЗА                                            │
└─────────────────────────────────────────────────────────────────┘

Новый модуль-кандидат в voter'ы:
  1. Пишет свой vote в data/history/shadow_votes.jsonl каждый RUN.
  2. НЕ читается ни confluence_gate, ни composite_detector_v2, ни
     scenario_engine, ни decision_layer, ни interpretation_layer.
  3. Показывается в digest ТОЛЬКО в отдельном блоке
     «🔬 SHADOW VOTERS» с явной плашкой HYPOTHESIS.
  4. Через 72h и 7d параллельно auto-postmortem закрывает запись:
     · fetch STRK-USDT D1 close на verify_after
     · pct_change vs issued_price
     · outcome_signal ∈ {RALLY, CRASH, NEUTRAL}
       (пороги из voter_config.json, тоже HYPOTHESIS)
     · per-voter outcome: HIT / MISS / SKIP

┌─────────────────────────────────────────────────────────────────┐
│ КРИТЕРИИ ВКЛЮЧЕНИЯ В LIVE VOTER                                  │
└─────────────────────────────────────────────────────────────────┘

Модуль переходит из shadow в live ТОЛЬКО когда ВСЕ выполнены:

  ✓ N_directional (HIT + MISS, без SKIP) ≥ 15 на окне 72h
  ✓ N_directional ≥ 15 на окне 7d
  ✓ precision_72h ≥ 55%
  ✓ precision_7d ≥ 55%
  ✓ Нет monotonic degrade: последние 5 directional outcomes ≠ все MISS

Если 72h и 7d расходятся (например 72h prec=70%, 7d prec=40%):
  → окно значимо → включать только в тот window, где precision выше
  → задокументировать причину в notes voter_config.json

┌─────────────────────────────────────────────────────────────────┐
│ ПОРОГИ VOTER'ов — WHERE                                          │
└─────────────────────────────────────────────────────────────────┘

Все пороги вынесены в config/voter_config.json с меткой:
  "_meta": {"status": "HYPOTHESIS", ...}

Изменение порогов ДО калибровки = ok (мы ещё калибруем).
Изменение порогов ПОСЛЕ live-inclusion = требует новой shadow-фазы
для этого модуля с нуля (новые пороги = новая гипотеза).

┌─────────────────────────────────────────────────────────────────┐
│ SHADOW-МОДУЛИ КАК ЧАСТЬ КОНТУРА B (ОБУЧЕНИЕ)                    │
└─────────────────────────────────────────────────────────────────┘

Shadow-voter's — это КОНТУР B (обучение):
  · shadow_voter.py         → пишет observations
  · shadow_postmortem.py    → закрывает observations через факт
  · calibration_report.py   → извлекает знание из observations

Они НЕ участвуют в КОНТУРЕ A (решение):
  · composite_detector_v2   ← не читает shadow_votes.jsonl
  · confluence_gate         ← не читает shadow_votes.jsonl
  · scenario_engine         ← не читает shadow_votes.jsonl
  · decision_layer          ← не читает shadow_votes.jsonl
  · interpretation_layer    ← не читает shadow_votes.jsonl

Автоматическая проверка (grep):
  grep -r "shadow_votes.jsonl" scripts/detectors/ scripts/scenario_engine.py scripts/detectors/decision_layer.py

  Должен возвращать ТОЛЬКО:
    scripts/detectors/shadow_voter.py         (writer)
    scripts/detectors/shadow_postmortem.py    (reader-writer, но не для DECISION)
    scripts/calibration_report.py             (reader только)

  Если grep находит совпадение в composite_detector_v2 / confluence_gate /
  scenario_engine / decision_layer / interpretation_layer — это НАРУШЕНИЕ
  дисциплины КОНТУР A vs B. Разбирать и удалять.

┌─────────────────────────────────────────────────────────────────┐
│ РАСШИРЕНИЕ §0.25 · AUTO POSTMORTEM ЗАКРЫВАЕТ ОБА ТИПА FORECAST  │
└─────────────────────────────────────────────────────────────────┘

Существующий auto_postmortem.py по §0.25 закрывает real forecasts
из STRK_FORWARDTEST_LOG.md (schema v2.0).

Новый shadow_postmortem.py закрывает shadow forecasts из
data/history/shadow_votes.jsonl (отдельная schema).

Оба вызываются в composite job workflow:
  1. auto_postmortem.py       → real forecasts (ШАГ 0.7)
  2. shadow_postmortem.py     → shadow forecasts
  3. shadow_voter.py          → пишет новые shadow_votes для этого RUN

Порядок важен: сначала закрываем старое, потом пишем новое.

┌─────────────────────────────────────────────────────────────────┐
│ ЛОВУШКИ SHADOW-ФАЗЫ                                              │
└─────────────────────────────────────────────────────────────────┘

⛔ Включить voter в live ДО достижения N=15 «потому что визуально сигнал
   выглядит правильно» — запрещено. Empirical evidence или ничего.

⛔ Изменить порог voter'а в config, увидев несколько MISS подряд,
   БЕЗ переоценки предыдущих forecasts — запрещено. Один воутер с
   меняющимися порогами = невалидная выборка. Меняешь порог = новая
   shadow-фаза для этого воутера, старые записи помечаются
   config_version и в calibration не смешиваются.

⛔ Считать SHADOW_RALLY_STRONG / SHADOW_CRASH_STRONG сигналом к действию.
   Никакая агрегация shadow-голосов НЕ является decision. Только после
   индивидуальной калибровки каждого voter'а можно рассматривать
   вопрос об агрегации в реальный DECISION.

⛔ Показывать shadow-блок в digest без плашки HYPOTHESIS и без
   отделения от блоков DECISION / CONFLUENCE — риск смешения в
   восприятии.

⛔ Забыть что config/voter_config.json тоже часть эксперимента.
   Каждое изменение порогов = calibration reset для затронутых voter'ов.
```

---

## Что вставляется в CHANGELOG в конце MASTER_INSTRUCTION.md

```
- v1.13 · 06.08.2026 — добавлена §0.26 Shadow-фаза. Все пять кандидатов
  (liquidity_shift, bridge_activity, cross_token, cvd_analysis, effort_result)
  переведены в shadow. Real DECISION не изменён. Первая калибровка
  доступна после N=15 closed forecasts на окне 72h (~4 дня при 6h cadence)
  и 7d (~10 дней).
```
