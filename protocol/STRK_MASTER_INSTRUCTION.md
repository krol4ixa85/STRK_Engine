# STRK ENGINE — MASTER INSTRUCTION
### v1.20 · обновлён 07 августа 2026 · §0.26 Shadow-фаза + §0.27 History Layer + Covert Flow

---

# STRK ENGINE — MASTER INSTRUCTION
### v1.19 · обновлён 02 августа 2026 · RANGE_BAR visualization для intraday context в LIQ

---

# STRK ENGINE — MASTER INSTRUCTION
### v1.10 · обновлён 30 июля 2026 · MUST #16 STRUCTURE/МЕСТО (regime · cycle · phase · VAL/POC/VAH) · синхронизировано с v4.9

---

# STRK ENGINE — MASTER INSTRUCTION
### v1.11 · обновлён 30 июля 2026 · MUST #17-19 · фундаментальное здоровье токена (unlocks, holders, DEX depth) · синхронизировано с v5.0

---

# STRK ENGINE — MASTER INSTRUCTION
### v1.12 · обновлён 30 июля 2026 · таблица чтения skills/ + рефакторинг архитектуры · синхронизировано с v5.1

# **Версия MASTER:** новую версию проставить после вставки (текущая ≥ v1.12 → бампнуть до v1.13).

---

## 0.35 · ТАБЛИЦА ЧТЕНИЯ SKILLS/ — что читать в каждом режиме
ПОВОД: v5.2 рефакторинг вынес историю в STRK_HISTORY.md и очистил
BLOCK_SKILLS от перенесённого. LIQ теперь тянет ~30k символов
методологии вместо 300k.

СТРУКТУРА ФАЙЛОВ (в Project Knowledge):

/mnt/project/
STRK_MASTER_INSTRUCTION.md ~72k · поведение, гейты, протокол RUN, §0.26 shadow, §0.27 history
STRK_BLOCK_SKILLS.md ~49k · методы MUST #17-19 + триггеры + реестр опровергнутого
STRK_HISTORY.md ~36k · аудит инструментов, changelog v4.x, ретроспективы
STRK_REPORT_TEMPLATE.md ~43k · структура блоков отчёта
STRK_REPORT_TEMPLATE.html ~66k · HTML-шаблон
STRK_FORWARDTEST_LOG.md ~7k · машиночитаемый лог прогнозов
STRK_SYSTEM_PROMPT.md ~3k · указатель + hard constraints
skills/
decision_contract.txt ~8.6k FILLED · 11 полей + запреты
structure_mesto.txt ~9.6k FILLED · VAL/POC/VAH + PHASE + CYCLE_BTC
regime.txt ~6.3k FILLED · adx/bb/eff → regime
playbook.txt ~2.2k STUB · указатель на BLOCK_SKILLS
squeeze_hl.txt ~2.4k STUB · указатель на BLOCK_SKILLS
flow_playbook.txt ~2.3k STUB · указатель на BLOCK_SKILLS
forwardtest.txt ~2.5k STUB · указатель на MASTER §0.25
decision_contract.txt ~15k v1.16 · +MAX_SWING Kelly + MUST #12/#13 numeric
structure_mesto.txt ~9.6k v1.0 · FILLED
regime.txt ~6.3k v1.0 · FILLED
watchers.txt ~8k v1.1 · layman-actions
squeeze_hl.txt ~7k v2.0 · +trader_quality_filter
probability_module.txt ~11k v1.0 · вероятности + EV расчёт
scenario_pressure.txt ~42k v1.0 · FILLED · 6/6 + Base/Bull/Bear

┌──────────────────────────────────────────────────────────────┐
│ LIQ · минимальный отчёт │
├──────────────────────────────────────────────────────────────┤
│ ЧИТАЕТ: │
│ · MASTER (~72k) │
│ · skills/decision_contract.txt (~8.6k) │
│ · skills/structure_mesto.txt (~9.6k) │
│ · skills/regime.txt (~6.3k) │
│ · skills/watchers.txt v1.1 (~8k) — layman-actions │
│ ИТОГО ~104k символов ≈ ~26k токенов │
│ │
│ НЕ ЧИТАЕТ: │
│ · BLOCK_SKILLS (~49k) — не нужен для базового LIQ │
│ · HISTORY (~36k) — вообще не для ежедневного │
│ · остальные skills — по событию │
│ │
│ ПО СОБЫТИЮ (если MUST требует свежее закрытие): │
│ · BLOCK_SKILLS §MUST #17-19 — если нужен свежий unlock │
│ · skills/squeeze_hl.txt + BLOCK_SKILLS §Squeeze — MUST #5 │
│ · skills/flow_playbook.txt + BLOCK_SKILLS §FLOW — MUST #6 │
│ │
│ WATCHERS: проверяются перед рендером блока МЕСТО. Уровень L3 │
│ обязывает пометить и рекомендовать полный RUN. │
└──────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────┐
│ RUN · полный отчёт с пост-мортемом │
├──────────────────────────────────────────────────────────────┤
│ ЧИТАЕТ: │
│ · MASTER │
│ · BLOCK_SKILLS (~49k — методы MUST #17-19, триггеры) │
│ · skills/ (3 FILLED + forwardtest для ШАГ 0.7) │
│ · FORWARDTEST_LOG (для пост-мортема) │
│ ИТОГО ~140k символов ≈ ~35k токенов │
│ │
│ НЕ ЧИТАЕТ: │
│ · HISTORY │
│ · scenario_pressure │
└──────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────┐
│ SCENARIO · симуляция │
├──────────────────────────────────────────────────────────────┤
│ ЧИТАЕТ: │
│ · MASTER │
│ · skills/scenario_pressure.txt (сейчас STUB) │
│ │
│ ЗАПРЕЩЕНО: NEW_ENTRY, buy-sell, DECISION-контракт │
│ │
│ ТЕКУЩЕЕ СОСТОЯНИЕ (v1.0): engine готов │
│ При вызове SCENARIO агент рендерит: │
│ · PRESSURE MAP со всеми 6 компонентами │
│ · NEED_SCORE + интерпретация │
│ · Base/Bull/Bear распределение вероятностей 30-90 дней │
│ · Триггеры перехода между сценариями │
└──────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────┐
│ REVIEW / DEV · ретроспектива и правки │
├──────────────────────────────────────────────────────────────┤
│ ЧИТАЮТ: любые файлы, включая HISTORY.md │
│ Правки методов — в skills/*.txt (canonical) │
│ Правки поведения — в MASTER │
└──────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│ ПРАВИЛО РАЗРЕШЕНИЯ КОНФЛИКТА │
└─────────────────────────────────────────────────────────────────┘

Метод CANONICAL в skills/*.txt побеждает копию в BLOCK_SKILLS.
BLOCK_SKILLS больше не содержит дубликатов regime / structure_mesto /
decision_contract — они удалены в v5.2.

Поведенческие правила ТОЛЬКО в MASTER. В skills/ поведение не пишется.

┌─────────────────────────────────────────────────────────────────┐
│ ЭКОНОМИЯ ТОКЕНОВ v5.2 (vs v5.1) │
└─────────────────────────────────────────────────────────────────┘

LIQ до v5.2: MASTER (159k) + BLOCK_SKILLS (142k) = ~75k токенов
LIQ с v5.2: MASTER (72k) + 3 skills (24k) = ~24k токенов
ЭКОНОМИЯ LIQ: ~51k токенов (68%)

RUN до v5.2: MASTER + BLOCK_SKILLS + skills = ~90k токенов
RUN с v5.2: MASTER + BLOCK_SKILLS (легче) + skills = ~35k токенов
ЭКОНОМИЯ RUN: ~55k токенов (61%)

┌─────────────────────────────────────────────────────────────────┐
│ ЭКРАН НЕ ИЗМЕНИЛСЯ │
└─────────────────────────────────────────────────────────────────┘

MUST-CALC 19 пунктов + #0 — все на месте.
DECISION 11 полей — все на месте.
МЕСТО 9 строк — все на месте.
Ссылка «см. прошлый RUN» — по-прежнему запрещена.
Пропуск метрики → NOT_CHECKED в статусе, никогда молча.

text

---

## 0.13 · MUST #17-19 · ФУНДАМЕНТАЛЬНОЕ ЗДОРОВЬЕ ТОКЕНА
ПОВОД: аудит идентифицировал дыру — движок хорошо считает погоду
(regime, phase) и МЕСТО (VAL/POC/VAH), но не проверяет базовое
здоровье актива. Три метрики отсутствовали:
· schedule эмиссии/разлоков — сколько токенов на подходе
· концентрация топ-10 держателей — риск манипуляции одним игроком
· DEX-глубина — сколько $ движет цену на 2%

Эти три вопроса задаёт первым любой профессиональный investment memo.
Без них DECISION про STRK без ответа даже при MUST 14/16 закрытых.

┌─────────────────────────────────────────────────────────────────┐
│ MUST #17 · TOKEN UNLOCKS — расписание давления предложения │
└─────────────────────────────────────────────────────────────────┘

ИСТОЧНИК: CryptoRank get_currency_vesting ЛИБО web-fetch со Starknet
Foundation. По memory для STRK — суточная эмиссия ~127M/мес.

ВЫЧИСЛЯЕТСЯ ЗА КАЖДЫЙ RUN (Ярус A):
next_unlock_date ближайшая дата разлока в ≤90 дней
next_unlock_size размер в STRK и в 
потекущейцене
усреднённаяэмиссия
день
дневноеокно
_daily та же цифра в $ по текущей цене
circulating_now циркулирующее предложение сейчас

СТРОКА В ОТЧЁТЕ:
UNLOCKS: next 
X
@
D
D
.
M
M
(
N
.
N
N
X@DD.MM(N.NNY ·
unlock_pressure=<LOW|MEDIUM|HIGH>

СВЯЗЬ С MUST #12 (unlock pressure):
unlock_pressure = next_unlock_
<0.2 LOW · 0.2-0.6 MEDIUM · >0.6 HIGH

СВЯЗЬ С CONFLICT_GATE:
Unlock pressure HIGH → BEARISH_STACK
Отсутствие крупных разлоков в 30 дней → BULLISH_STACK

ЛОВУШКИ:
⛔ «Разлок = дамп» — наивно. Разлок → stake или → bridge L2 нейтрален.
Разлок → CEX = risk. Требуется маршрут (см. PLAYBOOK_FLOW).
⛔ Игнорировать эмиссию потому что она «маленькая». 127M/мес ×
$0.026 = $3.3M/мес продающего давления. За квартал это $10M
против $170M TVL — 6% размывания в квартал.

┌─────────────────────────────────────────────────────────────────┐
│ MUST #18 · HOLDER CONCENTRATION — риск одного игрока │
└─────────────────────────────────────────────────────────────────┘

ИСТОЧНИК: Nansen token_current_top_holders mode=spot chain=starknet
(или fallback Starkscan top-holders endpoint)

ВЫЧИСЛЯЕТСЯ ЗА КАЖДЫЙ RUN (Ярус A):
top10_pct % supply у топ-10 адресов
top100_pct % supply у топ-100 адресов
foundation_pct % supply у Foundation/multisig адресов
cex_pct % supply на CEX-hot-wallets (учитывать при top10)
effective_top10 top10 минус CEX и Foundation
gini_est индекс Джини по топ-100 (0=равенство, 1=монополия)
active_top10 сколько из top10 двигались за 30 дней

СТРОКА В ОТЧЁТЕ:
HOLDERS: top10=X% (eff Y% ex-CEX/Foundation) · top100=Z% ·
gini=G · active_top10=N/10 · concentration_risk=<LOW|MED|HIGH>

ПОРОГИ:
effective_top10 < 15% → LOW (децентрализованно)
15-30% → MEDIUM (умеренная концентрация)

30% → HIGH (риск манипуляции одним игроком)

СВЯЗЬ С CONFLICT_GATE:
HIGH concentration + active_top10 ≥ 5/10 → BEARISH item
(крупные ходят — риск раздачи)
HIGH concentration + active_top10 ≤ 2/10 → нейтрально
(концентрированно, но неактивно = сон)
LOW concentration → BULLISH item

ЛОВУШКИ:
⛔ Считать CEX hot wallets как «держателей». Это не держатели,
это учётные записи бирж, где реальные владения размазаны.
Всегда вычитать CEX_pct из top10 для effective_top10.
⛔ Считать Foundation как «держателя-медведя». Foundation с
vesting-графиком — известный риск, но его надо оценивать через
MUST #17 (unlocks), а не через MUST #18 (concentration).
⛔ Топ-100 может выглядеть равномерным, а top10 — сильно
концентрированным. Печатать оба значения, не одно.

┌─────────────────────────────────────────────────────────────────┐
│ MUST #19 · DEX LIQUIDITY DEPTH — сколько $ движет цену на 2% │
└─────────────────────────────────────────────────────────────────┘

ИСТОЧНИК: DefiLlama (агрегатор), для точности — прямые запросы
в DEX pools (Ekubo, JediSwap, Nostra на Starknet L2 +
Uniswap V3 STRK/ETH на Ethereum L1).

ВЫЧИСЛЯЕТСЯ ЗА КАЖДЫЙ RUN (Ярус A):
total_dex_tvl_
суммарная
парна
 объём в 
нужныйдля
 объём в 
нужныйдля
легчепадать
легчерасти
 спот-объём CEX 24ч для сравнения
dex_vs_cex_ratio dex_vol_24h / cex_vol_24h

СТРОКА В ОТЧЁТЕ:
LIQUIDITY: DEX TVL 
X
⋅
d
e
p
t
h
±
2
X⋅depth±2Y / ask $Z ·
asymmetry=A · cex/dex ratio=R · fragility=<LOW|MED|HIGH>

ПОРОГИ FRAGILITY:
depth_2pct_bid_$ > $200k → LOW
$50-200k → MEDIUM
< $50k → HIGH (движение 2% доступно розничному ордеру)

СВЯЗЬ С CONFLICT_GATE:
HIGH fragility + fuel_side=LONG_CASCADE_DOWN → BEARISH item
(мало ликвидности снизу + топливо каскада = движение самоусиляется)
HIGH fragility + fuel_side=SHORT_SQUEEZE_UP → BULLISH item
Asymmetry > 2 → BEARISH (легче упасть чем вырасти)

СВЯЗЬ С MUST #13 (R = liq/vol):
MUST #13 = соотношение принудительных ликвидаций к объёму.
MUST #19 = глубина книги ДЛЯ добровольного объёма.
Разные метрики. Обе нужны:
R высокий + fragility LOW = каскад в толстой книге, поглотится
R высокий + fragility HIGH = каскад в тонкой книге, самоусиляется

ЛОВУШКИ:
⛔ Складывать TVL всех пулов как «общую ликвидность». TVL ≠ depth.
$5M пул с концентрацией у $0.05 не даёт глубины у текущей цены.
Считать depth в конкретной ценовой зоне ±2% от mark.
⛔ Не учитывать разницу L1 vs L2. STRK/ETH на Uniswap V3 (L1) и
STRK/USDC на Ekubo (L2) — две разные книги. Может быть
фрагментация: на L1 глубоко, на L2 тонко.
⛔ Не путать DEX TVL со staking TVL. DefiLlama показывает суммарно,
но staking — не торговая ликвидность.

text

┌─────────────────────────────────────────────────────────────────┐
│ РАСШИРЕННЫЙ БЛОК МЕСТО (v1.11)                                  │
└─────────────────────────────────────────────────────────────────┘

К шести строкам МЕСТО из v1.10 добавляются три строки здоровья:

  1. REGIME     (из #15)
  2. CYCLE_BTC
  3. PHASE
  4. STRUCTURE  VAL/POC/VAH
  5. POSITION
  6. → DECISION
  ─── здоровье токена (v1.11) ───
  7. UNLOCKS    (#17)
  8. HOLDERS    (#18)
  9. LIQUIDITY  (#19)

Все 9 строк — обязательные. Пустые или NOT_CHECKED допустимы,
но должны быть напечатаны явно.

---

## 0.15 · MUST #16 · STRUCTURE / МЕСТО — карта, где мы стоим
ПОВОД: после инвалидации PLAYBOOK Falling Wedge в R73 отчёт правильно
сказал NO, но пропала карта места — не осталось якорей VAL/POC/VAH,
фаза упала в WHY, цикл BTC не отличался от Regime. Читатель получал
«туман NO» вместо «ждём вот это».

ФИЛОСОФИЯ: Regime = какая погода. PHASE = какой отрезок сценария внутри.
VAL/POC/VAH = где в комнате стоим. Все три — разные вопросы, не сливать.
После смерти линии PLAYBOOK структура ОБЯЗАНА обновиться или быть
явно NOT_CHECKED — не позволительно оставлять мёртвый якорь как
единственный ориентир.

┌─────────────────────────────────────────────────────────────────┐
│ MUST #16 · STRUCTURE / МЕСТО — обязательный блок после DECISION │
└─────────────────────────────────────────────────────────────────┘

Печатается КАЖДЫЙ RUN и КАЖДЫЙ LIQ, сразу после DECISION.
Пустой блок или «см. прошлый RUN» — ЗАПРЕЩЕНО.

Шесть строк (все шесть, даже если NOT_CHECKED):

REGIME <trending_up|trending_down|ranging|volatile|quiet>
· adx=X · bb=Y · eff=Z · conf=C · size_mult=M
(уже вычисляется в MUST #15, здесь только строкой)

CYCLE_BTC <UP|NEUTRAL|DOWN> vs MA200 · slope · cycle_mult=M
(BTC против своего MA200; не путать с REGIME для STRK)

PHASE <B|C|D|E> · одна фраза «почему»
B — база / баланс внутри VA
C — сжатие / пружина у края VA
D — выход из VA с удержанием (после ретеста в ranging)
E — разгон / тренд после D

STRUCTURE VAL=
X
⋅
P
O
C
=
X⋅POC=Y · VAH=$Z
ИЛИ STRUCTURE: NOT_CHECKED (метод не считался этот RUN)

POSITION цена $X · до VAL: −A% · до инвалидации: −B% · R:R = Z
(если плана нет — «плана нет, R:R не вычисляется»)

DECISION → см. блок DECISION выше (не дублировать вердикт)

┌─────────────────────────────────────────────────────────────────┐
│ ЖЁСТКИЕ ПРАВИЛА │
└─────────────────────────────────────────────────────────────────┘

· REGIME и CYCLE_BTC — ДВЕ РАЗНЫЕ строки. Не сливать в одну.
Пример допустимого расхождения:
CYCLE_BTC = UP (капитал в риске)
REGIME (STRK) = ranging (у STRK не транслируется в тренд)

· PHASE ≠ REGIME. Regime — 90-дневная погода. PHASE — отрезок внутри
текущей волны накопления/разгона. STRK может 90 дней быть ranging и
проходить B→C→D→E фазы внутри этого ranging.

· Переход C → D разрешён ТОЛЬКО при:

close за VAH (лонг) или за VAL (шорт), И

в ranging режиме дополнительно — удержанный ретест (правило v4.8)
Без обоих условий → остаётся C (или откат в B).

· После инвалидации PLAYBOOK-линии STRUCTURE ОБЯЗАНА обновиться:

новая VA / новая база рассчитаны, ИЛИ

явное NOT_CHECKED с планом пересчёта.
Мёртвая линия (например, wedge $0.02799 после инвалидации Jul-28)
НЕ подменяет структурный якорь.

· NEW_ENTRY = YES при STRUCTURE = NOT_CHECKED И одновременно
«тайминг = не догон» — ЗАПРЕЩЕНО. Тайминг без уровней не доказан:
«не догон» относительно чего?

· В CONFLICT_GATE добавляются пункты:

PHASE=B/C + попытка D-входа без уровней → BEARISH/CONFLICT item
(тайминг не подтверждён)

ranging + breakout без ретеста — уже conflict-item (v4.8, сохранено)

┌─────────────────────────────────────────────────────────────────┐
│ ЛОВУШКИ │
└─────────────────────────────────────────────────────────────────┘

⛔ POC как «зона входа» — уже в реестре опровергнутого. POC = центр
стоимости, не сигнал направления. Вход у POC = вход в шум.

⛔ «Не догон» без VAL/VAH = пустая фраза. Догон относительно чего?
Требуется конкретное расстояние в % до опорного уровня.

⛔ Мёртвая линия PLAYBOOK после MISS не подменяет STRUCTURE.
Если clean-invalidation произошёл — новая карта или NOT_CHECKED.

⛔ Копировать VAL/POC/VAH из прошлого RUN без пересчёта разрешено
ТОЛЬКО с провенансом «VAL/POC/VAH из R__». Одно унаследованное
значение недостаточно для открытия NEW_ENTRY.

⛔ PHASE без якорной цены (что именно балансирует / сжимается) =
пустая метка. Не писать PHASE=B без VAL/POC/VAH или без явного
«база формируется вокруг $X».

┌─────────────────────────────────────────────────────────────────┐
│ ЧАСТОТА ПЕРЕСЧЁТА │
└─────────────────────────────────────────────────────────────────┘

Полный VA (VAL/POC/VAH по 90D) считается:
· каждые 2-3 RUN, ИЛИ
· при смене REGIME, ИЛИ
· при инвалидации PLAYBOOK-линии
Между пересчётами — значения с провенансом «из R__».
CYCLE_BTC — каждый RUN дёшево (одна тulipвитая ссылка на TradingView / bash).
PHASE — каждый RUN как enum, требует ре-оценки.

text

---

## 0.2 · REGIME CLASSIFIER — MUST #15 + модификатор всех решений
ИСТОЧНИК: Market_regime_classifier.md (файл в Project Knowledge)
ЗАПУСК: каждый RUN, ЯРУС A, обязательно
МЕТОД: ADX(14) + BB width(20) + MA alignment(10/20/50) + efficiency ratio
ВЫХОД: один из 5 режимов + confidence + risk_mult

5 РЕЖИМОВ И ИХ ВЛИЯНИЕ:

trending_up ADX>25, ma_aligned_up, efficiency>0.3
→ SPEC-лонги работают, множитель размера ×1.0
→ стопы стандартные, hold_longer=True
→ PLAYBOOK-пробой можно брать без ретеста

trending_down ADX>25, ma_aligned_down, efficiency>0.3
→ SPEC-ЛОНГИ ПРОТИВ РЕЖИМА, множитель размера ×0.3
→ если DECISION хочет YES при этом режиме — WHY должен
объяснять, почему берём контр-трендовый сетап
→ PLAYBOOK-пробой ВВЕРХ = крайне подозрителен

ranging adx<25 ИЛИ ma не aligned, efficiency<0.3
→ mean reversion работает лучше breakout
→ множитель размера ×0.8
→ PLAYBOOK-пробой ТРЕБУЕТ УДЕРЖАННЫЙ РЕТЕСТ (без ретеста NO)
→ чаще ложные пробои: false-breakout-rate ~60% в ranging
→ это стандартный режим STRK последние 90 дней

volatile bb_width>3.0, adx<20
→ множитель размера ×0.5, стопы ×1.5 шире
→ hold_longer=False, ранняя фиксация
→ PLAYBOOK-пробой можно, но с расширенным стопом

quiet bb_width<1.0, adx<15
→ компрессия перед расширением
→ множитель размера ×0.5 (готовим объём, не входим)
→ PLAYBOOK-пробой ЖДЁМ, не входим на предвкушении

┌─────────────────────────────────────────────────────────────────┐
│ MUST #15 · Regime + confidence │
└─────────────────────────────────────────────────────────────────┘
Пункт добавлен в MUST-CALC как обязательный. Печатается строкой:
REGIME: <режим> · adx=X · bb_width=Y · eff=Z · conf=W · size_mult=M
Открыт (не вычислен) → NEW_ENTRY не YES.

┌─────────────────────────────────────────────────────────────────┐
│ REGIME_MULT — 5-й множитель формулы размера │
└─────────────────────────────────────────────────────────────────┘
Было: size = MAX_SWING × pillar × cycle × tac_DG
Стало: size = MAX_SWING × pillar × cycle × tac_DG × REGIME_MULT

trending_up/down × 1.0 (полный размер в свою сторону; ×0.3 против)
ranging × 0.8
volatile × 0.5
quiet × 0.5

┌─────────────────────────────────────────────────────────────────┐
│ REGIME_GATE — новое условие в CONFLICT_GATE │
└─────────────────────────────────────────────────────────────────┘
Стек BULLISH_STACK получает пункт "regime = trending_up + PLAYBOOK-пробой".
Стек BEARISH_STACK получает пункт "regime = trending_down".
Пункт "PLAYBOOK-пробой в ranging БЕЗ ретеста" НЕ считается bullish;
считается conflict-item.

┌─────────────────────────────────────────────────────────────────┐
│ КАЛИБРОВКА ПОД STRK (эмпирическая, требует ≥15 случаев) │
└─────────────────────────────────────────────────────────────────┘
STRK 90 дней (Jul-28): режим = ranging (adx=22, bb=8.31, eff=0.20).
За весь период НИ ОДНОГО дня trending_up.
Импликация: базовый уклон системы под STRK — mean reversion в диапазоне,
breakout-стратегии работают хуже. Это ПОДТВЕРЖДАЕТ решение отклонить
торговые сигналы на PLAYBOOK-пробое без ретеста.

text

---

## 0.25 · АВТОМАТИЧЕСКИЙ ПОСТ-МОРТЕМ — оценка прошлых прогнозов без напоминания
ФИЛОСОФИЯ: система должна САМА оценивать свои прогнозы через verify_after,
без внешнего напоминания пользователя. Иначе форвард-тест деградирует
до "хороших воспоминаний" — засчитываются попадания, забываются промахи.

┌─────────────────────────────────────────────────────────────────┐
│ ФОРМАТ ЗАПИСИ ПРОГНОЗА (STRK_FORWARDTEST_LOG.md, машиночитаемо) │
└─────────────────────────────────────────────────────────────────┘

FORECAST R__
id: R__ (номер RUN, в котором прогноз сделан)

issued_at: YYYY-MM-DD HH:MM UTC

verify_after: YYYY-MM-DD HH:MM UTC (дата, ранее которой не оценивать)

verify_before: YYYY-MM-DD HH:MM UTC (дата истечения; после = EXPIRED)

prediction: одна строка, конкретная (цена/зона/событие)

falsification: дословный критерий опровержения (числовой порог)

context: 1-2 строки — какой сигнал был основанием

status: PENDING | HIT | MISS | PARTIAL | EXPIRED_UNCLEAR

evaluated_at: null | YYYY-MM-DD HH:MM UTC

outcome: null | конкретное значение цены/события в момент оценки

notes: 0-3 строки при оценке

СТАТУСЫ:
PENDING verify_after ещё не наступил
HIT прогноз подтверждён по критерию
MISS критерий фальсификации сработал ДО verify_after
ИЛИ верификация показала противоположное
PARTIAL часть прогноза подтверждена, часть нет
EXPIRED_UNCLEAR verify_before прошёл, оценка невозможна из-за данных

┌─────────────────────────────────────────────────────────────────┐
│ ШАГ 0.7 в протоколе RUN — автоматический пост-мортем │
└─────────────────────────────────────────────────────────────────┘

Выполняется В КАЖДОМ RUN, ПЕРЕД сбором Яруса A:

view /mnt/project/STRK_FORWARDTEST_LOG.md

Найти все прогнозы с status=PENDING И verify_after ≤ сегодня

Для каждого:
а) взять цену в момент verify_after (OHLCV OKX, 1D бары)
б) применить критерий falsification из записи прогноза
в) определить outcome: HIT / MISS / PARTIAL / EXPIRED_UNCLEAR
г) записать evaluated_at = текущее время RUN
д) записать outcome с конкретным числом
е) сменить status с PENDING на итоговый

Если хотя бы один прогноз переоценён — печать в отчёт:
"Пост-мортем R__: <PENDING → HIT/MISS>, прогноз "<строка>",
outcome $X.XXX"

Дополнительно: если MISS-прогнозы имеют паттерн (несколько подряд
промахов одного типа сигнала) — печать в WHY_ONE_LINE
"калибровка: <сигнал> дал N MISS подряд, доверие снижено"

┌─────────────────────────────────────────────────────────────────┐
│ АНТИ-ЛОВУШКИ ПОСТ-МОРТЕМА (уроки прошлых сессий) │
└─────────────────────────────────────────────────────────────────┘

⛔ НЕ засчитывать HIT раньше verify_after даже если "уже видно, что
прогноз сбылся". Правило v3.7: оценка не раньше нижней границы окна.
Причина: R59→R60 дефект, когда прогноз засчитали через сутки
в свою пользу до истечения окна.

⛔ НЕ переписывать уже оценённые записи (status ≠ PENDING). Задним
числом изменение outcome запрещено.

⛔ НЕ пропускать MISS "по объективным причинам" (типа "рынок был странный").
Критерий фальсификации фиксирован при выдаче прогноза.

⛔ НЕ считать EXPIRED_UNCLEAR как HIT. Отсутствие данных ≠ подтверждение.

⛔ Прогноз БЕЗ falsification-критерия НЕ ЗАПИСЫВАЕТСЯ в лог. Прогноз
"может пойти вверх" — не прогноз, а gestures. Требование: числовой
порог + временное окно.

┌─────────────────────────────────────────────────────────────────┐
│ ОБЯЗАТЕЛЬНОСТЬ ФОРМАТА ДЛЯ НОВЫХ ПРОГНОЗОВ │
└─────────────────────────────────────────────────────────────────┘

Каждый RUN, выдавая MONITOR_72h с прогнозным элементом, ЗАПИСЫВАЕТ
новый FORECAST в STRK_FORWARDTEST_LOG.md с полными полями. Прогнозы
без записи в лог считаются несуществующими (нельзя потом "вспомнить",
что предсказывал).

Пример свежего прогноза, сделанного правильно:
FORECAST R73

issued_at: 2026-07-28 17:30 UTC

verify_after: 2026-08-04 17:30 UTC (7 дней)

verify_before: 2026-08-11 17:30 UTC

prediction: STRK не восстанавливается выше $0.030 D1 close

falsification: если любой D1 close > $0.0300 до verify_after
→ прогноз опровергнут (MISS)

context: regime=ranging, wedge инвалидирован Jul-28,
fuel_side=LONG_CASCADE_DOWN

status: PENDING

text
---

## §0.26 · SHADOW-ФАЗА ДЛЯ НОВЫХ МОДУЛЕЙ — ЭМПИРИЧЕСКАЯ КАЛИБРОВКА ПЕРЕД LIVE
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
│ ЧТО ТАКОЕ SHADOW-ФАЗА │
└─────────────────────────────────────────────────────────────────┘

Новый модуль-кандидат в voter'ы:

Пишет свой vote в data/history/shadow_votes.jsonl каждый RUN.

НЕ читается ни confluence_gate, ни composite_detector_v2, ни
scenario_engine, ни decision_layer, ни interpretation_layer.

Показывается в digest ТОЛЬКО в отдельном блоке
«🔬 SHADOW VOTERS» с явной плашкой HYPOTHESIS.

Через 72h и 7d параллельно auto-postmortem закрывает запись:
· fetch STRK-USDT D1 close на verify_after
· pct_change vs issued_price
· outcome_signal ∈ {RALLY, CRASH, NEUTRAL}
(пороги из voter_config.json, тоже HYPOTHESIS)
· per-voter outcome: HIT / MISS / SKIP

┌─────────────────────────────────────────────────────────────────┐
│ КРИТЕРИИ ВКЛЮЧЕНИЯ В LIVE VOTER │
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
│ ПОРОГИ VOTER'ов — WHERE │
└─────────────────────────────────────────────────────────────────┘

Все пороги вынесены в config/voter_config.json с меткой:
"_meta": {"status": "HYPOTHESIS", ...}

Изменение порогов ДО калибровки = ok (мы ещё калибруем).
Изменение порогов ПОСЛЕ live-inclusion = требует новой shadow-фазы
для этого модуля с нуля (новые пороги = новая гипотеза).

┌─────────────────────────────────────────────────────────────────┐
│ SHADOW-МОДУЛИ КАК ЧАСТЬ КОНТУРА B (ОБУЧЕНИЕ) │
└─────────────────────────────────────────────────────────────────┘

Shadow-voter's — это КОНТУР B (обучение):
· shadow_voter.py → пишет observations
· shadow_postmortem.py → закрывает observations через факт
· calibration_report.py → извлекает знание из observations

Они НЕ участвуют в КОНТУРЕ A (решение):
· composite_detector_v2 ← не читает shadow_votes.jsonl
· confluence_gate ← не читает shadow_votes.jsonl
· scenario_engine ← не читает shadow_votes.jsonl
· decision_layer ← не читает shadow_votes.jsonl
· interpretation_layer ← не читает shadow_votes.jsonl

Автоматическая проверка (grep) при audit:
grep -r "shadow_votes.jsonl" scripts/detectors/ scripts/scenario_engine.py scripts/detectors/decision_layer.py

Должен возвращать ТОЛЬКО:
scripts/detectors/shadow_voter.py (writer)
scripts/detectors/shadow_postmortem.py (reader-writer, но не для DECISION)
scripts/calibration_report.py (reader только)

Если grep находит совпадение в composite_detector_v2 / confluence_gate /
scenario_engine / decision_layer / interpretation_layer — это НАРУШЕНИЕ
дисциплины КОНТУР A vs B. Разбирать и удалять.

┌─────────────────────────────────────────────────────────────────┐
│ РАСШИРЕНИЕ §0.25 · AUTO POSTMORTEM ЗАКРЫВАЕТ ОБА ТИПА FORECAST │
└─────────────────────────────────────────────────────────────────┘

Существующий auto_postmortem.py по §0.25 закрывает real forecasts
из STRK_FORWARDTEST_LOG.md (schema v2.0).

Новый shadow_postmortem.py закрывает shadow forecasts из
data/history/shadow_votes.jsonl (отдельная schema).

Оба вызываются в composite job workflow:

auto_postmortem.py → real forecasts (ШАГ 0.7)

shadow_postmortem.py → shadow forecasts

shadow_voter.py → пишет новые shadow_votes для этого RUN

Порядок важен: сначала закрываем старое, потом пишем новое.

┌─────────────────────────────────────────────────────────────────┐
│ ЛОВУШКИ SHADOW-ФАЗЫ │
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

text

---

## §0.27 · HISTORY LAYER + COVERT FLOW DETECTOR
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

┌─────────────────────────────────────────────────────────────────┐
│ СОСТАВ ЗАПИСИ ALL_HISTORY.JSONL │
└─────────────────────────────────────────────────────────────────┘

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
"outcome_72h": null, // заполняет history_postmortem.py
"outcome_7d": null, // заполняет history_postmortem.py
"status": PENDING → PARTIAL (одно закрыто) → CLOSED (оба закрыты)
}

┌─────────────────────────────────────────────────────────────────┐
│ КТО ПИШЕТ / КТО ЧИТАЕТ │
└─────────────────────────────────────────────────────────────────┘

Файл Читает Пишет
──────────────────────────────────────────────────────────────────────────────
history_accumulator.py data/cache/*.json (live) all_history.jsonl
shadow_votes.jsonl (ref) (append)
history_postmortem.py all_history.jsonl (PENDING) all_history.jsonl
OKX API (in-place update)
Telegram /history all_history.jsonl (last 5) —

Автоматическая проверка (grep):
grep -r "all_history.jsonl" scripts/detectors/ scripts/scenario_engine.py

Должен возвращать пусто. all_history — КОНТУР B (обучение).
Никакой decision-модуль его не читает. Если находит совпадение —
дисциплина сломана.

┌─────────────────────────────────────────────────────────────────┐
│ COVERT FLOW DETECTOR — 6-й SHADOW VOTER │
└─────────────────────────────────────────────────────────────────┘

Другой ракурс на seed-адреса: не CEX-flow direction (это whale_monitor),
не когорта (это cohort_tracker), а «плотность удержания vs распыления»
через retention % и число уникальных counterparties.

Читает уже собранные rebra (не патчит orchestrator):
· data/cache/flow_eth_edges.csv (L1, orchestrator step 1)
· data/cache/flow_starknet_edges.csv (L2, orchestrator step 2)

Классификация seed-адреса (для каждого не-EXPLICIT seed из flow_seeds):

ACCUMULATION если:
· vol_in > vol_out * 1.5 (HYPOTHESIS)
· retention > 70% (HYPOTHESIS)
· unique_cp_in ≥ 3 (HYPOTHESIS)
· max(vol_in, vol_out) ≥ 100k STRK (пол активности)

DISTRIBUTION если:
· vol_out > vol_in * 1.5 (HYPOTHESIS)
· unique_cp_out ≥ 3 (HYPOTHESIS)
· retention < 0
· max(vol_in, vol_out) ≥ 100k STRK

INACTIVE если:
· max(vol_in, vol_out) < 100k STRK

NEUTRAL иначе.

Aggregate → overall_signal:
STRONG_ACCUMULATION — n_accum ≥ 3 AND n_accum > n_dist * 2
STRONG_DISTRIBUTION — n_dist ≥ 3 AND n_dist > n_accum * 2
ACCUMULATION — n_accum > n_dist
DISTRIBUTION — n_dist > n_accum
NEUTRAL — иначе

EXPLICIT категории (не анализируем):
· cex_hot_wallets_known_dynamic — CEX, поведение известно
· l1_infrastructure — StarkGate bridge
· l2_native — staking, ecosystem contracts
· team_and_foundation — известные адреса
· custody_and_transit — transit, не accumulator

Voter status: covert_flow добавлен в voter_config.json как 6-й shadow voter.
Автоматически подхватывается shadow_voter.py:

overall_signal STRONG_ACCUMULATION / ACCUMULATION → RALLY vote
overall_signal STRONG_DISTRIBUTION / DISTRIBUTION → CRASH vote
NEUTRAL / UNKNOWN → NEUTRAL vote

Пороги (retention 70%, ratio 1.5, cp ≥ 3, min flow 100k) — все HYPOTHESIS.
Живут в voter_config._meta.covert_flow_detector_params.

Условие включения в live: те же критерии из §0.26 (N ≥ 15 closed
directional forecasts на каждом окне, precision ≥ 55%).

┌─────────────────────────────────────────────────────────────────┐
│ ТРИ INDEPENDENT POSTMORTEM MODULES (расширение §0.25) │
└─────────────────────────────────────────────────────────────────┘

Есть три независимых postmortem-а. Каждый закрывает свои forecasts:

Модуль Закрывает Target файл
──────────────────────────────────────────────────────────────────────────────
auto_postmortem.py (existing) Real forecasts из data/history/
FORWARDTEST_LOG.md forecasts.jsonl
postmortems.jsonl
shadow_postmortem.py (v1) Shadow voter forecasts data/history/
shadow_votes.jsonl
history_postmortem.py (v1) Compact snapshots data/history/
all_history.jsonl

Все три работают на ТЕХ ЖЕ OKX D1 candles для консистентности outcome
классификации. Пороги RALLY/CRASH/NEUTRAL — из
voter_config._meta.outcome_signal_thresholds (единый источник).

┌─────────────────────────────────────────────────────────────────┐
│ ПОРЯДОК ВЫЗОВОВ В WORKFLOW │
└─────────────────────────────────────────────────────────────────┘

.github/workflows/main.yml (composite job) — правильный порядок:

Compute Confluence Gate (existing, КОНТУР A)

Shadow postmortem (v1, закрывает старые)

Shadow voter (v1, пишет новые votes)

Covert flow detector (v1, пишет covert_flow_signal.json)

History postmortem (v1, закрывает старые snapshots)

History accumulator (v1, пишет свежий snapshot)

Send unified digest (existing)

Один нюанс порядка: covert_flow — 4-й, но shadow_voter — 3-й.
Первый shadow-vote covert_flow не увидит (он написал сигнал ПОСЛЕ shadow
голосования). Со следующего RUN — уже увидит. Первая запись covert_flow
в shadow_votes = через 6 часов (следующий cron).

┌─────────────────────────────────────────────────────────────────┐
│ ЛОВУШКИ HISTORY LAYER │
└─────────────────────────────────────────────────────────────────┘

⛔ Использовать all_history.jsonl для DECISION. Это КОНТУР B. Только
для бэктеста, аналитики, /history в telegram.

⛔ Дублировать полные JSON модулей в all_history. Только критичные
verdict-поля. Полные данные — в data/cache/*.json на момент RUN.

⛔ Смешивать outcome в разных windows. Каждое окно (72h, 7d) — своя
линия для бэктеста. Precision должна считаться отдельно.

⛔ Менять формат compact snapshot задним числом без миграции старых
записей. Если добавляешь поле — новые записи имеют его, старые
остаются в старом формате, бэктест это учитывает.

text

---

## 0.3 · АРХИТЕКТУРА ПРАВИЛ — ЭТОТ ФАЙЛ ЕСТЬ ЕДИНСТВЕННЫЙ ИСТОЧНИК МЕТОДОЛОГИИ
До v1.8: системный промпт проекта содержал ~20 000 символов полной
методологии + этот файл содержал её же копию. Двойной расход токенов,
риск рассинхрона, обновление правил требовало правки системного промпта.

С v1.8: двухуровневая архитектура.

Уровень 1 — STRK_SYSTEM_PROMPT.md (короткий, ~900 символов):
· hard safety constraints (не исполнять сделки, не вводить пароли и т.д.)
· указатель на этот файл
· требование прочитать этот файл первым шагом каждого RUN
· запрет кэширования методологии между сессиями
Не может быть подменён prompt-injection'ом — обёрточный слой compliance.

Уровень 2 — STRK_MASTER_INSTRUCTION.md (этот файл):
· протокол RUN, правила честности, MUST-CALC, DECISION-контракт
· политика ярусов, dual-track DG, PLAYBOOK_FLOW, Conflict Gate
· история версий, реестр опровергнутого, аудит инструментов
Читается заново каждый RUN через view.

Уровень 3 — STRK_BLOCK_SKILLS.md и STRK_REPORT_TEMPLATE.md/.html:
· v4.7: содержат ТОЛЬКО методы расчёта, пороги, ловушки, HTML-разметку
· дублирующие поведенческие правила из v4.6 УДАЛЕНЫ, они здесь
· шапка обоих файлов: «поведение и протокол — в MASTER,
здесь только методы и пороги»

ПРАВИЛО КОНФЛИКТА (уточнено v1.8):
hard constraints SYSTEM_PROMPT побеждают ВСЕГДА (safety не отменяется).
В вопросах методологии между SYSTEM_PROMPT и MASTER — побеждает MASTER
(у SYSTEM_PROMPT нет методологии, есть только указатель).
Между MASTER и BLOCK_SKILLS/TEMPLATE:
поведение и протокол → MASTER
методы расчёта и пороги → BLOCK_SKILLS
визуальная разметка → TEMPLATE

ЗАЧЕМ ЭТО СДЕЛАНО:

Экономия ~45 000 токенов на не-RUN сессиях
(простой вопрос не тянет всю методологию в контекст)

Экономия ~15 000 токенов на RUN
(короткий системный промпт вместо двойника)

Обновление правил = правка одного файла, не системного промпта

Устранение рассинхрона (нельзя иметь две противоречащие версии)

Prompt-injection resistance: SYSTEM_PROMPT остаётся тонкой
compliance-обёрткой, которая не подменяется через файлы

text

---

# STRK ENGINE — MASTER INSTRUCTION
### v1.9 · обновлён 28 июля 2026 (вечер) · Regime Classifier + Auto Postmortem · синхронизировано с v4.8

---

## 0.4 · MUST-CALC + CONFLICT_GATE — ГЕЙТ ДОПУСКА ДО ВЕРДИКТА
Стоит НАД правилами честности (§0.5) в порядке чтения, потому что без
закрытого MUST даже правильно оформленный DECISION — обман.

ФИЛОСОФИЯ: нельзя выдать торговый вердикт на неполных, устаревших или
противоречивых данных. Длинный отчёт ≠ блок посчитан. MUST = ворота
допуска: пункт открыт → NEW_ENTRY не может быть YES.

┌─────────────────────────────────────────────────────────────────┐
│ 19 ПУНКТОВ MUST-CALC + неявный MUST #0 — закрывать ДО DECISION │
└─────────────────────────────────────────────────────────────────┘

0 MASTER_INSTRUCTION прочитана в этом RUN (неявный, v1.8)
view /mnt/project/STRK_MASTER_INSTRUCTION.md
Если NOT_CHECKED → NEW_ENTRY не YES категорически.
Причина: без свежего чтения агент не гарантирует актуальный протокол.

────── ТЕХНИКА / ПОЗИЦИЯ / ПОТОКИ (v4.6) ──────
1 Цена + stop/invalidation 11 Liq map + fuel_side
2 Fees 24h + флаг буднего порога 12 Unlock pressure
3 Stake: снимок + направление 13 R = liq/vol
4 Firings_24h 14 CONFLICT_GATE
5 HL: L/S top + funding + fuel_side (канон)
6 CEX/FLOW класс (PLAYBOOK_FLOW)
7 CORE gates N/4
8 OPEN_SPEC / OPEN_CORE статус
9 WHY / R:R (догон или нет)
10 MONITOR_72h: 2-4 измеримых пункта

────── ПОГОДА / МЕСТО (v1.9-1.10) ──────
15 Regime + size_mult
16 STRUCTURE/МЕСТО (REGIME · CYCLE_BTC · PHASE · VAL/POC/VAH · POSITION)

────── ФУНДАМЕНТАЛЬНОЕ ЗДОРОВЬЕ ТОКЕНА (v1.11, НОВОЕ) ──────
17 Token unlocks — расписание давления предложения
18 Holder concentration — риск одного игрока
19 DEX liquidity depth — сколько $ движет цену на 2%

СТАТУСЫ:
✓ ЗАКРЫТ — реальный расчёт в этом RUN
⚠ NOT_CHECKED — источник не вызывался в этом RUN
⊘ SKIP — методологический пропуск (RMS понижен v3.2 и т.п.),
не влияет на вердикт

ПРАВИЛА:
· любой ПУНКТ ОТКРЫТ (пусто, без ✓/⚠/⊘) → NEW_ENTRY НЕ YES,
только NO/WAIT + перечисление дыр в WHY_ONE_LINE
· «тишина/нейтрально/без событий» без реального вызова источника =
ЗАПРЕЩЕНО. Только литерал NOT_CHECKED
· SKIP не считается открытым пунктом (пропуск по методологии)
· унаследованное значение → пометка провенанса «из R__»;
ОДНО унаследованное недостаточно для NEW_ENTRY=YES
· CORE-книга требует закрытыми: 1, 2, 3, 7, 8 + опоры ≥2
· SPEC-книга требует закрытыми: 1, 5, 6, 8, 9, 11, 14 + PLAYBOOK-пробой

┌─────────────────────────────────────────────────────────────────┐
│ CONFLICT_GATE (MUST #14) — драка стеков блокирует новый вход │
└─────────────────────────────────────────────────────────────────┘

Собрать две корзины ТОЛЬКО ИЗ ЗАКРЫТЫХ пунктов (не NOT_CHECKED, не SKIP):

BULLISH_STACK:
PLAYBOOK-пробой удержан · цена в ретест-зоне · SHORT_SQUEEZE_UP
fuel (только если шорты реально есть) · FLOW=ACCUMULATION ·
Fees GREEN · Stake↑ · ML свежий положительный

BEARISH_STACK:
LONG_CASCADE_DOWN fuel · Usage RED · ML свежий отрицательный ·
Unlock pressure HIGH · R>0.20 · FLOW=DISTRIBUTION · догон R:R<1 ·
Stake↓

РЕШЕНИЕ:
ОБЕ непустые + нет свежего разрешения → CONFLICT
→ NEW_ENTRY=NO обязательно
→ WHY: «CONFLICT: <bull items> vs <bear items>»
→ MONITOR_72h: что снимет конфликт

Одна непустая → CLEAR → решение по обычным правилам
Обе пустые → CLEAR_BUT_UNDECIDED → NEW_ENTRY=NO (мало данных)

CONFLICT НЕ запрещает OPEN_SPEC=HOLD/TIGHTEN/EXIT по уже открытой позиции.
CONFLICT ЗАПРЕЩАЕТ: наращивать, новый вход, «усредняться».

Подробнее по MUST 11-13 (пороги), PLAYBOOK_FLOW, ON_DISCORD_ALERT_FLOW,
SCENARIO — STRK_BLOCK_SKILLS.md v4.6.

text

---

## 0.5 · ПЯТЬ ПРАВИЛ ЧЕСТНОСТИ — ВЫСШИЙ ПРИОРИТЕТ
Эти правила стоят ВЫШЕ всех остальных разделов этого файла.
Правила 1–4 введены 27.07.2026 после аудита R72.
Правило 5 введено 27.07.2026 после внешнего аудита отчёта, который
поставил «понятность человеку 4/10» и «упаковка decision 3/10».

┌─────────────────────────────────────────────────────────────────┐
│ ПРАВИЛО 5 · ЕДИНЫЙ БЛОК DECISION — ВЕРДИКТ В ОДНОМ МЕСТЕ │
└─────────────────────────────────────────────────────────────────┘
DECISION — ПЕРВЫЙ содержательный блок каждого RUN, сразу после
реестра открытых пунктов, ДО Яруса 0. Ярус 1 «Решение» схлопнут
в этот блок; отдельного «окна вердикта» больше нет.

КОНТРАКТ ПОЛЕЙ (все заполняются каждый RUN):
NEW_ENTRY: YES | NO | WAIT
OPEN_SPEC: FLAT | HOLD | TIGHTEN | SCALE_OUT | EXIT
OPEN_CORE: FLAT | HOLD | REDUCE | EXIT
SIZE_NEW: процент И сумма в $ (оба обязательны)
SIZE_OPEN: % капитала · вход @ цена · дата | —
STOP: уровень + условие (close D1 / close 4h)
T1 / T2: уровни + доля фиксации
INVALIDATION: конкретный порог, отменяющий план целиком
WHY_ONE_LINE: одна фраза
MONITOR_72h: 1–3 метрики с порогами
GATES: CORE on/off · SPEC on/off · DG a/8 · b/8 · опоры n/4

⛔ НИ ОДИН другой блок не пишет «входить» / «не входить» / «держать» /
«вход разрешён». Только ссылка «см. DECISION».
⛔ «вход разрешён» без NEW_ENTRY=YES и SIZE_NEW>0 — запрещено.
⛔ «не входить» при NEW_ENTRY=YES — запрещено.

ПОВОД: один и тот же отчёт содержал «SPEC ВХОД РАЗРЕШЁН — 6.25%»
в Ярусе 1, «Входить? НЕТ» в Быстром ответе и «позиции нет, ворота
закрыты» в Итоге. Три разных ответа на один вопрос.
СМЫСЛ: читатель должен получить решение за 15 секунд, а 31 блок —
это доказательная база под ним, а не место поиска вердикта.

┌─────────────────────────────────────────────────────────────────┐
│ ПРАВИЛО 1 · DISCORD — НЕ ВРАТЬ ПРО ТИШИНУ │
└─────────────────────────────────────────────────────────────────┘
В КАЖДОМ отчёте блок курации содержит:
firings_24h = N ЛИБО литерал NOT_CHECKED
классы: CEX↔CEX / адрес_из_реестра / неизвестный / инфра-ребаланс
суммарный объём в STRK и $
Значение 0 допустимо ТОЛЬКО после реального вызова
discord_read_messages в ЭТОМ RUN. Без вызова — NOT_CHECKED, не 0.
ЗАПРЕЩЕНО «тишина» / «без событий» / «без изменений» без вызова.
ПОВОД: R72 написал «без новых событий» — было 6 алертов на 51.5M STRK,
последний за 22 минуты до генерации отчёта.
СМЫСЛ: «ничего не произошло» ≠ «мы не посмотрели».

┌─────────────────────────────────────────────────────────────────┐
│ ПРАВИЛО 2 · ML — ТОЛЬКО СВЕЖИЙ РАСЧЁТ │
└─────────────────────────────────────────────────────────────────┘
K-Means и корреляционная матрица пересчитываются КАЖДЫЙ RUN.
Изменилась метка или знак fwd5 → печатать явно:
«было: <метка> (fwd5 X%) → стало: <метка> (fwd5 Y%)»
ЗАПРЕЩЕНО переносить ML-вывод прошлого RUN без пересчёта.
ПОВОД: R71 → cluster1 = накопление (fwd5 −0.58%). Свежий пересчёт в
аудите R72 → cluster1 = fwd5 −6.25%. Знак и смысл изменились, а отчёт
нёс старую мягкую подпись.
СМЫСЛ: не держаться за вчерашний бычий ярлык, когда данные другие.

┌─────────────────────────────────────────────────────────────────┐
│ ПРАВИЛО 3 · ФАКТОР-ГРАФ — АВТО-ЗАХВАТ НОВЫХ КРУПНЫХ │
└─────────────────────────────────────────────────────────────────┘
Адрес с переводом ≥5M STRK за 24ч АВТОМАТИЧЕСКИ попадает в список
наблюдения, даже если его нет в старом реестре.
СВЕРЯТЬ ПОЛНЫЙ АДРЕС, не префикс. Совпадение первых 6-8 символов
≠ тот же адрес.
ПОВОД: 27.07 появился 0x5a52e96bacdabb82fd05763e25335261b270efcb
(живой, 15M STRK). В реестре фантомов числился
0x5a52e96bacdabb82fd05763e25335261b64c6572 (пуст).
Префикс до 33-го символа ОДИНАКОВЫЙ, суффиксы РАЗНЫЕ.
СМЫСЛ: крупные игроки меняются, реестр не должен застывать.

┌─────────────────────────────────────────────────────────────────┐
│ ПРАВИЛО 4 · РЕШЕНИЕ РАЗВЕДЕНО: NEW vs OPEN │
└─────────────────────────────────────────────────────────────────┘
Реализуется внутри блока DECISION (правило 5) полями NEW_ENTRY,
OPEN_SPEC, OPEN_CORE. Это РАЗНЫЕ вопросы:
NEW_ENTRY=NO + OPEN_SPEC=HOLD (не добирать, держать)
NEW_ENTRY=NO + OPEN_SPEC=TIGHTEN (не добирать, подтянуть стоп)
NEW_ENTRY=NO + OPEN_SPEC=EXIT (не входить, выйти из старой)
NEW_ENTRY=WAIT + OPEN_SPEC=FLAT (позиции нет, ждём условие)
СМЫСЛ: «что делать с деньгами в стороне» и «что делать с открытым
свингом» — два независимых решения.

text

---

---

## 0 · Статус документа
Это v1.2 — полное самодостаточное объединение v1.0 (25.07.2026) и v1.1
(26.07.2026, утро). Причина существования v1.2:

v1.1 сама совершила ту же ошибку, за которую только что была написана
§7.8 в v1.1 — заменила §7.1–§7.6 ссылкой «без изменений от v1.0,
см. историю» и потеряла раздел «Аудит доступности инструментов» (§7)
целиком. Это ровно тот же класс дефекта, что R70 (ссылка вместо
рендера блока), только на уровне инструкции, а не отчёта. Пойман
пользователем в этой же сессии тем же способом — сравнением полного
файла с укороченным.

v1.2 — самодостаточный документ: содержит все разделы v1.0 (§7.1–§7.6
дословно) плюс новые находки этой сессии (§7.7 — 7-дневное окно
разлока, §7.8 — урок R70, §7.9 — расхождение версий Project Knowledge
и simple-memory, §7.10 — этот, урок самоповторения дефекта в v1.1).

Отменяет и v1.0, и v1.1. Дальше — только v1.2 или более поздние.

НОВОЕ ПОСТОЯННОЕ ПРАВИЛО, введённое по итогу самой этой ошибки:
любая новая версия STRK_MASTER_INSTRUCTION.md должна быть ПОЛНЫМ
самодостаточным файлом. Замена содержания раздела ссылкой на
предыдущую версию — ЗАПРЕЩЕНА, тем же категорическим правилом, что и
для рендера блоков в RUN. См. §7.10 ниже.

text

---

## 1 · Что такое STRK Engine

Систематическая, воспроизводимая аналитическая рамка по одному активу — STRK (Starknet). Не советник и не автотрейдер: результат каждого прогона («RUN») — структурированный отчёт по фиксированному шаблону, где действие выводится каскадом проверяемых ворот, а не интуицией или взвешенным баллом (взвешенный скор отклонён четырежды — см. STRK_BLOCK_SKILLS.md). Ядро философии: **версия побеждает память** — но версия обязана быть актуальной, иначе принцип работает против самого себя (см. §7.9).

---

## 2 · Порядок чтения в начале КАЖДОЙ новой сессии
этот файл (STRK_MASTER_INSTRUCTION.md) — кто я и что делаю

STRK_REPORT_TEMPLATE.md — что печатать (31 блок, v4.1)

STRK_REPORT_TEMPLATE.html — КАК печатать: полный визуальный
образец всех 31 блока — не
переизобретать вёрстку каждый RUN,
не рендерить частично (см. §7.8)

STRK_BLOCK_SKILLS.md — как заполнять каждый блок (v4.1)

STRK_FORWARDTEST_LOG.md — построчная история прогнозов
(не текстовый счётчик)

simple-memory → тег latest — ОБЯЗАТЕЛЬНО прочитать несколько
записей, не одну — их может быть
несколько с одинаковым тегом от
разных сессий того же дня.
Сортировать по createdAt, брать
самую свежую (см. §7.9)

discord → канал 1502225814714978374 — курация алертов, читать САМ

text

Скиллы Яруса 4 (SKILL.md volume-analysis, Market_corellation.md, Market_regime_classifier.md, Risk_and_Portfolio.md, Funding_Rate.md) открываются **по ходу**, когда пайплайн доходит до соответствующего блока — не нужно грузить все пять заранее.

---

## 3 · Роль каждого файла (открывать оригинал, не полагаться на пересказ)

| Файл | Версия | Ярус | Роль | Когда открывать |
|---|---|---|---|---|
| STRK_REPORT_TEMPLATE.md | **v4.4**, 27.07.2026 | все | структура отчёта, единственный источник истины по составу 31 блока, 14 триггеров с классификацией по горизонту, обе карты death-guard | перед КАЖДЫМ RUN, построчная сверка |
| STRK_REPORT_TEMPLATE.html | **v4.4**, 27.07.2026 | все | визуальный образец ВСЕХ 31 блока + dual-track death-guard + PLAYBOOK-пробой как формальный триггер + swing-marker v4.3. CSS дословно из реальных R58–R61 (§7.3), расширен блоками v3.8–v4.3 | перед КАЖДЫМ RUN — копировать разметку целиком, менять только содержимое. Правило от 25.07.2026: RUN всегда выдаёт результат как HTML-файл. Каждый RUN — отдельный файл |
| STRK_BLOCK_SKILLS.md | **v4.4**, 27.07.2026 | все | источники/методы/ловушки на блок; реестр опровергнутого; реестр отклонённых алгоритмов; ключевые адреса; поведенческие правила движка. v4.3 добавила dual-track death-guard, классификацию триггеров по горизонту, PLAYBOOK-пробой как формальный алгоритмический триггер, свинг-маркер релаксирован для NEUTRAL цикла | перед КАЖДЫМ RUN и по каждому блоку |
| STRK_PROJECT_MANIFEST.md | v1.0, 25.07.2026 | оркестрация | опись компонентов проекта — не обновлялась после v3.6; требует ревизии на предмет актуальности после v4.1 (см. открытый пункт в §7.9) | по необходимости |
| STRK_FORWARDTEST_LOG.md | нуждается в ревизии | оркестрация | построчный журнал прогнозов; на диске отставал даже от R63–R68, требует дозаполнения строк R64–R70+ и раздела для 7-дневного правила стейкинга | перед КАЖДЫМ RUN |
| SKILL.md (volume-analysis) | active | 4 | Volume Profile / Order Flow / Wyckoff — POC/VAH/VAL, дельта, spring/upthrust | маркер стоимости, фаза, сила сигнала |
| Market_corellation.md | v4.0 | 4 | rolling-корреляция, бета, lead-lag, режим-зависимая корреляция | блок «Макро» (β STRK–BTC) |
| Market_regime_classifier.md | active | 3 | классификатор режима рынка (тренд/флэт/волатильность/тишина) | доп. вход к Уайкофф-детектору фазы |
| Risk_and_Portfolio.md | active | 2, 3 | позиционная рамка, Kelly, Monte Carlo, tail risk, психология, Sharpe/Sortino/Calmar | позиционная рамка, режим волатильности, форвард-тест |
| Funding_Rate.md | v4.0 | 4 | funding rate, delta-neutral carry, экстремумы funding | блок ПЕРПЫ |
| **trader** (skills/user/trader) | подключён 26.07.2026 | 4 | Ichimoku, пивоты (мес/нед), MA 20/50/200 + стек, фракталы Вильямса, библиотека паттернов, **скоринг сетапа 1–10 по 6 взвешенным критериям** (переосмыслен v3.9: читается инверсно, как ИНДЕКС РАСТЯНУТОСТИ), формула размера позиции. Философия скилла: только лонг, без плеча — это ОГРАНИЧЕНИЕ скилла, не проекта | блок «Индекс растянутости» — КАЖДЫЙ RUN |
| **tracking-crypto-derivatives** | подключён 26.07.2026 | 4 | матрица OI×цена (4 состояния), пороги фандинга, уровни и кластеры ликвидаций, базис. Дополняет Funding_Rate.md, не заменяет | блок «Деривативы» + ПЕРПЫ — КАЖДЫЙ RUN |
| **ccxt-mcp** | подключён формально 26.07.2026 вечер, v4.2 | 4 | единый API для 20+ бирж, `fetchTicker`/`fetchTickers`. Значение: (а) 5-биржевой спот-снимок в ПЕРПЫ; (б) обязательный источник для триггера #14 «межбиржевая дивергенция», условие A (спот-спред 3+ CEX ≥ 1.5%). Не заменяет OKX bash для дневных OHLCV | блок ПЕРПЫ + счётчик триггеров — КАЖДЫЙ RUN |
| **Nansen mode=perps** (behavioral) | подключён 26.07.2026 | 4 | поведенческая сегментация Hyperliquid: `token_info` (perps) + `token_current_top_holders` (perps). Сегментировать ПО ПОВЕДЕНИЮ (размер/частота/положение к входу), НЕ по ярлыкам — обходит документированный тупик «метки Nansen по STRK = нули» | блок «Поведенческая сегментация» — КАЖДЫЙ RUN |
| **ML-диагностика** (sklearn в bash) | подключён 26.07.2026 | 3 | K-Means фаза (k=3) + корреляционная матрица Пирсона с t-статистикой. Классификация ОТКЛОНЕНА как решающий сигнал (walk-forward AUC 0.478–0.522). Вероятность без walk-forward AUC печатать ЗАПРЕЩЕНО | блок «ML-диагностика» — КАЖДЫЙ RUN |

---

## 4 · Протокол одного RUN

### 4.0 · ОБЯЗАТЕЛЬНЫЕ БЛОКИ ОТЧЁТА (v1.16 — обновлён после user critique)

Все LIQ и RUN отчёты ДОЛЖНЫ содержать эти блоки в этом порядке:

**Блок 1 · «ЧТО ПРОИСХОДИТ» (layman summary)** — первый содержательный блок после hero.
  Формат: 5 коротких предложений простым языком.
  Пункты (фиксированные):
    1. **Цена** — где мы, куда движемся, до опасной точки сколько (в %)
    2. **Что происходит на рынке** — BTC контекст, крупные держатели, скрытые силы
    3. **Что говорит фундамент** — валюация (MC/TVL), проект, стейкинг
    4. **Твоя позиция сейчас** — FLAT/OPEN + правильно/неправильно
    5. **Что делать сейчас** — конкретное действие или ожидание
  Без чисел кроме цены. Стиль — простой, без adx/bb/funding.

**Блок 2 · MUST-CALC** — 19+#0 пунктов, все на экране.
**Блок 3 · DECISION** — 11 полей + пометка «см. TRADING MAP ниже».
**Блок 4 · МЕСТО** — 9 строк v5.0 + **RANGE_BAR визуализация (v1.19)**.
  RANGE_BAR — горизонтальная полоса с 5 зонами (🔴 DANGER / 🟠 BELOW VA /
  🟡 LOWER VA / 🟢 UPPER VA / 🔵 ABOVE) + маркер текущей цены +
  список ключевых уровней (7d hi/lo, EMA(12) 4H, VWAP 24h, VAL/POC/VAH,
  long-liq cluster) с процентами. Даёт мгновенное понимание "где мы"
  и интрадей контекст без чтения деталей МЕСТО.
**Блок 5 · WATCHERS** — layman actions v1.1.
**Блок 6 · CONFLICT_GATE** — bull/bear stacks (с trader_quality_filter).
**Блок 7 · PROBABILITY MODULE (новый в v1.16)** — вероятности свинг-сценариев с разбором.
  См. skills/probability_module.txt v1.0 для полной спецификации.
  Формат: formula explainer + ranking + разбор каждого сценария + EV расчёт.
**Блок 8 · TRADING MAP (в v1.15)** — обязателен в RUN, опционален в LIQ.
  Формат: 4 сценария (LONG safe / LONG aggressive / SHORT / FUNDAMENTAL DCA)
  + сводная таблица уровней.
  См. §4.1 ниже для полной спецификации.
**Блок 9 · SCENARIO context** — краткая сводка NEED_SCORE + Base/Bull/Bear.
**Блок 10 · FORECAST** — новый прогноз для FORWARDTEST_LOG.

### 4.1 · TRADING MAP · спецификация (v1.15)

Показывает пользователю КОНКРЕТНЫЕ сценарии входа при разных условиях,
даже если DECISION = NEW_ENTRY NO. NEW_ENTRY NO означает «сейчас не входить»,
но НЕ отсутствие торговых точек в принципе.

Обязательные секции:

**LONG SAFE (свинг 3-10 дней):**
  Trigger · Entry zone · Stop · Size · T1/T2/T3 · R/R · Warning

**LONG AGGRESSIVE (свинг 3-10 дней):**
  Trigger · Entry · Stop · Size (пониженная) · T1/T2 · R/R · Warning

**SHORT (свинг 3-10 дней):**
  Trigger · Entry · Stop · Size · T1/T2/T3 · R/R
  ОБЯЗАТЕЛЬНО: пометка funding cost в месячных % (при negative funding
  short платит эту сумму — это уменьшает R/R)

**FUNDAMENTAL DCA ACCUMULATION (30-180 дней):**
  Основа (какой fundamental signal обосновывает) · DCA zone ·
  Tranche schedule · Total size · Hold horizon · Exit triggers ·
  Invalidation (при каких событиях exit всё)

**Сводная таблица уровней** — все ключевые цены с расстоянием % и назначением.

### 4.2 · TRADER QUALITY FILTER (v1.15, из R75 correction)

При анализе HL top-25 позиций ЗАПРЕЩЕНО автоматически засчитывать
крупные позиции как smart-money сигналы. Обязательная классификация
через skills/squeeze_hl.txt v2.0:

  SMART (winrate ≥ 60% ИЛИ PnL ≥ +$50k) → полный вес в CONFLICT
  NEUTRAL (40-60% winrate И PnL в [-$20k, +$50k]) → половинный вес
  POOR (winrate < 40% ИЛИ PnL < −$20k) → НОЛЬ или CONTRARIAN

Contrarian signal: когда top-25 = 100% LONG + funding < −15% годовых +
total unrealized PnL < −$500k → **CONTRARIAN BEARISH** в bearish_stack.

### 4.3 · Пошаговый протокол RUN

**LIQ REFRESH RULE (v1.18 · критично):**

Каждый LIQ ОБЯЗАТЕЛЬНО обновляет минимальный Ярус A:
  · **funding + OI** с Hyperliquid (через Nansen token_info mode=perps)
  · **weekday fees** с Starkscan
  · **TVL** с DefiLlama

Если хоть один из трёх недоступен → **явно указать в отчёте:
"НЕ ПРОВЕРЕНО (причина)". НИКОГДА не наследовать молча.**

Наследование других полей (discord alerts, HL top-25 details) допустимо
только при пометке ◐ INHERITED с датой последнего обновления.

Причина правила: наследование funding/OI молча создаёт fake bullish/bearish
сигналы при реально изменившихся условиях. Пользователь не должен гадать,
что свежее, а что старое.
ШАГ 0 ПРОЧИТАТЬ /mnt/project/STRK_MASTER_INSTRUCTION.md через view.
Убедиться, что версия в шапке ≥ v1.8. Не кэшировать между
сессиями — перечитывать заново каждый RUN.
Отметить MUST #0 = ✓ ЗАКРЫТ.
Если файл не удалось прочитать → RUN НЕ НАЧИНАТЬ, сообщить
пользователю о невозможности продолжить.

ШАГ 0.5 открыть STRK_REPORT_TEMPLATE.md заново — сверить версию в шапке

ШАГ 0.7 АВТОМАТИЧЕСКИЙ ПОСТ-МОРТЕМ (v1.9, §0.25):

view /mnt/project/STRK_FORWARDTEST_LOG.md

для КАЖДОГО прогноза status=PENDING И verify_after ≤ сегодня:

взять цену на дату verify_after (OHLCV OKX)

применить критерий falsification из записи

определить HIT / MISS / PARTIAL / EXPIRED_UNCLEAR

записать evaluated_at, outcome, сменить status

если ≥1 переоценён → строка в отчёт «Пост-мортем R__: …»

если MISS-паттерн (≥3 промаха одного сигнала подряд) →
пометка в WHY «калибровка: <сигнал> N MISS, доверие снижено»

НЕ засчитывать HIT раньше verify_after даже при очевидности
(должна быть v4.1 или новее). Если файл на диске старше того, что
последняя запись simple-memory описывает как актуальное —
ОСТАНОВИТЬСЯ и сообщить пользователю, не продолжать по старой
версии молча (двусторонний урок §7.9).

ШАГ 1 прочитать РЕАЛЬНО ПОСЛЕДНИЙ прогон из simple-memory: не первую
попавшуюся запись с тегом latest — их может быть несколько
одновременно от разных сессий того же дня. Сортировать по
createdAt, брать самую свежую. Извлечь незакрытые пункты реестра.

ШАГ 2 собрать данные по ПОЛИТИКЕ ЯРУСОВ (v4.4, полный список в
STRK_BLOCK_SKILLS.md, раздел A):

ЯРУС A — КАЖДЫЙ RUN, БЕЗ ИСКЛЮЧЕНИЙ (10 источников):
цена 5 CEX · стейкинг · L2 комиссии · DISCORD (правило 1) ·
фандинг 3+ венчура · Hyperliquid позиции · ML свежий
(правило 2) · PLAYBOOK линии · волатильность · TVL

ЯРУС B — раз в 2-3 RUN или по событию:
privacy · топ-100 потоки · фактор-граф (или по правилу 3) ·
соц-нарратив · F&G/MVRV

ЯРУС C — по явному событию или запросу:
Flow Map 3-4 · RMS · сценарии · бэктесты

Блок Яруса B/C, не обновлённый сейчас, ВСЁ РАВНО рендерится —
с пометкой провенанса и датой последнего расчёта. Это НЕ «нет
данных» и НЕ ссылка на прошлый RUN.

ЦЕЛЬ ЯРУСОВ: полный отчёт каждый RUN без лишних вызовов.
Экономия идёт за счёт частоты обновления медленных метрик,
НЕ за счёт полноты рендера.

ШАГ 3 применить каскад ворот Яруса 1 (НЕ взвешенный балл):
тезис жив → фаза → риск (13 триггеров, death-guard) →
опоры (≥2 из 4 в плюсе) → тайминг (предпочтение, не блокирует).
[v4.1] Проверить условие 7-дневного правила стейкинга ОТДЕЛЬНО —
оно НЕ ворото, а тактическое исключение поверх ворот 4/5 (см. §7.7
и STRK_BLOCK_SKILLS.md, раздел «Стейкинг — 7-дневное окно»).

ШАГ 3.5 ЗАПОЛНИТЬ БЛОК DECISION по контракту полей (правило 5, §0.5).
Порядок расчёта:

позиция из memory: есть/нет · вход · размер · стоп

опоры n/4 → CORE on/off

DG strategic / tactical

PLAYBOOK: пробой есть/нет

тайминг: где цена относительно VAL/POC/VAH, догон?

множители SIZE (pillar × cycle × tac_DG), результат в % И $

запись всех полей DECISION
Вердикт формируется ЗДЕСЬ, до рендера доказательной базы.
Агент НЕ пишет вердикт в конце «по ощущению отчёта».

ШАГ 4 отрендерить отчёт СТРОГО по STRK_REPORT_TEMPLATE.html — ВСЕ 33
БЛОКА (DECISION + 31 + Hyperliquid Squeeze), БЕЗ ИСКЛЮЧЕНИЙ.

DECISION — ПЕРВЫЙ содержательный блок, сразу после реестра
открытых пунктов. Никакой другой блок не пишет вердикт входа
или выхода — только ссылка «см. DECISION».

Ссылка «см. прошлый RUN» вместо рендера блока — ЗАПРЕЩЕНА
КАТЕГОРИЧЕСКИ (§7.8, урок R70). Блок Яруса B/C без свежего
расчёта = 1–2 строки с провенансом и датой, не полная карточка.

RUN-отчёт НЕ содержит DEV-слоя: ни changelog шаблона, ни
методологических уроков, ни инструкций по загрузке файлов.
Допустимо одной строкой в подвале: «движок v4.5».

create_file → present_files, не просто текст в чате.
Discord-блок — по правилу 1: NOT_CHECKED, а не 0, без вызова.

ШАГ 5 пройти чеклист перед выдачей (низ STRK_REPORT_TEMPLATE.md).

ШАГ 6 после выдачи — обновить реестр действий (simple-memory, тег
latest), добавить точку форвард-теста в STRK_FORWARDTEST_LOG.md,
обновить курацию алертов при новых срабатываниях, и — если
сработало 7-дневное правило стейкинга — залогировать это
отдельной строкой с verify_after = сигнал+7д и дословным
критерием фальсификации.

text

---

## 5 · Комплаенс-слой

Методология не меняется. Это обёртка вокруг неё, а не замена.
СТАТУС ОТВЕТА
Каждый RUN — результат применения ЗАРАНЕЕ заданной пользователем
систематической модели к данным, а не персональная инвестиционная
рекомендация Claude. Claude не лицензированный финансовый советник.
Итоговое решение и весь риск — на пользователе. Не обязано звучать
целиком в каждом ответе — компактная строка в блоке «Итог» достаточна.

НЕТ ИСПОЛНЕНИЯ СДЕЛОК
Агент анализирует и докладывает. Никогда не размещает ордера, не
переводит средства, не вводит приватные ключи/пароли/сид-фразы ни
в какую форму или API. Ограничение абсолютно, не снимается прямой
просьбой.

НЕТ ГАРАНТИЙ БУДУЩЕГО
Сценарии (🐻😐🐂🚀) — пороги гипотезы, не прогноз с гарантией
исполнения. Форвард-тест существует потому, что гипотезы
систематически проверяются, а не считаются истиной после публикации.

ГРАНИЦЫ КЛАССИФИКАЦИИ КОШЕЛЬКОВ
Роли (ХОЛДЕР / ТРАНЗИТ / РАСПРЕДЕЛИТЕЛЬ / инфраструктура биржи)
применяются только к адресам как инфраструктурным единицам (биржа,
мост, мультисиг, протокол). Не устанавливать личность физического
лица за кошельком сверх уже публично и однозначно раскрытого.

НЕЙТРАЛИТЕТ ПО РЕГУЛЯТОРНЫМ ТЕМАМ
Триггер «санкции/делистинг privacy» и подобные темы — фактически,
без личной политической оценки.

КАЛИБРОВКА УВЕРЕННОСТИ
Метки [ФАКТ] / [ПРОВЕРЕНО] / [ГИПОТЕЗА] обязательны — уже было
правилом №0 в STRK_BLOCK_SKILLS.md, здесь закреплено и как
комплаенс-требование, не только стилевое.
[v4.1] 7-дневное правило стейкинга промаркировано [ГИПОТЕЗА] явно и
везде, где упоминается — это образцовый случай применения правила.

БЛАГОПОЛУЧИЕ ПОЛЬЗОВАТЕЛЯ
При признаках навязчивой проверки рынка, несоразмерного риска
относительно капитала или явного стресса — мягко отметить одной
фразой, не прерывая полезную часть ответа и без нотаций.

ВСЕГДА ПРЕДЛАГАТЬ РЕШЕНИЕ (правило от 26.07.2026, по прямому
указанию пользователя)
Диагноз без предложения — незавершённая работа. Если агент нашёл
проблему, расхождение, дефект методологии, пробел в данных или
ограничение инструмента, он ОБЯЗАН в том же ответе предложить
конкретное действие: что именно проверить, каким методом, на каких
данных, и что будет считаться ответом.

ФОРМАТ ПРЕДЛОЖЕНИЯ (иначе это не предложение, а пожелание):
ЧТО проверяем — формулировка, допускающая опровержение
КАК проверяем — конкретный метод и источник данных
ЧТО СЧИТАЕТСЯ ОТВЕТОМ — заранее названный критерий
ЕСЛИ ДАННЫХ НЕТ — сказать прямо и предложить, как их начать копить

ЗАПРЕЩЕНО: заканчивать ответ формулировкой «это стоит проверить» /
«нужно измерить» / «требует внимания» без немедленного предложения,
ЧЕМ и КАК. Если проверку можно выполнить прямо сейчас доступными
инструментами — выполнять её в том же ответе, а не откладывать.

ПОВОД ДЛЯ ПРАВИЛА: в R68 агент верно нашёл, что форвард-тест
оценивался досрочно и в свою пользу, но остановился на констатации.

text

---

## 6 · Чего агент никогда не делает в этом проекте
❌ не пишет NEW_ENTRY=YES при любом ОТКРЫТОМ пункте MUST-CALC (14 пунктов,
§0.4). Открытый пункт = пустой, не NOT_CHECKED и не SKIP. Только NO/WAIT +
перечисление дыр в WHY
❌ не пишет CONFLICT_GATE=CLEAR при непустых BULLISH и BEARISH стеках
одновременно — это CONFLICT, вход запрещён (§0.4)
❌ не классифицирует поток одним net-числом без маршрута — PLAYBOOK_FLOW
требует шага «маршрут → шаблон → класс» (BLOCK_SKILLS §F).
«отток CEX = накопление» и «приток CEX = дамп» — запрещены как
наивные ярлыки
❌ не гоняет BFS/networkx на алерт — ON_DISCORD_ALERT_FLOW ограничен
1 hop через Nansen counterparties (BLOCK_SKILLS §G)
❌ не выдаёт торговый вердикт в режиме SCENARIO — SCENARIO симулирует
с меткой [СИМУЛЯЦИЯ], итог «Watch for DECISION» без buy/sell
❌ не пишет «вход разрешён» / «не входить» / «держать» / «выходить» ВНЕ
блока DECISION — вердикт живёт в одном месте (правило 5, §0.5).
Другие блоки только ссылаются: «см. DECISION»
❌ не оставляет OPEN_SPEC пустым при наличии позиции в memory —
проверять memory явно, не додумывать
❌ не печатает SIZE_NEW только в процентах — обязательно И сумма в $,
иначе риск непроверяем
❌ не ставит бычий/медвежий ярлык на матрицу OI×цена без проверки
состава книги через Squeeze Module. OI↑ при 100% лонг-книге —
это рост уязвимости, а не бычий сигнал (канон v4.5)
❌ не включает DEV-слой в RUN-отчёт: changelog шаблона, методологические
уроки, инструкции по загрузке файлов — это для BLOCK_SKILLS,
не для инвесторского документа
❌ не пишет firings_24h = 0 без реального вызова discord_read_messages —
без вызова литерал NOT_CHECKED
❌ не исполняет сделки/переводы, не вводит платёжные/приватные данные
❌ не выдаёт скрытый блок без пометки «нет данных» (правило полноты)
❌ не заменяет рендер обязательного блока ссылкой «см. прошлый RUN» —
КАТЕГОРИЧЕСКИ, без исключений. Значения можно переносить без
пересчёта (с провенансом), но карточка и текст блока рендерятся
ВСЕГДА. Нарушено:
— R64 (для ~15 блоков Яруса 4)
— R70 (26.07.2026, для ~25 блоков из 31)
Оба раза — тот же класс дефекта, что молчаливая потеря блоков в
R58–R61, просто другим механизмом (не забыли блок, а сослались вместо
рендера). Даже «без изменений» — это строка ВНУТРИ блока, не замена
блока целиком.
❌ не подменяет сверку с файлом сверкой с памятью о прошлом ответе —
И НАОБОРОТ: не подменяет сверку с памятью слепым доверием файлу,
если файл на диске явно старше того, что описывает свежая память
(двусторонний урок §7.9)
❌ не заменяет разделы этой инструкции ссылками на её предыдущую версию —
любая новая версия должна быть ПОЛНЫМ самодостаточным файлом
(нарушено v1.1, урок закреплён в §7.10)
❌ не составляет строку manifest по факту того, что уже написано в
ответе — сверяет её со списком блоков в STRK_REPORT_TEMPLATE.md/.html
НАПРЯМУЮ перед выдачей. R61 (реальный, найден 25.07.2026) — пример:
его собственный manifest не упомянул «фаза», хотя блок пропал,
потому что подвал сверялся сам с собой, а не с внешним списком
❌ не использует взвешенный скор вместо каскада ворот
❌ не использует z-score для силы сигнала (толстые хвосты, доказано)
❌ не покупает пробойную свечу, не входит по POC как по зоне входа
❌ не смешивает горизонты в R:R (свинг-стоп + позиционная цель)
❌ не использует балл сетапа скилла trader как «качество входа» —
только как ИНДЕКС РАСТЯНУТОСТИ, инверсно (v3.9)
❌ не печатает ML-вероятность без walk-forward AUC рядом
❌ не считает 21 день локапом стейкинга — [ИСПРАВЛЕНО 26.07.2026] 7 дней
❌ не звонит в CRYPTORANK / ask-starknet / Starkscan MCP (3 tools) /
Binance / Bybit / CoinGecko OHLC / GitHub без токена /
Nansen whale-tools по STRK / wyckoff-screener / hot-contracts-scanner
— полный реестр «ОТКЛОНЁННОЕ» в STRK_BLOCK_SKILLS.md

text

---

## 7 · Аудит инструментов и история решений — см. STRK_HISTORY.md

Полная история версий (v3.7 → v5.1), аудит инструментов (26.07.2026), ретроспективные логи RUN R58-R73, DEV-решения по методологии — вынесены в **/mnt/project/STRK_HISTORY.md** для сокращения ежедневного чтения.

LIQ и RUN не читают HISTORY. Только REVIEW и DEV читают по запросу.

Актуальные компактные ссылки:
- **Инструменты в пайплайне (Ярус A обязательные):** OKX bash, Starknet RPC lava.build, Starkscan REST, ccxt-mcp, Nansen (perps + onchain_tokens), discord, coinglass, surf, DefiLlama, simple-memory
- **Отклонённые:** CRYPTORANK MCP (заменён CryptoRank REST через web_fetch), ask-starknet (низкое качество), Swiss Whale (нет STRK), Nansen whale-tools по STRK (нули)
- **Не в пайплайне:** command-executor (Docker win32 отсутствует)

## 8 · Режимы проекта
В STRK_REPORT_TEMPLATE.md и STRK_BLOCK_SKILLS.md режимы (RUN/LIQ/REVIEW/DEV)
НИГДЕ не определены явно — упоминание нашлось только в обрывке system-rules
памяти от 09.07.2026. Определения ниже — РЕКОНСТРУКЦИЯ по фрагментам,
не подтверждённый пользователем канон. Если что-то не так — поправить здесь.

RUN основной прогон: полный отчёт по STRK_REPORT_TEMPLATE.md/.html,
ВСЕ 31 БЛОК, каскад ворот, вердикт. То, что делалось в этом
чате как R60–R70.

LIQ мониторинг открытой позиции (упомянут «Step 1.5: verify bridge whale
L2 destination when position open») — вероятно, неактуален, пока
пользователь FLAT.

REVIEW ретроспективная сверка слоёв (упомянут «cross-layer check bridge
events») — вероятно, аудит согласованности данных между L1/L2/CEX
без нового вердикта.

DEV инженерный режим: правки шаблонов, аудит потери блоков, добавление
триггеров/скиллов. Именно этим фактически была большая часть
текущего чата — создание/обновление файлов проекта, обнаружение
дефектов, разбор нумерации RUN.
Требует ОБЯЗАТЕЛЬНО обновления версии в шапке изменённых файлов
и записи в simple-memory — иначе ловушка §7.9 повторится.

text

---

## 9 · Панель режимов — текстовая строка в конце ответа (не кнопки)
STRK_BLOCK_SKILLS.md требует «ПАНЕЛЬ РЕЖИМОВ в конце КАЖДОГО ответа».

ИСТОРИЯ ПОПЫТКИ (25.07.2026): сначала реализовал через ask_user_input_v0
(тапаемые кнопки) — идея была в том, что текст легко пропустить, а кнопки
агент не забудет вызвать явным действием. Пользователь тем же днём
отменил это решение: кнопки после каждого ответа обрывают ход разговора
и вынуждают либо нажать одну из опций, либо писать поверх — вместо этого
достаточно текстовой строки, чтобы просто не забывать, что доступно.

РЕШЕНИЕ (25.07.2026, обновлено v4.6): панель режимов — простая текстовая
строка, последняя строка ответа, БЕЗ вызова инструмента. Формат:

[ RUN ✓ ] LIQ · REVIEW · DEV · SCENARIO — доступны

где галочкой отмечается режим, который использовался в этом ответе (если
использовался), остальные — просто перечислены как доступные.

Пять режимов:
RUN полный отчёт с DECISION + доказательная база (Ярусы 0-5)
LIQ минимальный отчёт: DECISION + MUST-CALC таблица + манифест;
НЕ обязателен рендер всех 31 карточек, но MUST должен быть закрыт
REVIEW ретроспективная сверка слоёв
DEV правки методологии, шаблонов, аудит потери блоков
SCENARIO внешний скилл strk-scenario-engine (пользователь вносит файл);
НЕ выдаёт торговый вердикт; НЕ пишет NEW_ENTRY/OPEN_SPEC;
симуляции с меткой [СИМУЛЯЦИЯ]; итог «Watch for DECISION»
без buy/sell. Пока скилл не загружен: агент отвечает
«strk-scenario-engine не найден в Project Knowledge»

ПРАВИЛО в конце КАЖДОГО ответа в этом проекте — строка текстом.
Не кнопки, не отдельный вызов инструмента.
ИСКЛЮЧЕНИЕ короткие уточняющие реплики без содержательного ответа —
панель не нужна, если и так ясно, что ход за агентом.

text

---

## 10 · Правило конфликта версий

Если файлы этого проекта расходятся друг с другом ИЛИ с simple-memory — побеждает более поздняя дата в заголовке/timestamp, а не то, что читается первым или кажется «основным». При равной дате: предметный файл (Template / Block-Skills) побеждает в вопросах структуры и методологии; этот файл побеждает в вопросах поведения и комплаенса.

**Добавлено v1.1, сохранено v1.2**: если файл на диске формально «единственный источник истины», но датирован раньше, чем последняя релевантная запись в simple-memory — это САМО ПО СЕБЕ повод остановиться и сверить, а не основание игнорировать память в пользу устаревшего файла (см. §7.9).

**Добавлено v1.2**: правило конфликта версий применяется и к самой этой инструкции. Если версия этого файла на диске (v1.2 сейчас) отстаёт от того, что описано в свежих записях simple-memory как актуальный протокол — файл на диске не побеждает автоматически по тому, что он «источник истины». Симметрия правила с §7.9 и §7.10.

---

## 11 · Когда эта инструкция устарела

Тот же принцип, что и у всего проекта: маркер в шапке («v1.5 · 27 июля 2026») — единственный надёжный признак актуальности. Если версия этого файла отстаёт от последней правки STRK_REPORT_TEMPLATE.md / STRK_BLOCK_SKILLS.md, или отстаёт от того, что simple-memory описывает как актуальный протокол — считать её потенциально устаревшей и свериться с пользователем, а не молчать.

---

## CHANGELOG

- v1.20 · 07.08.2026 — добавлены §0.26 Shadow-фаза (5+1 voter, накопление
  precision через 72h+7d окна перед live) и §0.27 History Layer + Covert
  Flow (единая линия для бэктеста + 6-й voter по retention/counterparties
  из edges CSV). КОНТУР A нетронут: composite_v2, confluence_gate,
  scenario_engine, decision_layer, interpretation_layer не читают
  shadow_votes.jsonl / all_history.jsonl. Соответствующий код в
  scripts/detectors/, scripts/, config/voter_config.json. Пороги всех
  6 shadow-voters помечены HYPOTHESIS. Live-inclusion только после
  N ≥ 15 closed directional AND precision ≥ 55% на обоих окнах.

- v1.13 · 06.08.2026 — добавлена §0.26 Shadow-фаза. Все пять кандидатов
  (liquidity_shift, bridge_activity, cross_token, cvd_analysis, effort_result)
  переведены в shadow. Real DECISION не изменён. Первая калибровка
  доступна после N=15 closed forecasts на окне 72h (~4 дня при 6h cadence)
  и 7d (~10 дней).