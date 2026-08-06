#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
backtest_interpretation.py — Бэктест interpretation layer на 10 исторических setup'ах

Каждый setup — mock context, соответствующий реальной ситуации STRK
в определённый момент. Пропускаем через interpretation_layer.synthesize_from_ctx()
и проверяем что модель выявила правильный паттерн + narrative совпадает
с тем что произошло дальше.

10 setup'ов покрывают разные фазы:
  1. Feb 2024 TGE peak            → PHASE_E_TOP
  2. Mar 2024 post-TGE crash      → CAPITULATION
  3. Apr 2024 first accumulation  → ACCUMULATION_BASE
  4. May 2024 Rally 1             → GENUINE_BREAKOUT (missed by model)
  5. Jun 2024 unlock approach     → PRE_UNLOCK_WEAKNESS
  6. Jul 2024 sector rotation     → SECTOR_ROTATION_OUT
  7. Sep 2024 Rally 2 (bull trap) → BULL_TRAP
  8. Oct 2024 range               → RANGE_BOUND
  9. Nov 2024 short squeeze       → SHORT_SQUEEZE_FUEL
  10. Aug 2026 (сегодня)          → SECTOR_ROTATION_OUT + PRE_UNLOCK_WEAKNESS
"""

import sys
import json
from pathlib import Path

# Import interpretation layer patterns directly
SCRIPT_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPT_DIR / 'detectors'))

from interpretation_layer import ALL_PATTERNS

def synthesize_from_ctx(ctx):
    """Run all patterns against a given context (bypass file loading)."""
    matches = []
    for pattern_fn in ALL_PATTERNS:
        try:
            result = pattern_fn(ctx)
            if result:
                matches.append(result)
        except Exception as e:
            print(f"  Pattern {pattern_fn.__name__} error: {e}")
    
    matches.sort(key=lambda x: -x['confidence'])
    return matches


# ============================================================
# 10 ИСТОРИЧЕСКИХ SETUP'ОВ STRK
# ============================================================

SETUPS = [
    {
        'label': '1. Feb 2024 · TGE peak ($4.50 → $2.00)',
        'expected_pattern': 'PHASE_E_TOP or BULL_TRAP',
        'actual_outcome': 'Крах -55% в 4 недели',
        'ctx': {
            'phase': 'MARKUP', 'sub_phase': 'terminal', 'wyckoff_conf': 'HIGH',
            'price': 4.50, 'high_14d': 4.85, 'low_14d': 3.20,
            'slope_3d': 2.5, 'slope_accel': -8, 'vol_ratio_3d': 0.8,
            'rsi': 78, 'pct_from_high': -7, 'pct_from_low': 40,
            'funding_apr': 45, 'funding_min_7d': 20, 'short_crowded': False, 'long_crowded': True,
            'hhi': 0.15, 'entropy': 3.8, 'large_receivers_14d': 120, 'top5_share': 65,
            'cex_signal': 'STRONG_DISTRIBUTION', 'cex_net_7d': -80_000_000, 'cex_consecutive': 5,
            'effort_signal': 'ABSORPTION_DISTRIBUTION', 'cvd_signal': 'BEARISH_DIVERGENCE',
            'event_signal': 'HIGH_VOL_WINDOW', 'event_bull': 3, 'event_bear': 5,
            'sector_signal': 'STRK_OUTPERFORMING', 'strk_alpha_7d': 15,
            'days_to_unlock': 999, 'unlock_amount': 0, 'supply_30d': 0,
            'btc_price': 51000, 'btc_cycle': 'UP', 'btc_dist200': 8,
        }
    },
    {
        'label': '2. Mar 2024 · Post-TGE capitulation ($1.60)',
        'expected_pattern': 'CAPITULATION',
        'actual_outcome': 'Bounce +45% в 3 недели',
        'ctx': {
            'phase': 'MARKDOWN', 'sub_phase': 'exhaustion', 'wyckoff_conf': 'HIGH',
            'price': 1.60, 'high_14d': 2.40, 'low_14d': 1.55,
            'slope_3d': -18, 'slope_accel': 4, 'vol_ratio_3d': 2.8,
            'rsi': 22, 'pct_from_high': -33, 'pct_from_low': 3,
            'funding_apr': -35, 'funding_min_7d': -45, 'short_crowded': True, 'long_crowded': False,
            'hhi': 0.09, 'entropy': 4.5, 'large_receivers_14d': 45, 'top5_share': 42,
            'cex_signal': 'NEUTRAL', 'cex_net_7d': 5_000_000, 'cex_consecutive': 0,
            'effort_signal': 'CAPITULATION', 'cvd_signal': 'BULLISH_DIVERGENCE',
            'event_signal': 'CALM', 'event_bull': 2, 'event_bear': 3,
            'sector_signal': 'SECTOR_WEAKNESS', 'strk_alpha_7d': -2,
            'days_to_unlock': 60, 'unlock_amount': 64_000_000, 'supply_30d': 0,
            'btc_price': 62000, 'btc_cycle': 'UP', 'btc_dist200': 12,
        }
    },
    {
        'label': '3. Apr 2024 · First accumulation ($1.30-1.55)',
        'expected_pattern': 'ACCUMULATION_BASE',
        'actual_outcome': 'Ranged 6 недель, potом Rally +80%',
        'ctx': {
            'phase': 'ACCUMULATION', 'sub_phase': 'Phase B · building base', 'wyckoff_conf': 'HIGH',
            'price': 1.42, 'high_14d': 1.58, 'low_14d': 1.28,
            'slope_3d': 0.5, 'slope_accel': 1, 'vol_ratio_3d': 0.6,
            'rsi': 48, 'pct_from_high': -10, 'pct_from_low': 11,
            'funding_apr': 2, 'funding_min_7d': -3, 'short_crowded': False, 'long_crowded': False,
            'hhi': 0.055, 'entropy': 4.9, 'large_receivers_14d': 25, 'top5_share': 38,
            'cex_signal': 'NEUTRAL', 'cex_net_7d': -2_000_000, 'cex_consecutive': 0,
            'effort_signal': 'QUIET_ACCUMULATION', 'cvd_signal': 'NEUTRAL',
            'event_signal': 'CALM', 'event_bull': 2, 'event_bear': 2,
            'sector_signal': 'IN_LINE', 'strk_alpha_7d': 0.5,
            'days_to_unlock': 40, 'unlock_amount': 64_000_000, 'supply_30d': 0,
            'btc_price': 65000, 'btc_cycle': 'UP', 'btc_dist200': 14,
        }
    },
    {
        'label': '4. May 2024 · Rally 1 start ($1.55 → $2.80, +80%)',
        'expected_pattern': 'STEALTH_ACCUMULATION or GENUINE_BREAKOUT',
        'actual_outcome': 'Real rally +80% в 5 недель',
        'ctx': {
            'phase': 'MARKUP', 'sub_phase': 'Phase D · sign of strength', 'wyckoff_conf': 'HIGH',
            'price': 1.72, 'high_14d': 1.70, 'low_14d': 1.42,
            'slope_3d': 4, 'slope_accel': 5, 'vol_ratio_3d': 1.8,
            'rsi': 62, 'pct_from_high': 1, 'pct_from_low': 21,
            'funding_apr': 8, 'funding_min_7d': -2, 'short_crowded': False, 'long_crowded': False,
            'hhi': 0.06, 'entropy': 4.85, 'large_receivers_14d': 32, 'top5_share': 40,
            'cex_signal': 'MILD_ACCUMULATION', 'cex_net_7d': 8_000_000, 'cex_consecutive': 0,
            'effort_signal': 'STRONG_MOVE', 'cvd_signal': 'BULLISH_CONFIRMATION',
            'event_signal': 'POSITIVE_CATALYST', 'event_bull': 5, 'event_bear': 1,
            'sector_signal': 'STRK_OUTPERFORMING', 'strk_alpha_7d': 8,
            'days_to_unlock': 20, 'unlock_amount': 64_000_000, 'supply_30d': 0,
            'btc_price': 68000, 'btc_cycle': 'UP', 'btc_dist200': 16,
        }
    },
    {
        'label': '5. Jun 2024 · Pre-unlock ($1.85, 5 days to unlock)',
        'expected_pattern': 'PRE_UNLOCK_WEAKNESS',
        'actual_outcome': 'Dump -12% за 4 дня до unlock',
        'ctx': {
            'phase': 'DISTRIBUTION', 'sub_phase': 'Phase B', 'wyckoff_conf': 'MEDIUM',
            'price': 1.85, 'high_14d': 2.15, 'low_14d': 1.75,
            'slope_3d': -2, 'slope_accel': -3, 'vol_ratio_3d': 1.2,
            'rsi': 42, 'pct_from_high': -14, 'pct_from_low': 6,
            'funding_apr': -3, 'funding_min_7d': -8, 'short_crowded': False, 'long_crowded': False,
            'hhi': 0.058, 'entropy': 4.92, 'large_receivers_14d': 42, 'top5_share': 41,
            'cex_signal': 'MILD_DISTRIBUTION', 'cex_net_7d': -22_000_000, 'cex_consecutive': 3,
            'effort_signal': 'MIXED', 'cvd_signal': 'NEUTRAL',
            'event_signal': 'NEGATIVE_CATALYST', 'event_bull': 2, 'event_bear': 6,
            'sector_signal': 'IN_LINE', 'strk_alpha_7d': -1,
            'days_to_unlock': 5, 'unlock_amount': 128_000_000, 'supply_30d': 128_000_000,
            'btc_price': 61000, 'btc_cycle': 'UP', 'btc_dist200': 5,
        }
    },
    {
        'label': '6. Jul 2024 · Sector rotation OUT (-8% alpha)',
        'expected_pattern': 'SECTOR_ROTATION_OUT',
        'actual_outcome': 'Underperform ещё 3 недели',
        'ctx': {
            'phase': 'MARKDOWN', 'sub_phase': '', 'wyckoff_conf': 'MEDIUM',
            'price': 1.15, 'high_14d': 1.35, 'low_14d': 1.10,
            'slope_3d': -5, 'slope_accel': -2, 'vol_ratio_3d': 1.1,
            'rsi': 38, 'pct_from_high': -15, 'pct_from_low': 5,
            'funding_apr': -8, 'funding_min_7d': -15, 'short_crowded': False, 'long_crowded': False,
            'hhi': 0.055, 'entropy': 4.9, 'large_receivers_14d': 55, 'top5_share': 40,
            'cex_signal': 'MILD_DISTRIBUTION', 'cex_net_7d': -15_000_000, 'cex_consecutive': 4,
            'effort_signal': 'MIXED', 'cvd_signal': 'BEARISH_LEAN',
            'event_signal': 'SLIGHT_BEARISH', 'event_bull': 2, 'event_bear': 4,
            'sector_signal': 'STRK_UNDERPERFORMING', 'strk_alpha_7d': -8,
            'days_to_unlock': 25, 'unlock_amount': 64_000_000, 'supply_30d': 0,
            'btc_price': 66000, 'btc_cycle': 'UP', 'btc_dist200': 12,
        }
    },
    {
        'label': '7. Sep 2024 · Rally 2 top ($2.15, bull trap)',
        'expected_pattern': 'BULL_TRAP or PHASE_E_TOP',
        'actual_outcome': 'False rally, -25% в 2 недели',
        'ctx': {
            'phase': 'MARKUP', 'sub_phase': 'Phase C · terminal', 'wyckoff_conf': 'MEDIUM',
            'price': 2.15, 'high_14d': 2.20, 'low_14d': 1.60,
            'slope_3d': 6, 'slope_accel': -1, 'vol_ratio_3d': 1.4,
            'rsi': 72, 'pct_from_high': -2, 'pct_from_low': 34,
            'funding_apr': 22, 'funding_min_7d': 8, 'short_crowded': False, 'long_crowded': True,
            'hhi': 0.062, 'entropy': 4.8, 'large_receivers_14d': 78, 'top5_share': 44,
            'cex_signal': 'MILD_DISTRIBUTION', 'cex_net_7d': -25_000_000, 'cex_consecutive': 4,
            'effort_signal': 'ABSORPTION_DISTRIBUTION', 'cvd_signal': 'STEALTH_DISTRIBUTION',
            'event_signal': 'SLIGHT_BULLISH', 'event_bull': 3, 'event_bear': 2,
            'sector_signal': 'STRK_OUTPERFORMING', 'strk_alpha_7d': 5,
            'days_to_unlock': 12, 'unlock_amount': 64_000_000, 'supply_30d': 128_000_000,
            'btc_price': 60000, 'btc_cycle': 'DOWN', 'btc_dist200': -3,
        }
    },
    {
        'label': '8. Oct 2024 · Range-bound ($1.30-1.50)',
        'expected_pattern': 'RANGE_BOUND',
        'actual_outcome': 'Ranged 4 недели, breakdown после',
        'ctx': {
            'phase': 'UNKNOWN', 'sub_phase': '', 'wyckoff_conf': 'LOW',
            'price': 1.40, 'high_14d': 1.48, 'low_14d': 1.32,
            'slope_3d': 0.2, 'slope_accel': 0, 'vol_ratio_3d': 0.7,
            'rsi': 52, 'pct_from_high': -5, 'pct_from_low': 6,
            'funding_apr': 1, 'funding_min_7d': -3, 'short_crowded': False, 'long_crowded': False,
            'hhi': 0.058, 'entropy': 4.9, 'large_receivers_14d': 30, 'top5_share': 40,
            'cex_signal': 'NEUTRAL', 'cex_net_7d': 1_000_000, 'cex_consecutive': 0,
            'effort_signal': 'QUIET', 'cvd_signal': 'NEUTRAL',
            'event_signal': 'CALM', 'event_bull': 2, 'event_bear': 2,
            'sector_signal': 'IN_LINE', 'strk_alpha_7d': 0.2,
            'days_to_unlock': 45, 'unlock_amount': 64_000_000, 'supply_30d': 0,
            'btc_price': 63000, 'btc_cycle': 'DOWN', 'btc_dist200': -2,
        }
    },
    {
        'label': '9. Nov 2024 · Deep drop + extreme shorts ($0.95)',
        'expected_pattern': 'SHORT_SQUEEZE_FUEL or CAPITULATION',
        'actual_outcome': 'Squeeze +40% за 8 дней',
        'ctx': {
            'phase': 'MARKDOWN', 'sub_phase': 'exhaustion', 'wyckoff_conf': 'MEDIUM',
            'price': 0.95, 'high_14d': 1.35, 'low_14d': 0.92,
            'slope_3d': -8, 'slope_accel': 5, 'vol_ratio_3d': 2.1,
            'rsi': 28, 'pct_from_high': -30, 'pct_from_low': 3.3,
            'funding_apr': -25, 'funding_min_7d': -35, 'short_crowded': True, 'long_crowded': False,
            'hhi': 0.052, 'entropy': 4.95, 'large_receivers_14d': 38, 'top5_share': 39,
            'cex_signal': 'NEUTRAL', 'cex_net_7d': -3_000_000, 'cex_consecutive': 1,
            'effort_signal': 'CAPITULATION', 'cvd_signal': 'BULLISH_DIVERGENCE',
            'event_signal': 'CALM', 'event_bull': 3, 'event_bear': 2,
            'sector_signal': 'IN_LINE', 'strk_alpha_7d': -3,
            'days_to_unlock': 55, 'unlock_amount': 64_000_000, 'supply_30d': 0,
            'btc_price': 68000, 'btc_cycle': 'UP', 'btc_dist200': 15,
        }
    },
    {
        'label': '10. Aug 2026 · СЕГОДНЯ ($0.0258)',
        'expected_pattern': 'SECTOR_ROTATION_OUT + PRE_UNLOCK_WEAKNESS',
        'actual_outcome': 'PENDING (это форвардтест)',
        'ctx': {
            'phase': 'ACCUMULATION', 'sub_phase': 'Phase B · building base', 'wyckoff_conf': 'HIGH',
            'price': 0.0258, 'high_14d': 0.0317, 'low_14d': 0.0239,
            'slope_3d': 5.3, 'slope_accel': 14.7, 'vol_ratio_3d': 1.7,
            'rsi': 59, 'pct_from_high': -18.6, 'pct_from_low': 8.0,
            'funding_apr': -9.7, 'funding_min_7d': -14.6, 'short_crowded': True, 'long_crowded': False,
            'hhi': 0.051, 'entropy': 5.00, 'large_receivers_14d': 77, 'top5_share': 41,
            'cex_signal': 'STRONG_DISTRIBUTION', 'cex_net_7d': -38_400_000, 'cex_consecutive': 5,
            'effort_signal': 'MIXED', 'cvd_signal': 'BEARISH_LEAN',
            'event_signal': 'SLIGHT_BEARISH', 'event_bull': 3, 'event_bear': 4,
            'sector_signal': 'STRK_UNDERPERFORMING', 'strk_alpha_7d': -6.6,
            'days_to_unlock': 9, 'unlock_amount': 64_000_000, 'supply_30d': 64_000_000,
            'btc_price': 64218, 'btc_cycle': 'DOWN', 'btc_dist200': -9.2,
        }
    },
]


def evaluate_setup(setup):
    """Run interpretation on setup and compare to expected."""
    matches = synthesize_from_ctx(setup['ctx'])
    
    primary = matches[0] if matches else None
    secondary = matches[1] if len(matches) > 1 else None
    tertiary = matches[2] if len(matches) > 2 else None
    
    return {
        'label': setup['label'],
        'expected': setup['expected_pattern'],
        'outcome': setup['actual_outcome'],
        'total_matches': len(matches),
        'primary': primary,
        'secondary': secondary,
        'tertiary': tertiary,
        'all_matched_names': [m['name'] for m in matches],
    }


def main():
    print("=" * 70)
    print("BACKTEST: interpretation_layer на 10 исторических setup'ах")
    print("=" * 70)
    
    correct = 0
    partial = 0
    missed = 0
    results = []
    
    for setup in SETUPS:
        result = evaluate_setup(setup)
        results.append(result)
        
        print(f"\n{'─'*70}")
        print(f"{result['label']}")
        print(f"Expected: {result['expected']}")
        print(f"Actual outcome: {result['outcome']}")
        
        if not result['primary']:
            print(f"❌ MISSED: no patterns matched")
            missed += 1
            continue
        
        p = result['primary']
        print(f"\n  📊 Model output:")
        print(f"    Primary:   {p['name']} · {p['confidence']}% · {p['direction']}")
        if result['secondary']:
            print(f"    Secondary: {result['secondary']['name']} · {result['secondary']['confidence']}%")
        if result['tertiary']:
            print(f"    Tertiary:  {result['tertiary']['name']} · {result['tertiary']['confidence']}%")
        
        # Check if expected pattern is in top 3
        expected_names = [e.strip() for e in result['expected'].replace(' or ', '+').split('+')]
        top3_names = result['all_matched_names'][:3]
        
        hits = [e for e in expected_names if e in top3_names]
        
        if hits and hits[0] == top3_names[0]:
            # Expected was PRIMARY match
            print(f"  ✅ CORRECT (expected primary): {hits}")
            correct += 1
        elif hits:
            # Expected in top 3 but not primary
            print(f"  ⚠️  PARTIAL (expected in top-3): {hits}, primary was {top3_names[0]}")
            partial += 1
        else:
            print(f"  ❌ MISSED: expected {expected_names}, got {top3_names}")
            missed += 1
        
        # Print narrative for primary
        print(f"\n  📝 Narrative:")
        for line in [p['hypothesis'], p['position_hint']]:
            # Wrap long lines
            words = line.split()
            current = "     "
            for w in words:
                if len(current) + len(w) > 70:
                    print(current)
                    current = "     " + w + " "
                else:
                    current += w + " "
            if current.strip():
                print(current)
    
    print(f"\n{'='*70}")
    print(f"РЕЗУЛЬТАТЫ БЭКТЕСТА")
    print(f"{'='*70}")
    print(f"Всего setup'ов: {len(SETUPS)}")
    print(f"  ✅ Correct (primary right):  {correct}/{len(SETUPS)} = {correct/len(SETUPS)*100:.0f}%")
    print(f"  ⚠️  Partial (top-3 hit):    {partial}/{len(SETUPS)} = {partial/len(SETUPS)*100:.0f}%")
    print(f"  ❌ Missed:                  {missed}/{len(SETUPS)} = {missed/len(SETUPS)*100:.0f}%")
    print(f"\nAccuracy (correct + partial): {(correct+partial)/len(SETUPS)*100:.0f}%")
    
    # Save
    output = {
        'setups_count': len(SETUPS),
        'correct': correct,
        'partial': partial,
        'missed': missed,
        'accuracy_pct': (correct+partial)/len(SETUPS)*100,
        'results': [
            {
                'label': r['label'],
                'expected': r['expected'],
                'outcome': r['outcome'],
                'primary': {'name': r['primary']['name'], 'confidence': r['primary']['confidence']} if r['primary'] else None,
                'all_matched': r['all_matched_names'],
            }
            for r in results
        ]
    }
    
    output_file = Path(__file__).parent.parent / 'data' / 'cache' / 'backtest_interpretation.json'
    output_file.parent.mkdir(parents=True, exist_ok=True)
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    
    print(f"\nSaved: {output_file}")


if __name__ == '__main__':
    main()
