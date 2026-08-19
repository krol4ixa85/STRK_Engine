// LAYMAN LAYER · Global tooltip system
// Every technical term gets hover explanation

const LAYMAN_GLOSSARY = {
  'Wyckoff': 'Метод анализа рынка Wyckoff — 4 фазы: накопление (крупные тихо покупают), markup (импульс вверх), распределение (крупные продают retail), markdown (падение)',
  'accumulation': 'Крупные игроки тихо покупают актив на низких ценах, готовясь к росту. Retail в это время продаёт из страха',
  'distribution': 'Крупные игроки тихо продают retail на высоких ценах. Retail покупает от FOMO, не понимая что смарт-мани выходят',
  'markup': 'Фаза быстрого роста цены после накопления. Крупные подтолкнули цену вверх, retail заходит от FOMO',
  'markdown': 'Фаза падения после распределения. Retail в убытках, смарт-мани уже на выходе',
  'Spring': 'Технический сигнал: цена falsely пробивает поддержку вниз, но быстро возвращается — сигнал конца accumulation, начала markup',
  'SOS': 'Sign of Strength — большой прирост объёма покупок после accumulation. Подтверждает начало markup',
  'confluence': 'Совпадение нескольких независимых сигналов на одном направлении. Rally 5/9 = 5 из 9 факторов дают bullish',
  'netflow': 'Разница между притоком и оттоком капитала. Positive = деньги идут в актив, negative = выходят',
  'STRONG_BUY': 'Токен показывает силу: активный приток капитала + рост цены + низкая concentration в одних руках',
  'DIVERGENCE': 'Цена растёт, но капитал не идёт следом (или наоборот). Сигнал слабости — может развернуться',
  'STRONG_SELL': 'Активный отток капитала + падение цены + high whale activity. Distribution signal',
  'BTC.D': 'Dominance BTC — доля Bitcoin в total crypto market cap. >55% = BTC season, <50% = alt season',
  'TVL': 'Total Value Locked — сколько денег заперто в DeFi протоколах. Растёт = деньги идут в DeFi',
  'funding rate': 'Плата, которую платят leveraged лонги шортам (или наоборот). Positive = много лонгов, negative = много шортов',
  'dry powder': 'Свободные stables на биржах = "порох" для покупки. High = потенциал rally, low = мало кто может покупать',
  'ATH': 'All-Time High — исторический максимум цены',
  'ATL': 'All-Time Low — исторический минимум цены',
  'LST': 'Liquid Staking Token — токен получаемый за staking (ETHFI = staked ETH через EtherFi)',
  'RWA': 'Real World Assets — токенизированные реальные активы (US Treasuries через LINK, gold через PAXG)',
  'INFRA': 'Инфраструктурные токены (LINK для oracle, GRT для indexing, AKT для compute)',
  'L2': 'Layer 2 — надстройка над Ethereum для дешёвых транзакций (STRK Starknet, ARB Arbitrum, OP Optimism)',
};

function initLaymanHints() {
  // Add global CSS for tooltips
  if (!document.getElementById('layman-hints-css')) {
    const style = document.createElement('style');
    style.id = 'layman-hints-css';
    style.textContent = `
      .layman-term {
        border-bottom: 1px dotted var(--text-muted);
        cursor: help;
        position: relative;
      }
      .layman-term:hover::after {
        content: attr(data-hint);
        position: absolute;
        bottom: calc(100% + 6px);
        left: 50%;
        transform: translateX(-50%);
        background: rgba(20, 20, 30, 0.95);
        color: white;
        padding: 8px 12px;
        border-radius: 6px;
        font-size: 12px;
        line-height: 1.4;
        min-width: 240px;
        max-width: 320px;
        white-space: normal;
        z-index: 10000;
        box-shadow: 0 4px 12px rgba(0,0,0,0.2);
        pointer-events: none;
      }
      .layman-term:hover::before {
        content: '';
        position: absolute;
        bottom: calc(100% + 1px);
        left: 50%;
        transform: translateX(-50%);
        border: 5px solid transparent;
        border-top-color: rgba(20, 20, 30, 0.95);
        z-index: 10001;
      }
    `;
    document.head.appendChild(style);
  }
  
  // Wrap terms after every render
  wrapTerms(document.body);
}

function wrapTerms(root) {
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, null, false);
  const nodesToProcess = [];
  let node;
  while (node = walker.nextNode()) {
    // Skip if already inside .layman-term or script/style tags
    if (node.parentElement.classList?.contains('layman-term')) continue;
    if (['SCRIPT', 'STYLE', 'CODE', 'PRE'].includes(node.parentElement.tagName)) continue;
    nodesToProcess.push(node);
  }
  
  for (const textNode of nodesToProcess) {
    let text = textNode.textContent;
    let hasChanges = false;
    
    // Check each glossary term
    for (const [term, hint] of Object.entries(LAYMAN_GLOSSARY)) {
      const regex = new RegExp('\\\\b' + term.replace(/[.*+?^${}()|[\\]\\\\]/g, '\\\\$&') + '\\\\b', 'gi');
      if (regex.test(text)) {
        hasChanges = true;
        text = text.replace(regex, `<span class="layman-term" data-hint="${hint.replace(/"/g, '&quot;')}">${term}</span>`);
      }
    }
    
    if (hasChanges) {
      const span = document.createElement('span');
      span.innerHTML = text;
      textNode.parentNode.replaceChild(span, textNode);
    }
  }
}

// Call after every render
document.addEventListener('DOMContentLoaded', initLaymanHints);
