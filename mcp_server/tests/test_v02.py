"""v0.2 regression tests: no network access, quote fabrication, or trade execution."""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from mcp_server.research import (completed_stock_daily_bars, option_candidates,
                                 option_liquidity_score, parse_last_trade_at,
                                 stock_metrics, us_stock_session_date)

NY = ZoneInfo('America/New_York')
NOW = datetime(2026, 10, 23, 13, 22, tzinfo=NY)
EXP = date(2026, 11, 20).isoformat()


def daily_rows():
    # Yahoo midnight UTC stamps are SESSION DATE labels, not after-hours observations.
    dates = pd.date_range('2026-07-01', '2026-10-23', freq='B', tz='UTC')
    return pd.DataFrame({'close': [200 + i * .2 for i in range(len(dates))],
                         'volume': [100000] * len(dates)}, index=dates)


def test_midnight_utc_daily_labels_are_session_dates_not_new_york_previous_day():
    assert us_stock_session_date(pd.Timestamp('2026-10-23T00:00:00Z')) == date(2026, 10, 23)
    assert us_stock_session_date(pd.Timestamp('2026-10-23T04:00:00Z')) == date(2026, 10, 23)


def test_partial_daily_bar_excluded_before_close_but_included_after_grace():
    df = daily_rows()
    partial, meta = completed_stock_daily_bars(df, now=NOW)
    assert meta['incomplete_daily_bars_excluded'] == 1
    assert meta['last_completed_session_date'] == '2026-10-22'
    assert len(partial) == len(df) - 1
    complete, info = completed_stock_daily_bars(df, now=NOW.replace(hour=16, minute=31))
    assert len(complete) == len(df)
    assert info['last_completed_session_date'] == '2026-10-23'


def test_partial_daily_bar_is_excluded_before_calculating_indicators():
    df = daily_rows()
    # Extreme partial day's values should have no effect on the completed history.
    df.iloc[-1, df.columns.get_loc('close')] = 1000000
    included, _ = completed_stock_daily_bars(df, now=NOW)
    assert included['close'].iloc[-1] != 1000000
    assert '2026-10-23' not in included.index.strftime('%Y-%m-%d')


def test_daily_score_timestamp_is_not_incorrectly_treated_as_17h_old_quote():
    df = daily_rows().iloc[:-1].copy()
    for field, val in (('ema21', 200), ('ema50', 190), ('rsi14', 54),
                       ('atr14', 1.5), ('vol_ratio', 1.1)):
        df[field] = val
    result = stock_metrics(df, '1d', now=NOW)
    assert result['data_age_minutes'] is None
    assert result['quote_timestamp_utc'] is None
    assert result['last_completed_session_date'] == '2026-10-22'
    assert result['last_completed_candle_age_days'] == 1
    assert not any('old' in w for w in result['warnings'])


def quote_rows():
    return pd.DataFrame([
        {'contractSymbol': 'AAPL_VALID_LIQ', 'strike': 375, 'bid': 1.73, 'ask': 1.74,
         'volume': 610, 'openInterest': 9495, 'impliedVolatility': .256,
         'lastTradeDate': '2026-10-23T16:37:00Z'},
        {'contractSymbol': 'AAPL_VALID_LOW', 'strike': 390, 'bid': .40, 'ask': .45,
         'volume': 12, 'openInterest': 110, 'impliedVolatility': .25,
         'lastTradeDate': '2026-10-23T13:00:00Z'},
        {'contractSymbol': 'AAPL_LOCKED', 'strike': 400, 'bid': .25, 'ask': .25,
         'volume': 810, 'openInterest': 39512, 'impliedVolatility': .261},
        {'contractSymbol': 'AAPL_ZERO_BID', 'strike': 410, 'bid': 0, 'ask': .04,
         'volume': 1400, 'openInterest': 5000, 'impliedVolatility': .3},
        {'contractSymbol': 'AAPL_CROSSED', 'strike': 350, 'bid': 1.2, 'ask': 1.1,
         'volume': 1500, 'openInterest': 5000, 'impliedVolatility': .3},
    ])


def test_discriminating_liquidity_and_unverified_quotes():
    out = option_candidates(quote_rows(), 'AAPL', EXP, 'call', underlying_price=346, now=NOW)
    assert [x['contract'] for x in out] == ['AAPL_VALID_LIQ', 'AAPL_VALID_LOW']
    assert out[0]['liquidity_score'] != out[1]['liquidity_score']
    assert 0 < out[0]['liquidity_score'] < 100
    assert out[0]['quote_timestamp_utc'] is None
    assert out[0]['quote_age_minutes'] is None
    assert out[0]['quote_freshness'] == 'unknown'
    assert out[0]['executable_quote_verified'] is False
    assert out[0]['greeks_complete'] is False
    assert out[0]['greeks']['delta'] is None
    assert out[0]['last_trade_age_minutes'] == 45
    assert any('Last trade' in w for w in out[1]['warnings'])


def test_moneyness_and_break_even_call_and_put():
    c = option_candidates(quote_rows().iloc[:1], 'AAPL', EXP, 'call',
                          underlying_price=346, now=NOW)[0]
    assert c['moneyness'] == 'OTM'
    assert c['expiration_breakeven_underlying_price'] == 376.74
    assert c['max_debit_for_one_contract_usd'] == 174
    assert c['strike_distance_from_spot_pct'] == pytest.approx(8.38, abs=.02)
    p = option_candidates(quote_rows().iloc[:1], 'AAPL', EXP, 'put',
                          underlying_price=400, now=NOW)[0]
    assert p['moneyness'] == 'OTM'
    assert p['expiration_breakeven_underlying_price'] == 373.26


def test_unknown_spot_still_returns_breakeven_but_not_moneyness_or_move():
    o = option_candidates(quote_rows().iloc[:1], 'AAPL', EXP, 'call',
                          underlying_price=None, now=NOW)[0]
    assert o['moneyness'] == 'unknown'
    assert o['expiration_breakeven_move_pct'] is None
    assert o['intrinsic_per_share_usd'] is None
    assert o['expiration_breakeven_underlying_price'] == 376.74


def test_last_trade_age_is_not_quote_age_and_missing_or_invalid_are_unknown():
    assert parse_last_trade_at(None, now=NOW) == (None, None)
    assert parse_last_trade_at('bad timestamp', now=NOW) == (None, None)
    _, age = parse_last_trade_at('2026-10-23T15:22:00Z', now=NOW)
    assert age == 120


def test_zero_spread_does_not_get_perfect_score():
    assert option_liquidity_score(1000, 10000, .05, .2) < 100
    assert not option_candidates(quote_rows().iloc[2:3], 'AAPL', EXP, 'call',
                                 underlying_price=346, now=NOW)
