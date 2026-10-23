"""Deterministic, read-only research helpers for TradeFlipper Research MCP v0.2.

Scores describe alignment and quoted *liquidity*, NOT forecasts, win probabilities,
or recommendations to enter a position. Yahoo options do not supply reliable quote
timestamps: an old last trade is NOT the same thing as an old quote.
"""
from __future__ import annotations

import math
import re
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

NY = ZoneInfo('America/New_York')
TICKER = re.compile(r'^[A-Z][A-Z0-9.\-]{0,9}$')
INTRADAY_AGE_LIMIT_MINUTES = 40
DAILY_AGE_LIMIT_DAYS = 4
RTH_CLOSE = time(16, 0)
DAILY_FINALIZED = time(16, 30)  # conservative feed grace, not exchange holiday calendar


def symbols_from_text(text: str, limit: int = 40) -> list[str]:
    """Validate caller-supplied tickers before using them in provider queries."""
    if not isinstance(text, str) or len(text) > 600:
        raise ValueError('Ticker list too long')
    items = [s.strip().upper() for s in text.replace(';', ',').split(',') if s.strip()]
    if not items or len(items) > limit or any(not TICKER.fullmatch(s) for s in items):
        raise ValueError(f'Provide 1-{limit} comma-separated US ticker symbols')
    return list(dict.fromkeys(items))


def number(value, default=None):
    """Coerce finite numeric market-data fields without leaking NaN into JSON."""
    try:
        x = float(value)
    except (TypeError, ValueError):
        return default
    return x if math.isfinite(x) else default


def utc_stamp(value) -> pd.Timestamp:
    """Require an unambiguous UTC timestamp; naive input is a UTC vendor label."""
    ts = pd.Timestamp(value)
    return ts.tz_localize('UTC') if ts.tzinfo is None else ts.tz_convert('UTC')


def us_stock_session_date(value) -> date:
    """Interpret Yahoo/provider daily bar labels as session dates, not quote times.

    Midnight UTC is commonly a DATE label, which is the following calendar day
    from the New York conversion. Other UTC stamps (including midnight local)
    are interpreted using New York's date. This applies only to 1d US equities.
    """
    ts = utc_stamp(value)
    if ts.hour == 0 and ts.minute == 0 and ts.second == 0:
        return ts.date()
    return ts.tz_convert(NY).date()


def completed_stock_daily_bars(df: pd.DataFrame, *, now: datetime | None = None,
                               grace_close: time = DAILY_FINALIZED) -> tuple[pd.DataFrame, dict]:
    """Drop an unfinished current-day daily bar *before* indicators/strategies.

    Future-dated provider rows are discarded. Does not model exchange holidays;
    the last *observed* date is used as the effective completed session.
    """
    now_local = utc_stamp(now if now is not None else datetime.now(UTC)).tz_convert(NY)
    today = now_local.date()
    sessions = [us_stock_session_date(ts) for ts in df.index]
    final = [(d < today or (d == today and now_local.time() >= grace_close)) and d <= today
             for d in sessions]
    filtered = df.iloc[[i for i, yes in enumerate(final) if yes]].copy()
    n_removed = len(df) - len(filtered)
    info = {
        'incomplete_daily_bars_excluded': n_removed,
        'daily_bar_timestamp_semantics': 'session-date label, NOT quote/last-trade timestamp',
        'last_completed_session_date': us_stock_session_date(filtered.index[-1]).isoformat()
        if len(filtered) else None,
        'daily_bar_finalization_policy_et': f'{grace_close.isoformat(timespec="minutes")} America/New_York',
    }
    return filtered, info


def stock_metrics(df: pd.DataFrame, interval: str, *, now: datetime | None = None) -> dict:
    """Technical alignment based on completed bars; scores are not entry signals."""
    if df is None or len(df) < 55:
        raise ValueError('At least 55 completed candles required')
    row = df.iloc[-1]
    close = number(row.get('close'))
    ema21, ema50 = number(row.get('ema21')), number(row.get('ema50'))
    rsi, vol, atr = number(row.get('rsi14')), number(row.get('vol_ratio')), number(row.get('atr14'))
    vwap = number(row.get('vwap')) if interval != '1d' else None
    if not close or close <= 0 or ema21 is None or ema50 is None:
        raise ValueError('Invalid or missing price/indicator data')
    now_ts = utc_stamp(now if now is not None else datetime.now(UTC))
    prev = number(df['close'].iloc[-6])
    momentum = ((close / prev - 1) * 100) if prev and prev > 0 else None
    above21, above50, trend_up = close > ema21, close > ema50, ema21 > ema50
    alignment = int(above21) + int(above50) + int(trend_up)
    if interval != '1d' and vwap is not None:
        alignment += int(close > vwap)
        n = 4
    else:
        n = 3
    bias = 'bullish' if alignment > n / 2 else 'bearish' if alignment < n / 2 else 'mixed'
    trend_score = round(100 * max(alignment, n - alignment) / n)
    activity = min(20, round(max(vol or 0.0, 0.0) * 5))
    label = utc_stamp(df.index[-1])
    flags: list[str] = []
    session = None
    age_min = None
    age_days = None
    if interval == '1d':
        session = us_stock_session_date(label)
        age_days = max(0, (now_ts.tz_convert(NY).date() - session).days)
        if age_days > DAILY_AGE_LIMIT_DAYS:
            flags.append('Most recent completed daily session is old; check feed and trading calendar')
        flags.append('Daily timestamp is a session-date label, not a quote timestamp')
    else:
        age_min = max(0, round((now_ts - label).total_seconds() / 60))
        if age_min > INTRADAY_AGE_LIMIT_MINUTES:
            flags.append('Most recent completed intraday candle is old; not a live setup')
    if vol is None:
        flags.append('Relative volume unavailable')
    if rsi is not None and (rsi >= 80 or rsi <= 20):
        flags.append('RSI extremely stretched')
    return {
        'bias': bias, 'technical_alignment_score': min(100, round(.75 * trend_score + activity)),
        'score_is_probability': False, 'close': round(close, 4),
        'rsi14': round(rsi, 2) if rsi is not None else None,
        'relative_volume': round(vol, 2) if vol is not None else None,
        'momentum_last_5_bars_pct': round(momentum, 2) if momentum is not None else None,
        'atr_pct': round(atr / close * 100, 2) if atr is not None else None,
        'above_ema21': above21, 'above_ema50': above50,
        'above_vwap': close > vwap if vwap is not None else None,
        'as_of_utc': label.isoformat(),
        'as_of_time_meaning': 'daily session-date label' if interval == '1d' else 'candle timestamp',
        'last_completed_session_date': session.isoformat() if session else None,
        'last_completed_candle_age_days': age_days,
        'data_age_minutes': age_min,  # null for 1d; never misrepresent a session label as a quote age
        'quote_timestamp_utc': None,
        'quote_age_minutes': None,
        'warnings': flags,
    }


def option_liquidity_score(volume: float, oi: float, spread: float,
                           max_spread: float) -> int:
    """Weighted relative quality, intentionally unsaturated at ordinary US option volumes.

    Quoted spread dominates. A missing *quote timestamp* is reported separately;
    this score only compares numeric liquidity on quotes of UNKNOWN freshness.
    """
    spread_quality = max(0, 1 - spread / max(max_spread, .0001))
    vol_quality = min(1.0, math.log1p(max(0, volume)) / math.log1p(3000))
    oi_quality = min(1.0, math.log1p(max(0, oi)) / math.log1p(15000))
    return round(100 * (.45 * spread_quality + .3 * vol_quality + .25 * oi_quality))


def parse_last_trade_at(raw, *, now: datetime | None = None) -> tuple[str | None, int | None]:
    if raw is None or str(raw) in ('', 'NaT', 'nan', 'None'):
        return None, None
    try:
        ts = utc_stamp(raw)
        age = (utc_stamp(now if now is not None else datetime.now(UTC)) - ts).total_seconds() / 60
        return ts.isoformat(), max(0, round(age))
    except (TypeError, ValueError, OverflowError):
        return None, None


def option_candidates(calls_or_puts: pd.DataFrame, underlying: str, expiration: str,
                      right: str, *, min_volume: int = 10, min_oi: int = 100,
                      max_spread_pct: float = .20, max_debit_usd: float = 500,
                      limit: int = 12, underlying_price: float | None = None,
                      now: datetime | None = None, max_last_trade_age_minutes: int = 60) -> list[dict]:
    """Screen long single-leg quotes (100-share standard contracts; no execution).

    Reject locked/crossed/one-sided quotes as *screen candidates*; do not assert
    the remaining quotes are live. Last-trade age != quote age. Missing Greeks stay null.
    """
    if calls_or_puts is None or calls_or_puts.empty:
        return []
    now = now or datetime.now(NY)
    days = (date.fromisoformat(expiration) - utc_stamp(now).tz_convert(NY).date()).days
    if days < 1:
        return []
    spot = number(underlying_price)
    spot = spot if spot is not None and spot > 0 else None
    results = []
    for _, row in calls_or_puts.iterrows():
        bid, ask = number(row.get('bid')), number(row.get('ask'))
        oi, volume = number(row.get('openInterest')), number(row.get('volume'))
        strike, iv = number(row.get('strike')), number(row.get('impliedVolatility'))
        # Strictly positive bid and strictly wider ask: zero/locked/indicative quote isn't liquid.
        if (bid is None or ask is None or bid <= 0 or ask <= bid
                or strike is None or strike <= 0 or oi is None or volume is None):
            continue
        mid = (bid + ask) / 2
        spread = (ask - bid) / mid
        if spread > max_spread_pct or volume < min_volume or oi < min_oi or ask * 100 > max_debit_usd:
            continue
        last_trade, trade_age = parse_last_trade_at(row.get('lastTradeDate'), now=now)
        warnings = ['Yahoo does not provide a verified bid/ask quote timestamp']
        if trade_age is None:
            warnings.append('Last trade time unavailable; this does not prove quote freshness')
        elif trade_age > max_last_trade_age_minutes:
            warnings.append(f'Last trade was {trade_age} minutes ago (not a quote timestamp)')
        if iv is None or iv <= 0:
            warnings.append('Implied volatility missing or invalid')
        greeks = {k: number(row.get(k)) for k in ('delta', 'gamma', 'theta', 'vega', 'rho')}
        if any(v is None for v in greeks.values()):
            warnings.append('Greeks incomplete/missing; no values estimated')
        intrinsic = (max(0, spot - strike) if right == 'call' else max(0, strike - spot)) if spot else None
        moneyness = (('ITM' if intrinsic and intrinsic > 0 else 'ATM'
                      if abs(spot - strike) / spot <= .005 else 'OTM') if spot else 'unknown')
        breakeven = strike + ask if right == 'call' else strike - ask
        target_move = (breakeven / spot - 1) * 100 if spot else None
        results.append({
            'contract': str(row.get('contractSymbol') or ''), 'underlying': underlying,
            'right': right, 'expiration': expiration, 'dte_calendar_days': days,
            'strike': round(strike, 2), 'bid': round(bid, 3), 'ask': round(ask, 3),
            'spread_pct': round(spread * 100, 2), 'volume': int(volume), 'open_interest': int(oi),
            'iv_pct': round(iv * 100, 2) if iv and iv > 0 else None,
            'max_debit_for_one_contract_usd': round(ask * 100, 2),
            'standard_contract_multiplier': 100,
            'expiration_breakeven_underlying_price': round(breakeven, 3),
            'expiration_breakeven_move_pct': round(target_move, 2) if target_move is not None else None,
            'moneyness': moneyness,
            'strike_distance_from_spot_pct': round((strike / spot - 1) * 100, 2) if spot else None,
            'intrinsic_per_share_usd': round(intrinsic, 3) if intrinsic is not None else None,
            'underlying_reference_price': round(spot, 4) if spot else None,
            'greeks': greeks, 'greeks_complete': all(v is not None for v in greeks.values()),
            'liquidity_score': option_liquidity_score(volume, oi, spread, max_spread_pct),
            'score_is_probability': False,
            'quote_timestamp_utc': None, 'quote_age_minutes': None,
            'quote_freshness': 'unknown', 'executable_quote_verified': False,
            'last_trade_at': last_trade, 'last_trade_age_minutes': trade_age,
            'warnings': warnings,
        })
    results.sort(key=lambda r: (-r['liquidity_score'], r['spread_pct'], -r['open_interest'], r['contract']))
    return results[:limit]
