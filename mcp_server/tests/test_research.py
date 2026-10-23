"""Offline regression tests for read-only screening helpers (no market/API access)."""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from mcp_server.research import number, option_candidates, stock_metrics, symbols_from_text


def test_symbols_validation():
    assert symbols_from_text('spy, AAPL, spy') == ['SPY', 'AAPL']
    with pytest.raises(ValueError):
        symbols_from_text('SPY, AAPL&echo')
    with pytest.raises(ValueError):
        symbols_from_text('SPY,QQQ', limit=1)


def test_score_is_not_marked_win_probability():
    index = pd.date_range(end=pd.Timestamp.now(tz='UTC') - pd.Timedelta(minutes=20),
                          periods=80, freq='5min')
    df = pd.DataFrame({'close': [99 + n * 0.1 for n in range(80)],
                       'ema21': [99.0] * 80, 'ema50': [98.0] * 80,
                       'rsi14': [60.0] * 80, 'atr14': [0.8] * 80,
                       'vol_ratio': [1.8] * 80, 'vwap': [98.5] * 80}, index=index)
    out = stock_metrics(df, '5m')
    assert out['bias'] == 'bullish'
    assert out['score_is_probability'] is False
    assert 0 <= out['technical_alignment_score'] <= 100


def test_stale_data_is_flagged():
    idx = pd.date_range(end='2025-01-01', periods=60, freq='B', tz='UTC')
    df = pd.DataFrame({'close': [50.0] * 60, 'ema21': [49.0] * 60,
                       'ema50': [48.0] * 60}, index=idx)
    assert any('old' in w for w in stock_metrics(df, '1d')['warnings'])


def test_options_exclude_wide_spreads_and_expensive_or_illiquid_contracts():
    exp = (datetime.now(ZoneInfo('America/New_York')).date() + timedelta(days=21)).isoformat()
    df = pd.DataFrame([
        {'contractSymbol': 'AAPL1', 'strike': 200, 'bid': 1.9, 'ask': 2.0,
         'volume': 350, 'openInterest': 2000, 'impliedVolatility': 0.35},
        {'contractSymbol': 'AAPL2', 'strike': 205, 'bid': 0.1, 'ask': 1.0,
         'volume': 5000, 'openInterest': 5000, 'impliedVolatility': 0.3},
        {'contractSymbol': 'AAPL3', 'strike': 210, 'bid': 7, 'ask': 8,
         'volume': 200, 'openInterest': 1500, 'impliedVolatility': 0.5},
        {'contractSymbol': 'AAPL4', 'strike': 215, 'bid': 1, 'ask': 1.01,
         'volume': 0, 'openInterest': 1, 'impliedVolatility': 0.6},
    ])
    result = option_candidates(df, 'AAPL', exp, 'call')
    assert [row['contract'] for row in result] == ['AAPL1']
    assert result[0]['max_debit_for_one_contract_usd'] == 200.0
    assert result[0]['score_is_probability'] is False


def test_nonfinite_data_rejected():
    assert number(float('nan')) is None
    assert number(float('inf')) is None
