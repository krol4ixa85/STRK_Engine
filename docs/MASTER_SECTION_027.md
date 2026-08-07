# §0.27 · HISTORY LAYER + COVERT FLOW (V1)

> **Куда вставить:** в `/mnt/project/STRK_MASTER_INSTRUCTION.md` после §0.26
> (Shadow-фаза), перед §0.3.
>
> **Версия MASTER:** бампнуть до v1.14 после вставки.

---

## §0.27 · HISTORY LAYER: единая линия для бэктестинга

```
ФИЛОСОФИЯ:
  Каждый RUN оставляет одну строку — компактный снапшот всех сигналов —
  в data/history/all_history.jsonl. Через verify_after (72h + 7d)
  автоматически закрывается outcome по D1 close цене.

  ЗАЧЕМ:
  · Одна линия для любого будущего бэктеста
  · Единый run_id связывает: this history record ↔ shadow_votes.jsonl
    ↔ (позже) real forecasts.jsonl
  · Автоматическое накопление без ручного сбора
  · Не дублирует shadow_votes.jsonl — хранит reference по run_id

  ЧЕГО НЕТ И НЕ БУДЕТ:
  · Не влияет на DECISION (КОНТУР A). Просто observer.
  · Не подменяет STRK_FORWARDTEST_LOG.md (это протокольный лог с ручным
    review; all_history — машинная линия для бэктеста).
  · Не дублирует полные данные модулей (только критичные поля).
```

### Состав записи all_history.jsonl

```
{
  "run_id": "hist_<workflow_run>_<num>",
  "timestamp": "2026-08-06T21:51:29Z",
  "price_usd": 0.0259,
  "live_signals": {
    "composite_v2": {direction, strength, confidence, btc_cycle},
    "confluence_gate": {signal, confidence, rally_score, crash_score},
    "wyckoff": {phase, sub_phase, confidence},
    "scenarios": {bull_prob, base_prob, bear_prob},
    "technical": {price, rsi, slope_3d_pct, vol_ratio, high_7d, low_7d},
    "funding": {signal, current_annualized_pct, avg_7d_pct},
    "cex_flow": {signal, net_7d_strk},
    "event_layer": {signal, bullish, bearish},
    "unlock": {signal, days_to_next, next_unlock_strk}
  },
  "shadow_ref": {
    "shadow_run_id": "shadow_YYYYMMDD_HHMM",
    "shadow_issued_at": "...",
    "shadow_signal": "SHADOW_CRASH_WEAK",
    "shadow_rally_votes": 0,
    "shadow_crash_votes": 2
  },
  "verify_windows": ["72h", "7d"],
  "verify_after_72h": "...",
  "verify_after_7d": "...",
  "outcome_72h": null,   // fills history_postmortem.py
  "outcome_7d": null,    // fills history_postmortem.py
  "status": "PENDING" → PARTIAL (одно окно закрыто) → CLOSED (оба закрыты)
}
```

### Что писатель / читатель

| Файл | Читает | Пишет |
|------|--------|-------|
| history_accumulator.py | data/cache/*.json (live), shadow_votes.jsonl (last 72h ref) | all_history.jsonl (append) |
| history_postmortem.py | all_history.jsonl (PENDING/PARTIAL), OKX API | all_history.jsonl (in-place update, `outcome_*` + `status`) |
| Telegram /history | all_history.jsonl (last 5) | — |

### Автоматический grep-check контура A/B

```
grep -r "all_history.jsonl" scripts/detectors/ scripts/scenario_engine.py
```

Должен возвращать **пусто**. all_history — КОНТУР B (обучение).
Никакой decision-модуль его не читает. Если находит совпадение —
дисциплина сломана.

---

## §0.27.2 · COVERT FLOW DETECTOR — 6-й shadow voter

```
ФИЛОСОФИЯ:
  Другой ракурс на seed-адреса: не CEX-flow direction (это whale_monitor),
  не когорта (это cohort_tracker), а «плотность удержания vs распыления»
  через retention % и число уникальных counterparties.

  Читает уже собранные rebra:
    · data/cache/flow_eth_edges.csv        (L1, orchestrator step 1)
    · data/cache/flow_starknet_edges.csv   (L2, orchestrator step 2)

  Не патчит orchestrator, не собирает свои rebra.
  Всё уже собрано существующими collectors.
```

### Классификация seed-адреса

Для каждого не-EXPLICIT seed из flow_seeds.json:

```
ACCUMULATION если:
  · vol_in > vol_out * 1.5     (HYPOTHESIS)
  · retention > 70%             (HYPOTHESIS)
  · unique_cp_in ≥ 3            (HYPOTHESIS)
  · max(vol_in, vol_out) ≥ 100k STRK (пол активности)

DISTRIBUTION если:
  · vol_out > vol_in * 1.5      (HYPOTHESIS)
  · unique_cp_out ≥ 3           (HYPOTHESIS)
  · retention < 0
  · max(vol_in, vol_out) ≥ 100k STRK

INACTIVE если:
  · max(vol_in, vol_out) < 100k STRK

NEUTRAL иначе
```

### Aggregate → overall_signal

```
STRONG_ACCUMULATION  — n_accum ≥ 3 AND n_accum > n_dist * 2
STRONG_DISTRIBUTION  — n_dist ≥ 3 AND n_dist > n_accum * 2
ACCUMULATION         — n_accum > n_dist
DISTRIBUTION         — n_dist > n_accum
NEUTRAL              — иначе
```

### EXPLICIT категории (не анализируем)

- `cex_hot_wallets_known_dynamic` — CEX, поведение известно
- `l1_infrastructure` — StarkGate bridge
- `l2_native` — staking, ecosystem contracts
- `team_and_foundation` — известные адреса
- `custody_and_transit` — transit, не accumulator

Только `watchlist` и другие «неявные» категории попадают в анализ.

### Voter status

`covert_flow` добавлен в `voter_config.json` как 6-й shadow voter.
Автоматически подхватывается `shadow_voter.py`:

```
overall_signal STRONG_ACCUMULATION / ACCUMULATION → RALLY vote
overall_signal STRONG_DISTRIBUTION / DISTRIBUTION → CRASH vote
NEUTRAL / UNKNOWN                                → NEUTRAL vote
```

Пороги (retention 70%, ratio 1.5, cp ≥ 3, min flow 100k) — **все
HYPOTHESIS**. Ничего не гарантирует. Живёт в
`voter_config._meta.covert_flow_detector_params`.

Условие включения в live: те же критерии из §0.26 (N ≥ 15 closed
directional forecasts, precision ≥ 55%).

---

## Что изменяется в §0.25 (auto postmortem)

Ничего. Есть три independent postmortem-a:

| Модуль | Закрывает | Файл target |
|--------|-----------|-------------|
| `auto_postmortem.py` (existing) | Real forecasts из FORWARDTEST_LOG.md | forecasts.jsonl / postmortems.jsonl |
| `shadow_postmortem.py` (v1) | Shadow voter forecasts | shadow_votes.jsonl |
| `history_postmortem.py` (v1) | Compact snapshots | all_history.jsonl |

Все три работают на **тех же OKX D1 candles** для консистентности
outcome классификации. Пороги RALLY/CRASH/NEUTRAL — из
`voter_config._meta.outcome_signal_thresholds` (единый источник).

---

## Что вставляется в CHANGELOG в конце MASTER_INSTRUCTION.md

```
- v1.14 · 06.08.2026 — добавлена §0.27 History Layer + Covert Flow.
  История: all_history.jsonl пишется каждый RUN, закрывается через 72h+7d.
  Covert flow: 6-й shadow voter, читает edges CSV из orchestrator (retention
  + unique counterparties). Real DECISION не изменён. covert_flow пороги
  живут в voter_config, все HYPOTHESIS. Telegram /history показывает
  последние 5 записей.
```
