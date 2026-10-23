"""Private, read-only TradeFlipper research MCP.

Run from the TradeFlipper repository root:
  python -m mcp_server.server            # stdio, preferred for local AI agents
  python -m mcp_server.server --http     # loopback-only 127.0.0.1:8765/mcp
No tools create orders, change configuration, or alter positions.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

# Works embedded in tradeFlipper/ or cloned as a standalone sibling repository.
BASE = Path(__file__).resolve().parent.parent
configured = os.environ.get('TRADEFLIPPER_ROOT')
roots = ([Path(configured).expanduser()] if configured else []) + [
    BASE, BASE.parent / 'tradeFlipper', Path.home() / 'tradeFlipper',
]
APP_ROOT = next((p for p in roots if (p / 'backend' / 'app').is_dir()), None)
if APP_ROOT is None:
    raise RuntimeError('TradeFlipper backend not found; set TRADEFLIPPER_ROOT to the tradeFlipper repository.')
sys.path.insert(0, str(APP_ROOT / 'backend'))

from mcp.server import MCPServer  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.data.provider import get_provider  # noqa: E402
from app.engine import complete_bars  # noqa: E402
from app.indicators import add_indicators  # noqa: E402
from app.models import Account, ExecutionSetting, Position, SignalRecord, Trade, WatchlistItem  # noqa: E402
from app.strategies import STRATEGIES  # noqa: E402
from .research import (completed_stock_daily_bars, number, option_candidates, stock_metrics,
                       symbols_from_text)  # noqa: E402

log = logging.getLogger(__name__)
mcp = MCPServer('TradeFlipper Research — read only')


def _prepare(raw, interval: str):
    """Finalize candles BEFORE technical indicators are calculated; never use an active 1d bar."""
    if interval == '1d':
        candles, quality = completed_stock_daily_bars(raw)
    else:
        candles, quality = complete_bars(raw, interval), {}
    # Indicators computed only on completed data: prevents today's partial volume
    # and close from affecting relative-volume and signal calculations.
    return add_indicators(candles), quality


def _bars(symbol: str, mode: str):
    interval = '5m' if mode == 'day' else '1d'
    period = '5d' if mode == 'day' else '1y'
    provider = get_provider()
    raw = provider.bars(symbol, period, interval)
    candles, quality = _prepare(raw, interval)
    return candles, interval, provider.name, quality


def _evaluate(symbol: str, df, mode: str, interval: str, source: str,
              quality: dict | None = None) -> dict:
    stats = stock_metrics(df, interval)
    signals = []
    i = len(df) - 1
    for name, strat in STRATEGIES.items():
        if mode not in strat.horizons or i < strat.min_bars:
            continue
        try:
            c = strat.evaluate(symbol, df, i)
        except Exception as exc:
            log.warning('strategy %s failed for %s: %s', name, symbol, type(exc).__name__)
            continue
        if c is None:
            continue
        signals.append({'strategy': name, 'side': c.side, 'confidence': c.confidence,
                        'entry': round(c.entry, 4), 'stop': round(c.stop, 4),
                        'target': round(c.target, 4), 'reward_risk': c.reward_risk,
                        'reasons': c.reasons[:5]})
    signals.sort(key=lambda s: -s['confidence'])
    quality = quality or {}
    if quality.get('incomplete_daily_bars_excluded'):
        stats['warnings'].append('Excluded unfinished/future daily bar(s) before computing indicators')
    return {'symbol': symbol, 'mode': mode, 'bar_interval': interval, 'data_provider': source,
            **stats, 'bar_quality': quality, 'strategy_signals': signals,
            'has_current_strategy_signal': bool(signals)}


@mcp.tool()
def analyze_stock(symbol: str, mode: str = 'swing') -> dict:
    """Read-only analysis of ONE US stock for swing (daily) or day (5-minute) setups.

    Uses completed candles and TradeFlipper's existing strategies. Scores indicate
    technical alignment, NOT probability of success. Data may be delayed.
    """
    if mode not in ('day', 'swing'):
        raise ValueError('mode must be day or swing')
    symbol = symbols_from_text(symbol, limit=1)[0]
    df, interval, source, quality = _bars(symbol, mode)
    return _evaluate(symbol, df, mode, interval, source, quality)


@mcp.tool()
def screen_stocks(mode: str = 'swing', scope: str = 'watchlist',
                  symbols: str = '', max_symbols: int = 25, top_n: int = 10,
                  universe_offset: int = 0) -> dict:
    """Rank stock candidates for DAY or SWING research; never place orders.

    scope=watchlist uses the saved TradeFlipper watchlist; scope=movers uses
    market gainers/most-active; scope=universe samples a rotating/batched slice
    of the S&P 500 universe (use universe_offset for the next batch).
    An explicit comma-separated symbols argument overrides scope (up to 40).
    These are technical rankings, not predicted returns or guaranteed winners.
    """
    if mode not in ('day', 'swing') or scope not in ('watchlist', 'movers', 'universe'):
        raise ValueError('Invalid mode or scope')
    if not 1 <= max_symbols <= 40 or not 1 <= top_n <= 20 or universe_offset < 0:
        raise ValueError('max_symbols=1..40, top_n=1..20, universe_offset>=0')
    notes = []
    if symbols.strip():
        pool = symbols_from_text(symbols)
    elif scope == 'watchlist':
        from app.db import SessionLocal
        with SessionLocal() as db:
            pool = list(db.scalars(select(WatchlistItem.symbol).where(WatchlistItem.active.is_(True))).all())
        if not pool:
            pool = get_settings().watchlist
            notes.append('No active saved watchlist; used default watchlist configuration.')
    elif scope == 'movers':
        from app.market_data import movers
        data = movers(get_provider(), count=20)
        pool = [x.get('symbol', '') for category in ('gainers', 'active', 'losers')
                for x in data.get(category, [])]
        pool = [s for s in pool if isinstance(s, str) and s.upper() == s and len(s) <= 10]
        notes.append('Mover discovery uses Yahoo screeners and may be delayed/rate-limited.')
    else:
        from app.universe import load_universe
        pool = list(load_universe())
        notes.append('A single request samples only one bounded universe batch; rotate universe_offset.')
    clean = list(dict.fromkeys(s for s in pool if isinstance(s, str) and len(s) <= 10
                               and s and s[0].isalpha() and all(c.isalnum() or c in '.-' for c in s)))
    selected = clean[universe_offset:universe_offset + max_symbols] if scope == 'universe' and not symbols.strip() \
        else clean[:max_symbols]
    if not selected:
        return {'candidates': [], 'scanned': 0, 'errors': {}, 'notes': ['No symbols in this selection.']}
    interval, period = ('5m', '5d') if mode == 'day' else ('1d', '1y')
    provider = get_provider()
    frames = provider.bars_many(selected, period, interval)
    found, errors = [], {}
    for symbol in selected:
        raw = frames.get(symbol)
        if raw is None or raw.empty:
            errors[symbol] = 'market data unavailable'
            continue
        try:
            df, quality = _prepare(raw, interval)
            found.append(_evaluate(symbol, df, mode, interval, provider.name, quality))
        except (ValueError, KeyError, TypeError) as exc:
            errors[symbol] = str(exc)[:160]
    found.sort(key=lambda x: (bool(x['strategy_signals']), x['technical_alignment_score']), reverse=True)
    return {'mode': mode, 'scope': scope, 'scanned': len(selected), 'data_provider': provider.name,
            'generated_at_utc': datetime.now(UTC).isoformat(), 'candidates': found[:top_n],
            'errors': errors, 'notes': notes + [
                'Technical rankings are NOT forecasts or win probabilities; confirm quote freshness before acting.'],
            'next_universe_offset': universe_offset + len(selected) if scope == 'universe' and not symbols.strip()
                                     and universe_offset + len(selected) < len(clean) else None}


def _underlying_reference(t) -> tuple[float | None, str]:
    """Indicative, unverified spot reference for moneyness, never a live quote."""
    try:
        spot = number(t.fast_info.get('last_price'))
        if spot is not None and spot > 0:
            return spot, 'Yahoo fast_info.last_price (quote timestamp unavailable)'
    except Exception as exc:
        log.debug('options reference quote unavailable: %s', type(exc).__name__)
    try:
        hist = t.history(period='5d', interval='1d')
        if hist is not None and not hist.empty:
            spot = number(hist['Close'].iloc[-1])
            if spot is not None and spot > 0:
                return spot, 'Yahoo latest daily close (possibly partial; unverified)'
    except Exception as exc:
        log.debug('options reference candle unavailable: %s', type(exc).__name__)
    return None, 'unavailable'


@mcp.tool()
def screen_options(symbol: str, right: str = 'call', min_dte: int = 7, max_dte: int = 45,
                   max_debit_usd: float = 500, min_volume: int = 10,
                   min_open_interest: int = 100, max_spread_pct: float = 20,
                   top_n: int = 12) -> dict:
    """Research LONG calls or puts on one stock; rank only liquid option quotes.

    Yahoo data is non-executable, potentially delayed and does not reliably include
    Greeks or an accurate quote timestamp. Reject 0DTE, missing quotes, wide spreads,
    illiquid contracts and premiums above max_debit_usd. Never execute orders.
    """
    symbol = symbols_from_text(symbol, limit=1)[0]
    if right not in ('call', 'put') or not (1 <= min_dte <= max_dte <= 120):
        raise ValueError('right must be call/put and DTE must be between 1 and 120 days')
    if not (0 <= min_volume <= 1_000_000 and 0 <= min_open_interest <= 1_000_000
            and 0 < max_spread_pct <= 100 and 1 <= max_debit_usd <= 100_000 and 1 <= top_n <= 30):
        raise ValueError('Invalid liquidity, spread, premium or count filter')
    import yfinance as yf
    from datetime import date
    from zoneinfo import ZoneInfo
    t = yf.Ticker(symbol)
    all_expiries = [exp for exp in (t.options or []) if min_dte <=
                    (date.fromisoformat(exp) - datetime.now(ZoneInfo('America/New_York')).date()).days <= max_dte]
    expiries = all_expiries[:5]  # bounded requests; use narrower DTE to investigate other expiries
    spot, spot_source = _underlying_reference(t)
    found, errors = [], {}
    checked_contracts = 0
    for exp in expiries:
        try:
            chain = t.option_chain(exp)
            frame = chain.calls if right == 'call' else chain.puts
            checked_contracts += len(frame)
            found += option_candidates(frame, symbol, exp, right, min_volume=min_volume,
                                       min_oi=min_open_interest, max_spread_pct=max_spread_pct / 100,
                                       max_debit_usd=max_debit_usd, limit=top_n, underlying_price=spot)
        except Exception as exc:
            errors[exp] = f'{type(exc).__name__}: {exc}'[:200]
    found.sort(key=lambda row: (-row['liquidity_score'], row['spread_pct'], -row['open_interest']))
    return {'symbol': symbol, 'right': right, 'contracts': found[:top_n],
            'expirations_checked': expiries, 'expirations_skipped_due_to_limit':
            max(0, len(all_expiries) - len(expiries)),
            'contracts_examined': checked_contracts,
            'contracts_passing_filters': len(found),
            'underlying_reference_price': spot, 'underlying_price_source': spot_source,
            'underlying_quote_timestamp_utc': None,
            'errors': errors, 'source': 'Yahoo Finance options chain (may be delayed/stale)',
            'generated_at_utc': datetime.now(UTC).isoformat(),
            'warnings': ['Liquidity score compares numerical spread, volume and open interest; NOT expected return.',
                         'Yahoo option bid/ask quote timestamps are unavailable: executable prices are unverified.',
                         'Locked (bid=ask), crossed, zero-bid and missing quotes are excluded.',
                         'Last-trade age is NOT quote age; verify current prices, Greeks and earnings elsewhere.',
                         'Breakeven applies at expiration and excludes transaction fees.',
                         'Long options may lose 100% of premium; standard contracts usually control 100 shares.',
                         'No order execution is available through this MCP server.']}


@mcp.tool()
def paper_portfolio(limit: int = 10) -> dict:
    """Read-only locally recorded TradeFlipper portfolio; no broker network calls.

    If the selected venue is Alpaca/Webull paper, the database may contain broker-
    backed fills rather than simulated fills. This is NOT a live broker balance.
    """
    if not 1 <= limit <= 30:
        raise ValueError('limit must be 1..30')
    from app.db import SessionLocal
    with SessionLocal() as db:
        acct = db.scalar(select(Account).limit(1))
        setting = db.get(ExecutionSetting, 1)
        venue = (setting.venue if setting else None) or 'simulator'
        held = db.scalars(select(Position).limit(limit)).all()
        recent = db.scalars(select(SignalRecord).order_by(SignalRecord.id.desc()).limit(limit)).all()
        history = db.scalars(select(Trade).order_by(Trade.id.desc()).limit(limit)).all()
        return {'source': 'TradeFlipper local SQLite records (not a live broker snapshot)',
                'selected_execution_venue': venue,
                'positions_source': 'simulator' if venue == 'simulator' else 'locally recorded broker paper fills; may be stale',
                'broker_connection_checked': False,
                'cash': number(acct.cash) if acct else None,
                'reference_starting_cash': number(acct.starting_cash) if acct else None,
                'positions': [{'symbol': p.symbol, 'side': p.side, 'qty': p.qty, 'entry': p.entry_price,
                               'last_mark': p.last_price, 'stop': p.stop, 'target': p.target,
                               'trading_mode': p.trading_mode} for p in held],
                'signals': [{'symbol': x.symbol, 'strategy': x.strategy, 'confidence': x.confidence,
                             'entry': x.entry, 'side': x.side} for x in recent],
                'closed_trades': [{'symbol': t.symbol, 'side': t.side, 'pnl': t.pnl,
                                   'exit_reason': t.exit_reason} for t in history],
                'generated_at_utc': datetime.now(UTC).isoformat()}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Private TradeFlipper research MCP (no order execution)')
    parser.add_argument('--http', action='store_true', help='Serve at 127.0.0.1:8765/mcp instead of stdio')
    args = parser.parse_args()
    if args.http:
        mcp.run(transport='streamable-http', host='127.0.0.1', port=8765)
    else:
        mcp.run()
