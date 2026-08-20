/**
 * Cloudflare Worker для @Lab_sector_bot webhook.
 *
 * Реализует команды: /help /status /check /list /refresh
 * Данные читаются напрямую из GitHub raw (repo public) — не polling, а webhook.
 * Latency ~1 сек (Telegram → CF Worker → GitHub raw → response).
 *
 * ДЕПЛОЙ:
 *   1. Cloudflare → Workers → Create → скопируй этот код
 *   2. Settings → Variables:
 *      - BOT_TOKEN     = <токен @Lab_sector_bot>
 *      - AUTHORIZED_CHAT_ID = 550238766 (Xenia)
 *      - GITHUB_REPO   = krol4ixa85/STRK_Engine
 *      - GITHUB_BRANCH = main
 *   3. Save & Deploy — CF даст URL типа lab-bot.your-name.workers.dev
 *   4. Открой в браузере:
 *      https://api.telegram.org/bot<TOKEN>/setWebhook?url=<WORKER_URL>
 *      Ответ: {"ok":true, "result":true, "description":"Webhook was set"}
 *   5. Напиши боту /help — ответ через ~1 сек
 */

// GitHub raw URLs (public repo — не требует auth)
const CACHE_FILES = {
    lab_snapshot: 'data/cache/strk_lab_report.json',
    momentum: 'data/cache/dune_sector_momentum.json',
    netflow: 'data/cache/dune_sector_netflow.json',
    rotation_state: 'data/cache/rotation_tracker_state.json',
    signals_summary: 'data/cache/lab_signals_summary.json',  // backtest precision
    weekly_summary: 'data/cache/weekly_summary.json',  // /week command
};

const LIQUIDITY_FLOOR = 5000;

// Playbook config
const MIN_N_FOR_PRECISION = 5;   // не показываем precision numbers если N меньше

// ============================================================
// UTILITIES
// ============================================================
function safeHtml(s) {
    if (s === null || s === undefined) return 'n/a';
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

async function githubRaw(env, path) {
    const repo = env.GITHUB_REPO;
    const branch = env.GITHUB_BRANCH || 'main';
    const url = `https://raw.githubusercontent.com/${repo}/${branch}/${path}`;
    // Обход cache через query param
    const bustUrl = `${url}?t=${Math.floor(Date.now() / 60000)}`;
    try {
        const resp = await fetch(bustUrl, { cf: { cacheTtl: 60 } });
        if (!resp.ok) return null;
        return await resp.json();
    } catch (e) {
        console.error(`Fetch failed ${path}: ${e}`);
        return null;
    }
}

function snapshotAge(snap) {
    if (!snap || !snap.generated_at) return 'unknown';
    try {
        const dt = new Date(snap.generated_at);
        const ageMs = Date.now() - dt.getTime();
        const ageH = ageMs / 3600000;
        if (ageH < 1) return `${Math.floor(ageH * 60)}m ago`;
        if (ageH < 24) return `${ageH.toFixed(1)}h ago`;
        return `${(ageH / 24).toFixed(1)}d ago`;
    } catch (e) {
        return 'unknown';
    }
}

async function tgSend(env, chatId, text, opts = {}) {
    const url = `https://api.telegram.org/bot${env.BOT_TOKEN}/sendMessage`;
    // Split long messages
    const maxLen = 4000;
    const parts = [];
    while (text.length > maxLen) {
        let cut = text.lastIndexOf('\n', maxLen);
        if (cut === -1) cut = maxLen;
        parts.push(text.slice(0, cut));
        text = text.slice(cut).trimStart();
    }
    if (text) parts.push(text);

    for (const part of parts) {
        const body = new URLSearchParams({
            chat_id: String(chatId),
            text: part,
            parse_mode: 'HTML',
            disable_web_page_preview: 'true',
        });
        try {
            await fetch(url, { method: 'POST', body });
        } catch (e) {
            console.error(`Send failed: ${e}`);
        }
    }
}

// ============================================================
// COMMANDS
// ============================================================
async function cmdHelp(env, chatId) {
    const text = `<b>🧪 LAB Bot Commands</b>

<b>📊 Общие</b>
<code>/status</code> — текущий STRK status + rotation compass
<code>/list</code> [<code>&lt;sector&gt;</code>] — все STRONG_BUY (фильтр по сектору)
<code>/check &lt;TOKEN&gt;</code> — детальный анализ токена
  Пример: <code>/check LINK</code>

<b>👁 Watchlist</b>
<code>/watch &lt;TOKEN&gt;</code> — добавить в персональный watchlist
<code>/unwatch &lt;TOKEN&gt;</code> — убрать
<code>/mywatch</code> — показать твой watchlist + свежие signals
  <i>После добавления получаешь alert только когда signal меняется</i>

<b>📅 Сводки</b>
<code>/week</code> — weekly summary (что было за 7 дней)
<code>/refresh</code> — принудительно обновить LAB data\n<code>/scan &lt;TOKEN&gt;</code> — точечный скан Dune по одному токену (5-25 кредитов вместо ~150)\n  Пример: <code>/scan LINK</code>

<b>📚 Обучение</b>
<code>/explain &lt;термин&gt;</code> — объяснение термина простыми словами
  Термины: fibonacci, atr, wyckoff, divergence, confluence, rr, streak, smart money, precision, rsi, sma, trailing stop
  Пример: <code>/explain fibonacci</code>

<code>/help</code> — эта справка

<i>💡 LAB = data-only. Все numbers из DEX volume (Dune).</i>
<i>💡 Auto-refresh: 11:30 и 23:30 MSK</i>`;
    await tgSend(env, chatId, text);
}

async function cmdStatus(env, chatId) {
    const snap = await githubRaw(env, CACHE_FILES.lab_snapshot);
    if (!snap) {
        await tgSend(env, chatId, '⚠ Нет LAB snapshot. Запусти <code>/refresh</code> или подожди утренний cron (08:30 UTC).');
        return;
    }

    const strk = snap.strk_status || {};
    const triggers = (snap.re_entry_triggers && snap.re_entry_triggers.trigger_list) || [];

    let text = `<b>📍 STRK STATUS</b>\n`;
    text += `<i>Snapshot: ${snapshotAge(snap)}</i>\n\n`;

    const verdict = strk.verdict || 'UNKNOWN';
    const emojiMap = { STILL_ACCUMULATION: '🔴', EARLY_INFLECTION: '🟡', WATCH_CLOSELY: '🟡', RE_ENTRY_ZONE: '🟢' };
    const emoji = emojiMap[verdict] || '⚪';
    text += `<b>${emoji} ${safeHtml(verdict)}</b>\n`;
    text += `Triggers hit: <code>${strk.triggers_hit || 0}/${strk.triggers_total || 4}</code>\n\n`;

    if (strk.strk_price) text += `Price: <code>$${Number(strk.strk_price).toFixed(4)}</code>\n`;
    text += `Wyckoff: <code>${safeHtml(strk.wyckoff_phase)}</code>\n`;
    text += `Dune monthly: <code>${safeHtml(strk.dune_monthly_signal)}</code>`;
    if (strk.bearish_30d !== undefined && strk.bearish_30d !== null) {
        text += ` (${strk.bearish_30d}/30d bearish)`;
    }
    text += `\nCEX: <code>${safeHtml(strk.cex_signal)}</code>\n\n`;

    text += `<b>Triggers:</b>\n`;
    for (const t of triggers) {
        if (Array.isArray(t) && t.length >= 3) {
            text += `${t[0]} <b>${safeHtml(t[1])}</b>: ${safeHtml(t[2])}\n`;
        }
    }
    text += `\n`;

    if (strk.recommendation) text += `<b>💡 ${safeHtml(strk.recommendation)}</b>\n\n`;

    const sb = snap.strong_buy || [];
    if (sb.length > 0) {
        text += `<b>🟢 STRONG_BUY (${sb.length}):</b> `;
        text += sb.slice(0, 8).map(t => `<code>${safeHtml(t.token)}</code>`).join(', ');
        text += `\n`;
    }

    await tgSend(env, chatId, text);
}

async function cmdCheck(env, chatId, tokenQuery) {
    if (!tokenQuery) {
        await tgSend(env, chatId, '⚠ Укажи токен. Пример: <code>/check LINK</code>');
        return;
    }

    const tokenUpper = tokenQuery.trim().toUpperCase();
    const [snap, momentum, netflow, rotation, signalsSummary] = await Promise.all([
        githubRaw(env, CACHE_FILES.lab_snapshot),
        githubRaw(env, CACHE_FILES.momentum),
        githubRaw(env, CACHE_FILES.netflow),
        githubRaw(env, CACHE_FILES.rotation_state),
        githubRaw(env, CACHE_FILES.signals_summary),
    ]);

    if (!snap && !momentum && !netflow) {
        await tgSend(env, chatId, '⚠ Нет данных. Запусти <code>/refresh</code>.');
        return;
    }

    // Find in momentum (price + signal)
    let momRow = null;
    if (momentum && momentum.rows) {
        for (const r of momentum.rows) {
            if (r && String(r.token || '').toUpperCase() === tokenUpper) {
                momRow = r;
                break;
            }
        }
    }

    // Find in netflow (direction)
    let nfRow = null;
    if (netflow && netflow.rows) {
        for (const r of netflow.rows) {
            if (r && String(r.token || '').toUpperCase() === tokenUpper) {
                nfRow = r;
                break;
            }
        }
    }

    if (!momRow && !nfRow) {
        // Fallback: check LAB snapshot
        let found = null;
        for (const section of ['strong_buy', 'divergence', 'buy_pressure', 'sell']) {
            for (const t of (snap && snap[section]) || []) {
                if (String(t.token || '').toUpperCase() === tokenUpper) {
                    found = { section, t };
                    break;
                }
            }
            if (found) break;
        }
        if (!found) {
            await tgSend(env, chatId,
                `⚠ Токен <code>${safeHtml(tokenUpper)}</code> не найден в universe.\n\n<i>Проверь список tracked токенов через /list.</i>`);
            return;
        }
        let text = `<b>📊 ${safeHtml(tokenUpper)}</b> (${safeHtml(found.t.sector)})\n\n`;
        text += `Signal: <code>${safeHtml(found.t.signal || found.section.toUpperCase())}</code>\n`;
        const nf = found.t.net_flow_m_usd || 0;
        text += `Net flow 7d: <code>${nf >= 0 ? '+' : ''}${nf.toFixed(2)}M USD</code>\n`;
        await tgSend(env, chatId, text);
        return;
    }

    // Build detailed response
    const sector = (momRow || nfRow).sector || 'unknown';
    let text = `<b>📊 ${safeHtml(tokenUpper)}</b> · <i>${safeHtml(sector)}</i>\n`;
    text += `<i>Snapshot: ${snapshotAge(snap)}</i>\n\n`;

    if (momRow) {
        const signal = momRow.signal || 'NEUTRAL';
        const sigEmoji = { STRONG_BUY: '🟢', STRONG_SELL: '🔴', DIVERGENCE: '⚠', NEUTRAL_FLOW_UP: '⚪', NEUTRAL: '⚪' }[signal] || '⚪';
        text += `<b>${sigEmoji} Signal: <code>${safeHtml(signal)}</code></b>\n\n`;

        text += `<b>Flow:</b>\n`;
        const buy = momRow.buy_volume_m_usd || 0;
        const sell = momRow.sell_volume_m_usd || 0;
        const net = momRow.net_flow_m_usd || 0;
        text += `  Buy vol 7d:   <code>$${buy.toFixed(2)}M</code>\n`;
        text += `  Sell vol 7d:  <code>$${sell.toFixed(2)}M</code>\n`;
        text += `  Net flow:     <code>${net >= 0 ? '+' : ''}${net.toFixed(2)}M USD</code>`;
        if (momRow.net_flow_pct !== undefined && momRow.net_flow_pct !== null) {
            text += ` (${momRow.net_flow_pct >= 0 ? '+' : ''}${momRow.net_flow_pct.toFixed(1)}%)`;
        }
        text += `\n\n`;

        text += `<b>Price:</b>\n`;
        if (momRow.price_now) text += `  Now:     <code>$${Number(momRow.price_now).toFixed(4)}</code>\n`;
        if (momRow.price_7d_ago) text += `  7d ago:  <code>$${Number(momRow.price_7d_ago).toFixed(4)}</code>\n`;
        const pc = momRow.price_change_7d_pct || 0;
        text += `  Change:  <code>${pc >= 0 ? '+' : ''}${pc.toFixed(1)}%</code>\n\n`;

        const tx = momRow.tx_count || 0;
        text += `<b>Liquidity:</b> <code>${tx.toLocaleString()} tx</code> за 7d `;
        text += tx > 50000 ? '(HIGH)' : tx > 10000 ? '(MEDIUM)' : '(LOW)';
        text += `\n\n`;
    }

    // Streak from rotation tracker
    if (rotation && rotation.signal_streaks) {
        const streak = rotation.signal_streaks[tokenUpper];
        if (streak) {
            text += `<b>📅 STRONG_BUY streak:</b> <code>${streak}d</code>`;
            if (streak >= 3) text += ` ✅ CONFIRMED`;
            text += `\n\n`;
        }
    }

    // Assessment
    if (momRow) {
        const signal = momRow.signal || 'NEUTRAL';
        const net = momRow.net_flow_m_usd || 0;
        const pc = momRow.price_change_7d_pct || 0;
        const tx = momRow.tx_count || 0;

        text += `<b>Assessment:</b>\n`;
        if (signal === 'STRONG_BUY') {
            if (net > 10 && pc > 10 && tx > 50000) {
                text += `  🟢 <b>Strong confluence</b> — significant flow + price + liquidity.\n`;
            } else if (net > 1 && pc > 5) {
                text += `  🟢 Moderate STRONG_BUY.\n`;
            } else {
                text += `  🟡 STRONG_BUY signal, но numbers скромные.\n`;
            }
        } else if (signal === 'DIVERGENCE') {
            text += `  ⚠ Price rally без buy flow — часто fake breakout.\n`;
        } else if (signal === 'STRONG_SELL' || signal === 'SELL_PRESSURE') {
            text += `  🔴 Distribution — избегать входа.\n`;
        } else {
            text += `  ⚪ ${signal} — no clear edge.\n`;
        }
    }

    text += `\n<b>📊 Historical precision (7d threshold ±3%):</b>\n`;
    // Real precision from backtest summary
    let precisionShown = false;
    if (signalsSummary) {
        const perToken = signalsSummary.per_token || {};
        const tokenStats = perToken[tokenUpper];
        if (tokenStats && tokenStats.has_enough_data && tokenStats.precision_pct !== null) {
            const conf = tokenStats.n_actionable >= 15 ? '' : ' <i>(wide CI · N low)</i>';
            text += `  <code>${tokenUpper}</code>: <code>${tokenStats.precision_pct}%</code> `;
            text += `(${tokenStats.hits}/${tokenStats.n_actionable} last 30d)${conf}\n`;
            if (tokenStats.avg_return_pct !== null) {
                const avgSign = tokenStats.avg_return_pct >= 0 ? '+' : '';
                text += `  Avg return: <code>${avgSign}${tokenStats.avg_return_pct}%</code>\n`;
            }
            precisionShown = true;
        }
        // Overall precision as fallback context
        const overall = signalsSummary.overall || {};
        if (overall.has_enough_data && overall.precision_pct !== null) {
            const conf = overall.high_confidence ? '' : ' <i>(early data)</i>';
            text += `  <b>Overall STRONG_BUY</b>: <code>${overall.precision_pct}%</code> `;
            text += `(${overall.hits}/${overall.n_actionable} last 30d)${conf}\n`;
            precisionShown = true;
        }
    }
    if (!precisionShown) {
        text += `  <i>Ещё measuring (N &lt; 5). Первые числа появятся через 7+ дней.</i>\n`;
    }

    text += `\n<i>💡 Not advice. Проверяй технику отдельно.</i>`;

    await tgSend(env, chatId, text);
}

async function cmdList(env, chatId, sectorFilter) {
    const [momentum, snap] = await Promise.all([
        githubRaw(env, CACHE_FILES.momentum),
        githubRaw(env, CACHE_FILES.lab_snapshot),
    ]);

    if (!momentum || !momentum.rows) {
        await tgSend(env, chatId, '⚠ Нет momentum data. Запусти <code>/refresh</code>.');
        return;
    }

    const sectorUpper = sectorFilter ? sectorFilter.trim().toUpperCase() : null;
    const buckets = { STRONG_BUY: [], DIVERGENCE: [], STRONG_SELL: [] };
    const seen = new Set();  // dedup by token

    for (const r of momentum.rows) {
        if (!r) continue;
        const token = String(r.token || '').toUpperCase();
        if (!token) continue;
        // Skip duplicates (LINK может быть в INFRA и RWA)
        if (seen.has(token)) continue;
        const tx = r.tx_count || 0;
        if (tx < LIQUIDITY_FLOOR) continue;
        if (sectorUpper && String(r.sector || '').toUpperCase() !== sectorUpper) continue;
        const sig = r.signal || 'NEUTRAL';
        if (buckets[sig]) {
            buckets[sig].push(r);
            seen.add(token);
        }
    }

    for (const key of Object.keys(buckets)) {
        buckets[key].sort((a, b) => (b.net_flow_m_usd || 0) - (a.net_flow_m_usd || 0));
    }

    let text = `<b>📋 LAB Tokens</b>`;
    if (sectorUpper) text += ` · sector: <code>${safeHtml(sectorUpper)}</code>`;
    // Use snapshot age from strk_lab_report.json (fallback to collected_at from momentum)
    let ageStr = 'unknown';
    if (snap && snap.generated_at) {
        ageStr = snapshotAge(snap);
    } else if (momentum.collected_at) {
        ageStr = snapshotAge({ generated_at: momentum.collected_at });
    }
    text += `\n<i>Snapshot: ${ageStr}</i>\n\n`;

    let totalShown = 0;

    if (buckets.STRONG_BUY.length > 0) {
        text += `<b>🟢 STRONG_BUY (${buckets.STRONG_BUY.length})</b>\n`;
        for (const r of buckets.STRONG_BUY.slice(0, 8)) {
            const net = r.net_flow_m_usd || 0;
            const pc = r.price_change_7d_pct || 0;
            text += `  <code>${(r.token || '').padEnd(7)}</code> `;
            text += `(${safeHtml(r.sector)}) `;
            text += `net <code>${net >= 0 ? '+' : ''}${net.toFixed(2)}M</code> · `;
            text += `<code>${pc >= 0 ? '+' : ''}${pc.toFixed(1)}%</code>\n`;
        }
        totalShown += buckets.STRONG_BUY.length;
        text += `\n`;
    }

    if (buckets.DIVERGENCE.length > 0) {
        text += `<b>⚠ DIVERGENCE (${buckets.DIVERGENCE.length})</b>\n`;
        for (const r of buckets.DIVERGENCE.slice(0, 5)) {
            const net = r.net_flow_m_usd || 0;
            const pc = r.price_change_7d_pct || 0;
            text += `  <code>${(r.token || '').padEnd(7)}</code> `;
            text += `net <code>${net >= 0 ? '+' : ''}${net.toFixed(2)}M</code> · `;
            text += `price <code>${pc >= 0 ? '+' : ''}${pc.toFixed(1)}%</code>\n`;
        }
        totalShown += buckets.DIVERGENCE.length;
        text += `\n`;
    }

    if (buckets.STRONG_SELL.length > 0) {
        text += `<b>🔴 STRONG_SELL (${buckets.STRONG_SELL.length})</b>\n`;
        for (const r of buckets.STRONG_SELL.slice(0, 5)) {
            const net = r.net_flow_m_usd || 0;
            const pc = r.price_change_7d_pct || 0;
            text += `  <code>${(r.token || '').padEnd(7)}</code> `;
            text += `net <code>${net >= 0 ? '+' : ''}${net.toFixed(2)}M</code> · `;
            text += `<code>${pc >= 0 ? '+' : ''}${pc.toFixed(1)}%</code>\n`;
        }
        totalShown += buckets.STRONG_SELL.length;
    }

    if (totalShown === 0) {
        text += `<i>Нет tokens подходящих под фильтр (или все с tx_count &lt; 5000).</i>\n\n`;
        const sectors = [...new Set(momentum.rows.map(r => r && r.sector).filter(Boolean))].sort();
        if (sectorFilter && sectors.length > 0) {
            text += `<b>Available sectors:</b> ` + sectors.map(s => `<code>${s}</code>`).join(', ');
        }
    }

    await tgSend(env, chatId, text);
}

async function cmdRefresh(env, chatId) {
    // Если PAT установлен — реально triggers workflow, иначе показывает инструкцию
    if (!env.GITHUB_PAT) {
        const text = `<b>🔄 Refresh LAB</b>

<b>Auto-dispatch не настроен</b> — добавь <code>GITHUB_PAT</code> в CF Worker Variables.

<b>Как настроить:</b>
1. GitHub → Settings → Developer settings → PAT (fine-grained tokens)
2. Generate → выбери repo <code>STRK_Engine</code>
3. Permissions → Actions: <b>Read and write</b>
4. Copy token → добавь в CF Worker Variables как <code>GITHUB_PAT</code> (Secret)

<b>Пока — ручной запуск:</b>
GitHub Actions → STRK Engine → Run workflow → mode = <code>lab</code>

<b>Автоматически:</b> 2x/сутки — 11:30 MSK (утро) + 23:30 MSK (вечер)`;
        await tgSend(env, chatId, text);
        return;
    }

    // Trigger workflow_dispatch
    const [owner, repo] = env.GITHUB_REPO.split('/');
    const workflowFile = 'strk_engine.yml';
    const url = `https://api.github.com/repos/${owner}/${repo}/actions/workflows/${workflowFile}/dispatches`;

    try {
        const resp = await fetch(url, {
            method: 'POST',
            headers: {
                'Authorization': `Bearer ${env.GITHUB_PAT}`,
                'Accept': 'application/vnd.github+json',
                'Content-Type': 'application/json',
                'User-Agent': 'lab-bot-worker',
                'X-GitHub-Api-Version': '2022-11-28',
            },
            body: JSON.stringify({
                ref: env.GITHUB_BRANCH || 'main',
                inputs: { mode: 'lab' },
            }),
        });

        if (resp.status === 204) {
            const text = `<b>🔄 Refresh LAB запущен</b>

Workflow dispatched · Actions → STRK Engine

⏱ Обычно занимает 2-5 минут:
  1. Dune API queries (sector data)
  2. Snapshot save
  3. LAB report → @Lab_sector_bot
  4. Rotation tracker alerts (if any)

<i>💡 Обычные автоматические запуски: 11:30 и 23:30 MSK</i>
<i>💡 Не спамь /refresh — Dune credits ограничены на месяц</i>`;
            await tgSend(env, chatId, text);
        } else {
            const errBody = await resp.text();
            const text = `<b>❌ Refresh failed</b>

HTTP ${resp.status}
${safeHtml(errBody.slice(0, 200))}

<i>Проверь GITHUB_PAT в CF Variables — есть ли Actions: Read and write?</i>`;
            await tgSend(env, chatId, text);
        }
    } catch (e) {
        await tgSend(env, chatId, `❌ Refresh error: ${safeHtml(String(e).slice(0, 200))}`);
    }
}

// ============================================================
// DISPATCH
// ============================================================
async function cmdWeek(env, chatId) {
    const summary = await githubRaw(env, CACHE_FILES.weekly_summary);
    if (!summary) {
        await tgSend(env, chatId,
            '⚠ Weekly summary ещё не создан. Первый запуск: воскресенье 00:00 MSK.\n\n' +
            '<i>Или в GitHub Actions запусти workflow вручную с /refresh.</i>');
        return;
    }

    const now = new Date();
    let text = `<b>📅 STRK LAB · WEEKLY SUMMARY</b>\n`;
    text += `<i>Snapshot: ${snapshotAge(summary)}</i>\n\n`;

    // STRK STATUS
    const strk = summary.strk_status || {};
    text += `━━━━━━━━━━━━━━━━━━━\n<b>📍 STRK STATE</b>\n━━━━━━━━━━━━━━━━━━━\n`;
    const emojiMap = { STILL_ACCUMULATION: '🔴', EARLY_INFLECTION: '🟡', WATCH_CLOSELY: '🟡', RE_ENTRY_ZONE: '🟢' };
    const emoji = emojiMap[strk.verdict] || '⚪';
    text += `${emoji} <b>${safeHtml(strk.verdict)}</b>\n`;
    text += `Triggers: <code>${strk.triggers_hit || 0}/${strk.triggers_total || 4}</code>\n`;
    if (strk.strk_price) text += `Price: <code>$${Number(strk.strk_price).toFixed(4)}</code>\n`;
    if (strk.bearish_30d !== undefined) {
        text += `Dune: ${safeHtml(strk.dune_monthly_signal)} (${strk.bearish_30d}/30d bearish)\n`;
    }
    text += `\n`;

    // LAB SIGNALS
    const ls = summary.lab_signals_7d || {};
    text += `━━━━━━━━━━━━━━━━━━━\n<b>🟢 LAB SIGNALS (7d)</b>\n━━━━━━━━━━━━━━━━━━━\n`;
    text += `Issued:   <code>${ls.total_issued || 0}</code>\n`;
    text += `Closed:   <code>${ls.total_closed || 0}</code>`;
    if (ls.total_closed) {
        text += ` · HIT <code>${ls.hits || 0}</code>, MISS <code>${ls.misses || 0}</code>, NEUTRAL <code>${ls.neutrals || 0}</code>`;
    }
    text += `\n`;
    if (ls.precision_7d !== null && ls.precision_7d !== undefined) {
        text += `Precision (7d): <code>${ls.precision_7d}%</code>\n`;
    }

    const topActive = ls.top_active_tokens || [];
    if (topActive.length > 0) {
        text += `\n<b>Top active:</b>\n`;
        for (const [tk, count] of topActive.slice(0, 5)) {
            text += `  <code>${safeHtml(tk)}</code>: ${count} issues\n`;
        }
    }

    const sectors = ls.sectors_hit || {};
    if (Object.keys(sectors).length > 0) {
        text += `\n<b>Sectors:</b> `;
        const sorted = Object.entries(sectors).sort((a, b) => b[1] - a[1]);
        text += sorted.slice(0, 6).map(([s, c]) => `<code>${safeHtml(s)}</code>×${c}`).join(', ');
        text += `\n`;
    }
    text += `\n`;

    // STREAKS
    const streaks = summary.top_streaks || [];
    if (streaks.length > 0) {
        text += `━━━━━━━━━━━━━━━━━━━\n<b>🏆 TOP STREAKS (7d)</b>\n━━━━━━━━━━━━━━━━━━━\n`;
        for (const st of streaks.slice(0, 5)) {
            text += `  <code>${(st.token || '').padEnd(7)}</code> · <code>${st.duration_days}d</code> streak · ${st.issues_count} issues\n`;
        }
        text += `\n`;
    }

    // ROTATION EVENTS
    const rot = summary.rotation_events_7d || {};
    const totalEvents = (rot.new_strong_buy || []).length + (rot.exited || []).length +
                        (rot.divergence_warn || []).length + (rot.sell_signals || []).length;
    if (totalEvents > 0) {
        text += `━━━━━━━━━━━━━━━━━━━\n<b>🔄 ROTATION EVENTS (7d)</b>\n━━━━━━━━━━━━━━━━━━━\n`;
        if (rot.new_strong_buy && rot.new_strong_buy.length > 0) {
            text += `📈 NEW STRONG_BUY: <code>${safeHtml(rot.new_strong_buy.join(', '))}</code>\n`;
        }
        if (rot.exited && rot.exited.length > 0) {
            text += `⚪ Exited: <code>${safeHtml(rot.exited.join(', '))}</code>\n`;
        }
        if (rot.divergence_warn && rot.divergence_warn.length > 0) {
            text += `⚠ Divergence: <code>${safeHtml(rot.divergence_warn.join(', '))}</code>\n`;
        }
        if (rot.sell_signals && rot.sell_signals.length > 0) {
            text += `🚪 Sell: <code>${safeHtml(rot.sell_signals.join(', '))}</code>\n`;
        }
        text += `\n`;
    }

    // BACKTEST OVERALL
    const bt = summary.backtest_overall;
    if (bt && bt.n_actionable) {
        text += `━━━━━━━━━━━━━━━━━━━\n<b>📊 BACKTEST (all-time)</b>\n━━━━━━━━━━━━━━━━━━━\n`;
        text += `N closed: <code>${bt.n_closed}</code> (actionable <code>${bt.n_actionable}</code>)\n`;
        if (bt.precision_pct !== null && bt.has_enough_data) {
            const conf = bt.high_confidence ? 'HIGH' : 'early (wide CI)';
            text += `Precision: <code>${bt.precision_pct}%</code> <i>(${conf})</i>\n`;
        }
        text += `\n`;
    }

    // TOP PRECISION
    const topPrec = summary.top_precision_tokens || [];
    if (topPrec.length > 0) {
        text += `<b>🎯 Top precision tokens:</b>\n`;
        for (const t of topPrec.slice(0, 5)) {
            const avg = t.avg_return_pct;
            const avgStr = avg !== null && avg !== undefined ? ` · avg <code>${avg >= 0 ? '+' : ''}${avg.toFixed(1)}%</code>` : '';
            text += `  <code>${(t.token || '').padEnd(7)}</code> <code>${t.precision_pct}%</code> (N=${t.n_actionable})${avgStr}\n`;
        }
        text += `\n`;
    }

    text += `<i>💡 Weekly cron: воскресенье 00:00 MSK · ручной запрос /week</i>\n`;
    text += `<i>💡 Backtest precision появится через 7+ дней</i>`;

    await tgSend(env, chatId, text);
}

// ============================================================
// GLOSSARY · /explain <term>
// ============================================================
const GLOSSARY = {
    'fibonacci': {
        title: 'Fibonacci retracement',
        text: `<b>📐 Fibonacci retracement</b>

Простыми словами: инструмент чтобы понять <b>куда цена может откатиться</b> после сильного движения.

<b>Уровни:</b> 23.6% · 38.2% · <b>50%</b> · <b>61.8%</b> · <b>78.6%</b>

Самые важные — 61.8% и 78.6%. Это места где рынок часто "останавливается" после отката.

<b>Пример:</b>
LINK вырос с $8.36 → $10.50 (рост $2.14)
78.6% откат = $8.36 + $2.14 × 0.786 = $10.04
Это уровень куда цена может откатиться перед продолжением

<b>Точность:</b> ~40-55% на крипте. Работает потому что <b>многие трейдеры</b> используют — самосбывающееся пророчество.

<b>⚠ Не magic:</b> уровень — это <b>область</b> ($9.95-10.10), не точная цифра.`
    },
    'atr': {
        title: 'ATR (Average True Range)',
        text: `<b>📊 ATR — Average True Range</b>

Простыми словами: <b>сколько цена обычно колеблется</b> за день.

<b>Формула:</b> средний размах цены (high - low) за N дней (обычно 14).

<b>Пример:</b>
Если ATR у LINK = $0.75, значит цена обычно ходит на $0.75 в день.
Стоп на $1.50 ниже = 2 ATR — очень safe.
Стоп на $0.30 ниже = 0.4 ATR — сработает от шума.

<b>Как используем:</b>
Trailing stop <b>1-2 ATR</b> от текущей цены — защита от нормальных колебаний.

<b>Слабость:</b>
В flash crash ATR не спасёт (цена улетит быстрее чем сработает стоп).`
    },
    'trailing stop': {
        title: 'Trailing stop',
        text: `<b>📉 Trailing stop</b>

Простыми словами: <b>стоп-лосс который движется за ценой вверх</b>, но не вниз.

<b>Пример:</b>
Купила LINK за $9.50, trailing stop 8%.
Цена растёт до $10.00 → стоп поднимается до $9.20
Цена растёт до $11.00 → стоп поднимается до $10.12
Цена падает до $10.12 → <b>автоматическая продажа</b>

<b>Зачем:</b>
Даёт цене расти сколько хочет, но защищает от разворота.

<b>Как выбрать %:</b>
Слишком узко (3-5%) → выкинет от шума
Слишком широко (15%+) → отдашь большой профит
Оптимум: <b>1.5-2 ATR</b> или <b>8-10% для крипты</b>`
    },
    'trailing': {
        title: 'Trailing stop',
        text: `Смотри <code>/explain trailing stop</code>`
    },
    'wyckoff': {
        title: 'Wyckoff phases',
        text: `<b>📚 Wyckoff — 4 фазы рынка</b>

Классификация по методу Richard Wyckoff (1930-е).

<b>1. ACCUMULATION (накопление) 🔵</b>
Умные деньги <b>тихо покупают</b>. Цена в боковике на низких уровнях.
STRK сейчас в этой фазе.

<b>2. MARKUP (рост) 🟢</b>
Начало bullish тренда. Цена растёт, объём растёт.
Это то что ты ждёшь для STRK.

<b>3. DISTRIBUTION (распределение) 🔴</b>
Умные деньги <b>тихо продают</b>. Цена в боковике на высоких уровнях.
Retail покупает FOMO.

<b>4. MARKDOWN (падение) 🔻</b>
Начало bearish тренда. Цена падает, объём растёт на падениях.

<b>Слабость метода:</b>
Фазы длятся <b>месяцами</b>. Не помогает для 7-дневных решений.
Разные аналитики видят разные фазы на одном графике.`
    },
    'confluence': {
        title: 'Confluence',
        text: `<b>🎯 Confluence — совпадение сигналов</b>

Простыми словами: <b>несколько независимых признаков</b> указывают в одну сторону.

<b>Пример bullish confluence:</b>
✓ Цена на 61.8% Fibo (technical)
✓ Net flow +$25M за 7d (on-chain)
✓ 5 дней подряд STRONG_BUY (momentum)
= Confluence → сигнал <b>сильнее</b> чем каждый по отдельности

<b>Логика:</b>
Один сигнал = 55% precision (мало)
Три независимых по 55% = ~75%+ precision (лучше)

<b>Осторожно:</b>
Если сигналы <b>коррелированы</b> (не независимы) — confluence обман.
Пример: цена растёт → RSI растёт → MACD растёт. Это <b>один</b> сигнал в 3 обёртках.`
    },
    'divergence': {
        title: 'Divergence',
        text: `<b>⚡ Divergence — расхождение</b>

Простыми словами: <b>цена и volume идут в разные стороны</b>.

<b>Bearish divergence (типичный сейчас у LINK):</b>
Цена растёт (+15%) ↑
Но buy volume падает (-40%) ↓
= "Кто-то держит цену, но новых покупателей нет"

<b>Что часто следует:</b>
Через 3-7 дней цена <b>догоняет</b> volume — идёт вниз.

<b>Bullish divergence:</b>
Цена падает ↓
Но buy volume растёт ↑
= "Умные деньги покупают на дне"

<b>Точность:</b>
~50-65% на крипте когда N ≥ 3 дня.
Часто fake на 1-day movement — жди подтверждения 2+ дня.`
    },
    'rr': {
        title: 'R/R (Risk/Reward)',
        text: `<b>⚖ R/R — Risk/Reward ratio</b>

Простыми словами: <b>сколько потенциальный профит vs риск</b>.

<b>Формула:</b> R/R = (target - entry) / (entry - stop)

<b>Пример:</b>
Entry: $10.00
Stop: $9.20 (риск $0.80)
Target: $12.40 (профит $2.40)
R/R = 2.40 / 0.80 = <b>3.0 (или 1:3)</b>

<b>Как читать:</b>
1:1 → рискуешь $1 чтобы заработать $1 (слабо)
1:2 → минимум чтобы это работало
1:3+ → хороший setup
1:5+ → редко реалистично

<b>⚠ Важно:</b>
R/R 1:3 <b>не гарантирует</b> прибыль!
Если precision setup 30% → в убытке даже с R/R 1:3.
Нужно: precision × win_size &gt; (1-precision) × loss_size

<b>Реалистичный target:</b>
R/R 1:2 при precision 55% = долгосрочный + edge`
    },
    'r/r': {
        title: 'R/R',
        text: `Смотри <code>/explain rr</code>`
    },
    'precision': {
        title: 'Precision',
        text: `<b>📊 Precision — точность сигнала</b>

Простыми словами: <b>из 100 сигналов сколько оказались правильными</b>.

<b>Пример:</b>
STRONG_BUY сработал 100 раз
Из них 62 раза цена выросла на +3% за 7 дней
Precision = 62%

<b>Как читать:</b>
&lt; 50% → хуже random
50-55% → слабый edge (нужен большой win/loss ratio)
55-65% → нормальный edge для manual trading
&gt; 70% → редкость, часто overfit

<b>Важно:</b>
Precision <b>без N</b> ничего не значит.
"70% precision" на N=3 = шум (может быть 30-90% реально)
"55% precision" на N=100 = <b>надёжно</b>

<b>В нашей системе:</b>
N &lt; 5 → скрываем цифры
N ≥ 15 → показываем но с caveat "wide CI"
N ≥ 30 → уверенно можно использовать`
    },
    'streak': {
        title: 'Streak',
        text: `<b>🔥 Streak — сколько дней подряд</b>

Простыми словами: <b>сколько снепшотов подряд</b> токен был в STRONG_BUY.

<b>В нашей системе:</b>
Snapshot 1 (день 1): LINK STRONG_BUY → streak = 1
Snapshot 2 (день 2): LINK STRONG_BUY → streak = 2
Snapshot 3 (день 3): LINK STRONG_BUY → streak = 3 → <b>CONFIRMED HOLD</b> alert
Snapshot 4 (день 4): LINK STRONG_BUY → streak = 4
Snapshot 5 (день 5): LINK DIVERGENCE → streak = 0 (сброс)

<b>Почему это важно:</b>
Streak 1-2 дня = может быть шум
Streak 3+ дня = устойчивый паттерн
Streak 5+ дней = очень сильно, но и <b>перекуплен</b> уже`
    },
    'smart money': {
        title: 'Smart Money',
        text: `<b>💼 Smart money</b>

Простыми словами: <b>крупные игроки</b> (институции, funds, whales) с большим капиталом и данными.

<b>Как их узнают:</b>
✓ Большие транзакции ($100K+ за раз)
✓ Timing (входят до news, выходят до dump)
✓ Кошельки известны (labeled on-chain)

<b>Классический pattern:</b>
"Smart money exit" = крупные продают, retail продолжает покупать
Signature: <b>net flow негативный</b> но price ещё растёт
= Divergence bearish

<b>В нашей системе:</b>
Не отслеживаем конкретные wallets (нужна Nansen подписка).
Косвенно видим через <b>net flow flip</b> и <b>volume patterns</b>.

<b>Осторожно:</b>
Не всегда правы! Smart money тоже теряет.
Precision их signals ~55-65%.`
    },
    'fibo': {
        title: 'Fibonacci',
        text: `Смотри <code>/explain fibonacci</code>`
    },
    'sma': {
        title: 'SMA — Simple Moving Average',
        text: `<b>📈 SMA — Простое скользящее среднее</b>

Простыми словами: <b>средняя цена</b> за последние N дней.

<b>Пример:</b>
SMA20 у LINK: среднее закрытие за 20 дней = $9.10
Если цена $10.20 → выше SMA20 → тренд bullish
Если цена $8.90 → ниже SMA20 → тренд bearish

<b>Популярные периоды:</b>
SMA20 — короткий тренд (месяц)
SMA50 — средний тренд
SMA200 — долгий тренд (год)

<b>Классический сигнал:</b>
SMA50 пересекает SMA200 <b>снизу вверх</b> = <b>Golden Cross</b> (bullish)
SMA50 пересекает SMA200 <b>сверху вниз</b> = <b>Death Cross</b> (bearish)

<b>Слабость:</b>
Отстающий индикатор. К моменту сигнала треть движения уже произошла.`
    },
    'rsi': {
        title: 'RSI — Relative Strength Index',
        text: `<b>📊 RSI — Индекс относительной силы</b>

Простыми словами: <b>шкала 0-100 показывает перекупленность/перепроданность</b>.

<b>Зоны:</b>
RSI &gt; 70 → перекупленность (может упасть)
RSI 30-70 → нормальная зона
RSI &lt; 30 → перепроданность (может расти)

<b>Пример:</b>
STRK RSI = 25 → сильно перепродан, часто это dip где smart money покупает

<b>Осторожно:</b>
RSI может оставаться &gt; 70 <b>долго</b> в сильном bull trend
RSI может оставаться &lt; 30 <b>долго</b> в сильном bear trend
Не используй как <b>изолированный</b> signal — комбинируй с volume/trend`
    },
};

async function cmdExplain(env, chatId, term) {
    if (!term) {
        // Show list of available terms
        let text = `<b>📚 Available explanations</b>\n\n`;
        text += `Отправь <code>/explain &lt;термин&gt;</code>\n\n`;
        text += `<b>Доступные термины:</b>\n`;
        const shown = new Set();
        for (const [key, val] of Object.entries(GLOSSARY)) {
            // Skip aliases (text starting with "Смотри")
            if (val.text.startsWith('Смотри')) continue;
            if (shown.has(val.title)) continue;
            shown.add(val.title);
            text += `  <code>/explain ${key}</code> — ${val.title}\n`;
        }
        text += `\n<i>💡 Больше терминов будет добавляться по мере развития. Скажи какие ещё нужны.</i>`;
        await tgSend(env, chatId, text);
        return;
    }

    const key = term.trim().toLowerCase();
    const entry = GLOSSARY[key];
    if (!entry) {
        // Fuzzy match — попробуем найти близкое
        const keys = Object.keys(GLOSSARY);
        const matches = keys.filter(k => k.includes(key) || key.includes(k));
        if (matches.length > 0) {
            let suggest = `⚠ Термин <code>${safeHtml(term)}</code> не найден.\n\n`;
            suggest += `Может ты имела в виду:\n`;
            for (const m of matches.slice(0, 3)) {
                suggest += `  <code>/explain ${m}</code>\n`;
            }
            await tgSend(env, chatId, suggest);
            return;
        }
        await tgSend(env, chatId,
            `⚠ Термин <code>${safeHtml(term)}</code> не найден.\n\n` +
            `Отправь <code>/explain</code> без аргумента чтобы увидеть все доступные.`);
        return;
    }

    await tgSend(env, chatId, entry.text);
}

// ============================================================
// WATCHLIST · CF KV storage
// ============================================================
async function getWatchlist(env, chatId) {
    if (!env.WATCHLIST_KV) return [];
    try {
        const raw = await env.WATCHLIST_KV.get(`watch:${chatId}`);
        return raw ? JSON.parse(raw) : [];
    } catch (e) {
        console.error(`Watchlist read failed: ${e}`);
        return [];
    }
}

async function saveWatchlist(env, chatId, tokens) {
    if (!env.WATCHLIST_KV) {
        console.error('WATCHLIST_KV binding not configured');
        return false;
    }
    try {
        await env.WATCHLIST_KV.put(`watch:${chatId}`, JSON.stringify(tokens));
        return true;
    } catch (e) {
        console.error(`Watchlist write failed: ${e}`);
        return false;
    }
}

async function cmdWatch(env, chatId, tokenQuery) {
    if (!env.WATCHLIST_KV) {
        await tgSend(env, chatId,
            `⚠ Watchlist storage не настроен.\n\n<b>Нужно настроить CF KV binding:</b>\n` +
            `1. CF Dashboard → Workers & Pages → KV\n` +
            `2. Create namespace <code>lab_watchlist</code>\n` +
            `3. Worker Settings → Bindings → KV Namespace\n` +
            `4. Variable name: <code>WATCHLIST_KV</code>\n` +
            `5. Save and deploy\n\n` +
            `После настройки команды <code>/watch</code>, <code>/unwatch</code>, <code>/mywatch</code> заработают.`);
        return;
    }

    if (!tokenQuery) {
        await tgSend(env, chatId, '⚠ Укажи токен. Пример: <code>/watch LINK</code>');
        return;
    }

    const token = tokenQuery.trim().toUpperCase();
    const list = await getWatchlist(env, chatId);
    if (list.includes(token)) {
        await tgSend(env, chatId, `⚠ <code>${safeHtml(token)}</code> уже в твоём watchlist.`);
        return;
    }
    list.push(token);
    const ok = await saveWatchlist(env, chatId, list);
    if (ok) {
        await tgSend(env, chatId,
            `✓ <code>${safeHtml(token)}</code> добавлен в watchlist (${list.length} tokens).\n\n` +
            `<i>💡 Получишь alert только когда signal этого токена изменится (без спама).</i>\n` +
            `<i>💡 Проверить весь watchlist: /mywatch</i>`);
    } else {
        await tgSend(env, chatId, '⚠ Не удалось сохранить. Проверь KV binding в CF.');
    }
}

async function cmdUnwatch(env, chatId, tokenQuery) {
    if (!tokenQuery) {
        await tgSend(env, chatId, '⚠ Укажи токен. Пример: <code>/unwatch LINK</code>');
        return;
    }
    const token = tokenQuery.trim().toUpperCase();
    let list = await getWatchlist(env, chatId);
    if (!list.includes(token)) {
        await tgSend(env, chatId, `⚠ <code>${safeHtml(token)}</code> нет в watchlist.`);
        return;
    }
    list = list.filter(t => t !== token);
    await saveWatchlist(env, chatId, list);
    await tgSend(env, chatId, `✓ <code>${safeHtml(token)}</code> убран из watchlist (${list.length} остались).`);
}

async function cmdMywatch(env, chatId) {
    const list = await getWatchlist(env, chatId);
    if (list.length === 0) {
        await tgSend(env, chatId,
            `📋 Watchlist пустой.\n\n` +
            `Добавь: <code>/watch LINK</code>\n\n` +
            `<i>💡 После добавления, я буду присылать alert только когда signal этих токенов меняется.</i>`);
        return;
    }

    // Load latest signals для watched tokens
    const momentum = await githubRaw(env, CACHE_FILES.momentum);
    const rotation = await githubRaw(env, CACHE_FILES.rotation_state);

    let text = `<b>👁 Твой watchlist (${list.length})</b>\n\n`;

    for (const token of list) {
        text += `<b>${safeHtml(token)}</b>`;
        // Найти в momentum
        let row = null;
        if (momentum && momentum.rows) {
            for (const r of momentum.rows) {
                if (r && String(r.token || '').toUpperCase() === token) {
                    row = r;
                    break;
                }
            }
        }
        if (!row) {
            text += ` <i>— не в universe или tx_count &lt; ${LIQUIDITY_FLOOR}</i>\n\n`;
            continue;
        }
        const signal = row.signal || 'NEUTRAL';
        const emoji = { STRONG_BUY: '🟢', STRONG_SELL: '🔴', DIVERGENCE: '⚠', NEUTRAL_FLOW_UP: '⚪' }[signal] || '⚪';
        text += ` ${emoji} <code>${safeHtml(signal)}</code>\n`;
        text += `  Sector: ${safeHtml(row.sector)}\n`;
        const net = row.net_flow_m_usd || 0;
        text += `  Net flow 7d: <code>${net >= 0 ? '+' : ''}${net.toFixed(2)}M</code>\n`;
        const pc = row.price_change_7d_pct || 0;
        text += `  Price 7d: <code>${pc >= 0 ? '+' : ''}${pc.toFixed(1)}%</code>\n`;
        // Streak
        if (rotation && rotation.signal_streaks) {
            const streak = rotation.signal_streaks[token];
            if (streak) {
                text += `  Streak: <code>${streak}d</code>`;
                if (streak >= 3) text += ` ✅`;
                text += `\n`;
            }
        }
        text += `\n`;
    }

    text += `<i>💡 Details по каждому: /check &lt;TOKEN&gt;</i>\n`;
    text += `<i>💡 Убрать из watchlist: /unwatch &lt;TOKEN&gt;</i>`;
    await tgSend(env, chatId, text);
}


// ============================================================
// SCAN · точечное обновление Dune по одному токену
// ------------------------------------------------------------
// Полный скан юниверса стоит ~150 кредитов Dune. Один токен — 5-25.
// При бюджете 4000/мес это позволяет обновлять конкретный актив
// перед решением, не трогая остальные 30.
//
// Два входа:
//   · /scan LINK в Telegram — авторизован через AUTHORIZED_CHAT_ID
//   · POST /scan из модалки дашборда — защищён PIN + лимитами
// ============================================================

// Разрешённые символы. Всё, чего здесь нет, не запускает workflow —
// иначе произвольная строка из браузера могла бы уйти в Actions.
const SCAN_ALLOWED = [
    'STRK', 'LINK', 'ETHFI', 'MORPHO', 'ONDO', 'ARB', 'OP', 'AAVE',
    'PENDLE', 'LDO', 'EIGEN', 'UNI', 'CFG', 'AIXBT', 'ZK', 'ENA',
    'JTO', 'DYDX', 'GMX', 'RPL', 'SSV', 'FXS', 'CRV', 'COMP', 'SNX',
    'INJ', 'TIA', 'SEI', 'SUI', 'APT', 'BTC', 'ETH', 'SOL',
];

const SCAN_COOLDOWN_HOURS = 6;    // один и тот же токен не чаще
const SCAN_DAILY_LIMIT = 6;       // всего сканов в сутки

async function scanQuota(env, token) {
    // Возвращает { allowed, reason, usedToday }
    if (!env.WATCHLIST_KV) {
        return { allowed: true, reason: null, usedToday: 0 };  // KV нет — лимиты не считаем
    }
    const now = Date.now();
    const day = new Date().toISOString().slice(0, 10);

    let usedToday = 0;
    try {
        const raw = await env.WATCHLIST_KV.get(`scanday:${day}`);
        usedToday = raw ? parseInt(raw, 10) || 0 : 0;
    } catch (e) { /* считаем что 0 */ }

    if (usedToday >= SCAN_DAILY_LIMIT) {
        return {
            allowed: false,
            usedToday,
            reason: `Дневной лимит ${SCAN_DAILY_LIMIT} сканов исчерпан. Это защита кредитов Dune — обновится завтра.`,
        };
    }

    try {
        const last = await env.WATCHLIST_KV.get(`scanlast:${token}`);
        if (last) {
            const ageH = (now - parseInt(last, 10)) / 3600000;
            if (ageH < SCAN_COOLDOWN_HOURS) {
                const left = (SCAN_COOLDOWN_HOURS - ageH).toFixed(1);
                return {
                    allowed: false,
                    usedToday,
                    reason: `${token} сканировали ${ageH.toFixed(1)} ч назад. Следующий скан через ${left} ч — данные Dune всё равно не успевают измениться.`,
                };
            }
        }
    } catch (e) { /* пропускаем */ }

    return { allowed: true, reason: null, usedToday };
}

async function scanQuotaConsume(env, token, usedToday) {
    if (!env.WATCHLIST_KV) return;
    const day = new Date().toISOString().slice(0, 10);
    try {
        // TTL сутки с запасом — ключи сами исчезнут
        await env.WATCHLIST_KV.put(`scanday:${day}`, String(usedToday + 1), { expirationTtl: 172800 });
        await env.WATCHLIST_KV.put(`scanlast:${token}`, String(Date.now()), { expirationTtl: 172800 });
    } catch (e) {
        console.error(`Scan quota write failed: ${e}`);
    }
}

async function dispatchScan(env, token) {
    // Возвращает { ok, status, body }
    if (!env.GITHUB_PAT) {
        return { ok: false, status: 0, body: 'GITHUB_PAT не задан в Variables воркера' };
    }
    const [owner, repo] = (env.GITHUB_REPO || '').split('/');
    if (!owner || !repo) {
        return { ok: false, status: 0, body: 'GITHUB_REPO не задан' };
    }
    const url = `https://api.github.com/repos/${owner}/${repo}/actions/workflows/strk_engine.yml/dispatches`;
    try {
        const resp = await fetch(url, {
            method: 'POST',
            headers: {
                'Authorization': `Bearer ${env.GITHUB_PAT}`,
                'Accept': 'application/vnd.github+json',
                'Content-Type': 'application/json',
                'User-Agent': 'lab-bot-worker',
                'X-GitHub-Api-Version': '2022-11-28',
            },
            body: JSON.stringify({
                ref: env.GITHUB_BRANCH || 'main',
                inputs: { mode: 'token_scan_single', token },
            }),
        });
        if (resp.status === 204) return { ok: true, status: 204, body: '' };
        return { ok: false, status: resp.status, body: (await resp.text()).slice(0, 200) };
    } catch (e) {
        return { ok: false, status: 0, body: String(e).slice(0, 200) };
    }
}

async function cmdScan(env, chatId, tokenQuery) {
    const token = (tokenQuery || '').trim().toUpperCase().replace(/[^A-Z0-9]/g, '');

    if (!token) {
        await tgSend(env, chatId,
            '<b>🔬 Точечный скан Dune</b>\n\n' +
            'Использование: <code>/scan LINK</code>\n\n' +
            'Обновляет данные Dune по одному токену: фаза, потоки, сигналы силы.\n' +
            `Стоит 5-25 кредитов вместо ~150 за весь юниверс.\n\n` +
            `<i>Лимиты: ${SCAN_DAILY_LIMIT} сканов в сутки, один токен не чаще раза в ${SCAN_COOLDOWN_HOURS} ч.</i>`);
        return;
    }

    if (!SCAN_ALLOWED.includes(token)) {
        await tgSend(env, chatId,
            `⚠ <code>${safeHtml(token)}</code> нет в списке разрешённых.\n\n` +
            `Доступны: ${SCAN_ALLOWED.slice(0, 14).map(t => `<code>${t}</code>`).join(' ')} …`);
        return;
    }

    const quota = await scanQuota(env, token);
    if (!quota.allowed) {
        await tgSend(env, chatId, `⏳ <b>Скан отклонён</b>\n\n${quota.reason}`);
        return;
    }

    const res = await dispatchScan(env, token);
    if (res.ok) {
        await scanQuotaConsume(env, token, quota.usedToday);
        await tgSend(env, chatId,
            `<b>🔬 Скан ${token} запущен</b>\n\n` +
            'Dune считает 26 недель потоков · обычно 2-4 минуты.\n\n' +
            `Использовано сегодня: ${quota.usedToday + 1} из ${SCAN_DAILY_LIMIT}\n\n` +
            `<i>Когда закончится — открой актив в дашборде, данные подтянутся.</i>`);
    } else {
        await tgSend(env, chatId,
            `❌ <b>Скан не запустился</b>\n\nHTTP ${res.status}\n${safeHtml(res.body)}`);
    }
}

// ── HTTP-эндпоинт для кнопки в модалке ──────────────────────
// Дашборд — статическая страница, положить в неё GITHUB_PAT нельзя.
// Поэтому: PIN хранится в воркере, страница спрашивает его один раз.
// PIN — первый рубеж, лимиты выше — второй: даже если PIN утечёт,
// потратить можно не больше SCAN_DAILY_LIMIT сканов в сутки.
function corsHeaders(env) {
    return {
        'Access-Control-Allow-Origin': env.DASHBOARD_ORIGIN || '*',
        'Access-Control-Allow-Methods': 'POST, OPTIONS',
        'Access-Control-Allow-Headers': 'Content-Type, X-Scan-Pin',
        'Access-Control-Max-Age': '86400',
    };
}

function jsonResponse(env, obj, status = 200) {
    return new Response(JSON.stringify(obj), {
        status,
        headers: { 'Content-Type': 'application/json', ...corsHeaders(env) },
    });
}

async function handleScanHttp(request, env) {
    if (!env.SCAN_PIN) {
        return jsonResponse(env, {
            ok: false,
            error: 'SCAN_PIN не настроен в воркере — кнопка отключена',
        }, 503);
    }

    const pin = request.headers.get('X-Scan-Pin') || '';
    if (pin !== env.SCAN_PIN) {
        return jsonResponse(env, { ok: false, error: 'Неверный PIN' }, 403);
    }

    let body = {};
    try { body = await request.json(); } catch (e) { /* пусто */ }

    const token = String(body.token || '').trim().toUpperCase().replace(/[^A-Z0-9]/g, '');
    if (!token || !SCAN_ALLOWED.includes(token)) {
        return jsonResponse(env, { ok: false, error: `Токен ${token || '—'} не разрешён` }, 400);
    }

    const quota = await scanQuota(env, token);
    if (!quota.allowed) {
        return jsonResponse(env, { ok: false, error: quota.reason, quota: true }, 429);
    }

    const res = await dispatchScan(env, token);
    if (!res.ok) {
        return jsonResponse(env, { ok: false, error: `GitHub HTTP ${res.status}: ${res.body}` }, 502);
    }

    await scanQuotaConsume(env, token, quota.usedToday);
    return jsonResponse(env, {
        ok: true,
        token,
        used_today: quota.usedToday + 1,
        daily_limit: SCAN_DAILY_LIMIT,
        message: `Скан ${token} запущен. Обычно 2-4 минуты.`,
    });
}

async function processCommand(env, chatId, text) {
    const trimmed = (text || '').trim();
    if (!trimmed.startsWith('/')) return;

    const parts = trimmed.split(/\s+/);
    let cmd = parts[0].toLowerCase();
    // Strip @botname
    if (cmd.includes('@')) cmd = cmd.split('@')[0];
    const arg = parts.slice(1).join(' ');

    console.log(`CMD from ${chatId}: ${cmd} arg=${arg.slice(0, 50)}`);

    try {
        if (cmd === '/help' || cmd === '/start') {
            await cmdHelp(env, chatId);
        } else if (cmd === '/status') {
            await cmdStatus(env, chatId);
        } else if (cmd === '/check') {
            await cmdCheck(env, chatId, arg);
        } else if (cmd === '/list') {
            await cmdList(env, chatId, arg || null);
        } else if (cmd === '/week') {
            await cmdWeek(env, chatId);
        } else if (cmd === '/watch') {
            await cmdWatch(env, chatId, arg);
        } else if (cmd === '/unwatch') {
            await cmdUnwatch(env, chatId, arg);
        } else if (cmd === '/mywatch') {
            await cmdMywatch(env, chatId);
        } else if (cmd === '/explain') {
            await cmdExplain(env, chatId, arg);
        } else if (cmd === '/refresh') {
            await cmdRefresh(env, chatId);
        } else if (cmd === '/scan') {
            await cmdScan(env, chatId, arg);
        } else {
            await tgSend(env, chatId, `Unknown command: ${safeHtml(cmd)}\nSend <code>/help</code> for list.`);
        }
    } catch (e) {
        console.error(`Command error: ${e}`);
        await tgSend(env, chatId, `⚠ Internal error: ${safeHtml(String(e).slice(0, 100))}`);
    }
}

// ============================================================
// WEBHOOK HANDLER
// ============================================================
export default {
    async fetch(request, env, ctx) {
        const url = new URL(request.url);

        // CORS preflight для кнопки скана в модалке
        if (request.method === 'OPTIONS' && url.pathname === '/scan') {
            return new Response(null, { status: 204, headers: corsHeaders(env) });
        }

        // Точечный скан Dune из дашборда
        if (request.method === 'POST' && url.pathname === '/scan') {
            return await handleScanHttp(request, env);
        }

        // Health check
        if (request.method === 'GET') {
            return new Response('LAB Bot Webhook · alive', { status: 200 });
        }

        if (request.method !== 'POST') {
            return new Response('Method not allowed', { status: 405 });
        }

        let update;
        try {
            update = await request.json();
        } catch (e) {
            return new Response('Invalid JSON', { status: 400 });
        }

        const msg = update.message || update.edited_message;
        if (!msg) {
            return new Response('OK', { status: 200 });
        }

        const chatId = msg.chat && msg.chat.id;
        const text = msg.text || '';

        // Auth check
        if (env.AUTHORIZED_CHAT_ID && String(chatId) !== String(env.AUTHORIZED_CHAT_ID)) {
            console.log(`Ignoring chat ${chatId} (authorized: ${env.AUTHORIZED_CHAT_ID})`);
            return new Response('OK', { status: 200 });
        }

        if (text.startsWith('/')) {
            // Process in background — respond to Telegram immediately
            ctx.waitUntil(processCommand(env, chatId, text));
        }

        return new Response('OK', { status: 200 });
    },
};