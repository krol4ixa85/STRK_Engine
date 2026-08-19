-- ============================================================
-- Universal Token Accumulation Scanner · v3
-- Uses spellbook dex.trades table (guaranteed to work)
-- Cost estimate: 15-25 credits per execution
-- Parameter: {{token}} (e.g. LINK, ETHFI, MORPHO)
-- 
-- Returns 26 weekly rows with DEX buy/sell volume + price
-- Python collector aggregates rolling metrics + streak
-- ============================================================

WITH params AS (
  SELECT UPPER('{{token}}') AS target_token
),

-- Weekly DEX trades aggregation
weekly_trades AS (
  SELECT
    DATE_TRUNC('week', block_time) AS week_start,
    -- Buy pressure: someone bought target token (target is token_bought)
    SUM(CASE WHEN UPPER(token_bought_symbol) = (SELECT target_token FROM params) 
             THEN amount_usd ELSE 0 END) AS buy_volume_usd,
    -- Sell pressure: someone sold target token (target is token_sold)
    SUM(CASE WHEN UPPER(token_sold_symbol) = (SELECT target_token FROM params)
             THEN amount_usd ELSE 0 END) AS sell_volume_usd,
    -- Latest price data
    AVG(CASE WHEN UPPER(token_bought_symbol) = (SELECT target_token FROM params) 
             AND amount_usd > 0 AND token_bought_amount > 0
             THEN amount_usd / token_bought_amount ELSE NULL END) AS avg_price_from_buys,
    AVG(CASE WHEN UPPER(token_sold_symbol) = (SELECT target_token FROM params)
             AND amount_usd > 0 AND token_sold_amount > 0
             THEN amount_usd / token_sold_amount ELSE NULL END) AS avg_price_from_sells,
    COUNT(*) AS tx_count
  FROM dex.trades
  WHERE block_time >= NOW() - INTERVAL '180' DAY
    AND (UPPER(token_bought_symbol) = (SELECT target_token FROM params)
      OR UPPER(token_sold_symbol) = (SELECT target_token FROM params))
    AND amount_usd IS NOT NULL
    AND amount_usd > 0
  GROUP BY 1
)

SELECT
  (SELECT target_token FROM params) AS token,
  DATE_FORMAT(week_start, '%Y-%m-%d') AS week,
  week_start,
  ROUND(buy_volume_usd / 1e6, 4) AS buy_volume_m_usd,
  ROUND(sell_volume_usd / 1e6, 4) AS sell_volume_m_usd,
  ROUND((buy_volume_usd - sell_volume_usd) / 1e6, 4) AS net_flow_m_usd,
  ROUND(COALESCE(avg_price_from_buys, avg_price_from_sells, 0), 8) AS avg_price,
  ROUND(COALESCE(avg_price_from_buys, avg_price_from_sells, 0), 8) AS close_price,
  tx_count
FROM weekly_trades
WHERE week_start IS NOT NULL
  AND week_start >= NOW() - INTERVAL '180' DAY
ORDER BY week_start ASC