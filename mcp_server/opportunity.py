"""Read-only opportunity discovery pipeline (v0.3). Pure logic: no network, no TradeFlipper imports.

Three things are reported SEPARATELY and are never blended into one number:
  * ``stock_technical``  - technical alignment score + indicators (not a probability)
  * ``rule_triggers``    - confirmed TradeFlipper strategy signals on completed candles
  * ``options.contracts``- displayed option liquidity (spread/volume/open interest)
Nothing here places, modifies or cancels orders or touches broker settings.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, field, fields
from datetime import UTC, date, datetime, timedelta
from typing import Any, Callable

import pandas as pd

from .research import NY, TICKER, utc_stamp

SCHEMA_VERSION = '1.0'
MODES = ('swing', 'day')
SCOPES = ('symbols', 'watchlist', 'movers', 'universe')
MAX_BATCH = 40
FORBIDDEN_KEYS = frozenset({'api_key', 'secret', 'token', 'password', 'account_id', 'order', 'orders',
                            'place_order', 'combined_score', 'composite_score'})
DISCLAIMERS = (
    'Research only: no orders are placed, modified or cancelled by this pipeline.',
    'stock_technical.technical_alignment_score is NOT a probability, forecast or recommendation.',
    'rule_triggers are deterministic rule matches on completed candles; they are not trade instructions.',
    'options.contracts[].liquidity_score measures displayed spread/volume/open interest only.',
    'Option bid/ask quote timestamps and Greeks are unavailable from Yahoo; quote freshness is unknown.',
)


def _bounded(name: str, value, lo, hi):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not lo <= value <= hi:
        raise ValueError(f'{name} must be between {lo} and {hi}')
    return value


@dataclass(frozen=True)
class StalePolicy:
    max_daily_age_days: int = 4          # calendar days since last completed session label
    max_intraday_age_minutes: int = 40   # age of last completed 5m candle
    ttl_minutes_swing: int = 360         # how long a swing snapshot may be shown as fresh
    ttl_minutes_day: int = 15

    def validate(self) -> StalePolicy:
        _bounded('max_daily_age_days', self.max_daily_age_days, 1, 10)
        _bounded('max_intraday_age_minutes', self.max_intraday_age_minutes, 5, 240)
        _bounded('ttl_minutes_swing', self.ttl_minutes_swing, 5, 2880)
        _bounded('ttl_minutes_day', self.ttl_minutes_day, 1, 240)
        return self


@dataclass(frozen=True)
class OptionsPolicy:
    enabled: bool = True
    require_trigger: bool = False        # True: only underlyings with a confirmed rule trigger
    min_technical_score: int = 70        # used when require_trigger is False
    max_underlyings: int = 3
    contracts_per_underlying: int = 5
    min_dte: int = 7
    max_dte: int = 45
    max_debit_usd: float = 500
    min_volume: int = 10
    min_open_interest: int = 100
    max_spread_pct: float = 20
    max_last_trade_age_minutes: int = 60  # stale-trade rejection; 0 disables

    def validate(self) -> OptionsPolicy:
        _bounded('min_technical_score', self.min_technical_score, 0, 100)
        _bounded('max_underlyings', self.max_underlyings, 0, 10)
        _bounded('contracts_per_underlying', self.contracts_per_underlying, 1, 10)
        _bounded('min_dte', self.min_dte, 1, 120)
        _bounded('max_dte', self.max_dte, 1, 120)
        if self.min_dte > self.max_dte:
            raise ValueError('min_dte must not exceed max_dte')
        _bounded('max_debit_usd', self.max_debit_usd, 1, 100_000)
        _bounded('min_volume', self.min_volume, 0, 1_000_000)
        _bounded('min_open_interest', self.min_open_interest, 0, 1_000_000)
        _bounded('max_spread_pct', self.max_spread_pct, 0.01, 100)
        _bounded('max_last_trade_age_minutes', self.max_last_trade_age_minutes, 0, 10_080)
        return self


@dataclass(frozen=True)
class PipelineConfig:
    modes: tuple[str, ...] = ('swing',)
    scope: str = 'symbols'
    symbols: tuple[str, ...] = ()
    batch_size: int = MAX_BATCH          # symbols per scan call (universe slice size)
    top_n: int = 10
    min_technical_score: int = 0         # candidates below this are dropped (not a trade filter)
    stale: StalePolicy = field(default_factory=StalePolicy)
    options: OptionsPolicy = field(default_factory=OptionsPolicy)
    retain_snapshots: int = 200

    @classmethod
    def from_dict(cls, raw: dict | None) -> PipelineConfig:
        raw = dict(raw or {})
        known = {f.name for f in fields(cls)}
        unknown = set(raw) - known
        if unknown:
            raise ValueError(f'Unknown config keys: {sorted(unknown)}')
        for key, klass in (('stale', StalePolicy), ('options', OptionsPolicy)):
            sub = dict(raw.get(key) or {})
            bad = set(sub) - {f.name for f in fields(klass)}
            if bad:
                raise ValueError(f'Unknown {key} config keys: {sorted(bad)}')
            raw[key] = klass(**sub)
        raw['modes'] = tuple(raw.get('modes') or ('swing',))
        symbols = raw.get('symbols') or ()
        cleaned = tuple(dict.fromkeys(str(s).strip().upper() for s in symbols))
        if len(cleaned) > 500 or any(not TICKER.fullmatch(s) for s in cleaned):
            raise ValueError('symbols must be up to 500 valid US ticker symbols')
        raw['symbols'] = cleaned
        cfg = cls(**raw)
        return cfg.validate()

    def validate(self) -> PipelineConfig:
        if not self.modes or any(m not in MODES for m in self.modes):
            raise ValueError(f'modes must be a non-empty subset of {MODES}')
        if self.scope not in SCOPES:
            raise ValueError(f'scope must be one of {SCOPES}')
        if self.scope == 'symbols' and not self.symbols:
            raise ValueError('scope=symbols requires a non-empty symbols list')
        _bounded('batch_size', self.batch_size, 1, MAX_BATCH)
        _bounded('top_n', self.top_n, 1, 20)
        _bounded('min_technical_score', self.min_technical_score, 0, 100)
        _bounded('retain_snapshots', self.retain_snapshots, 1, 5000)
        self.stale.validate()
        self.options.validate()
        return self

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------- stale-data rejection

def _aware(now: datetime | None) -> datetime:
    return utc_stamp(now if now is not None else datetime.now(UTC)).to_pydatetime()


def stale_reason(record: dict, mode: str, policy: StalePolicy, now: datetime) -> str | None:
    """Return why a scanned stock record must be rejected as stale/unusable, else None.

    Recomputed from the record's own candle labels against ``now`` so a record that sat in
    a cache is judged at snapshot time, not at scan time.
    """
    expected = '1d' if mode == 'swing' else '5m'
    if record.get('bar_interval') != expected:
        return f'unexpected bar interval {record.get("bar_interval")!r} for {mode} mode'
    now = _aware(now)
    if mode == 'swing':
        raw = record.get('last_completed_session_date')
        try:
            session = date.fromisoformat(raw)
        except (TypeError, ValueError):
            return 'no valid completed daily session date'
        age = (now.astimezone(NY).date() - session).days
        if age < 0:
            return f'daily session {raw} is in the future'
        if age > policy.max_daily_age_days:
            return f'last completed session {raw} is {age} days old (limit {policy.max_daily_age_days})'
        return None
    try:
        candle = utc_stamp(record['as_of_utc'])
    except (KeyError, TypeError, ValueError):
        return 'intraday candle timestamp unavailable'
    if pd.isna(candle):  # pd.Timestamp(None) is NaT, which would otherwise compare as "not old"
        return 'intraday candle timestamp unavailable'
    age_min = (utc_stamp(now) - candle).total_seconds() / 60
    if age_min < -1:
        return 'intraday candle is future-dated'
    if age_min > policy.max_intraday_age_minutes:
        return f'last completed 5m candle is {round(age_min)} minutes old (limit {policy.max_intraday_age_minutes})'
    return None


# ---------------------------------------------------------------- candidate construction

def _finite(x):
    return x if isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) else None


def _signals(record: dict) -> list[dict]:
    out = []
    for s in record.get('strategy_signals') or []:
        out.append({'strategy': str(s.get('strategy')), 'side': s.get('side'),
                    'confidence': _finite(s.get('confidence')), 'entry': _finite(s.get('entry')),
                    'stop': _finite(s.get('stop')), 'target': _finite(s.get('target')),
                    'reward_risk': _finite(s.get('reward_risk')),
                    'reasons': [str(r) for r in (s.get('reasons') or [])[:5]]})
    return out


def direction_for(record: dict) -> str | None:
    """'long' or 'short' research direction: from the best confirmed trigger, else technical bias."""
    signals = _signals(record)
    if signals:
        best = max(signals, key=lambda s: s['confidence'] or 0)
        if best['side'] in ('long', 'short'):
            return best['side']
    return {'bullish': 'long', 'bearish': 'short'}.get(record.get('bias'))


def build_candidate(record: dict, mode: str, rank: int) -> dict:
    signals = _signals(record)
    direction = direction_for(record)
    warnings = list(record.get('warnings') or [])
    bias_side = {'bullish': 'long', 'bearish': 'short'}.get(record.get('bias'))
    if signals and bias_side and any(s['side'] in ('long', 'short') and s['side'] != bias_side for s in signals):
        warnings.append('Confirmed trigger side differs from technical bias; treated as separate evidence')
    return {
        'rank': rank, 'symbol': record['symbol'], 'mode': mode, 'research_direction': direction,
        'stock_technical': {
            'bias': record.get('bias'),
            'technical_alignment_score': record.get('technical_alignment_score'),
            'score_is_probability': False,
            'close': record.get('close'), 'rsi14': record.get('rsi14'),
            'relative_volume': record.get('relative_volume'),
            'momentum_last_5_bars_pct': record.get('momentum_last_5_bars_pct'),
            'atr_pct': record.get('atr_pct'), 'above_ema21': record.get('above_ema21'),
            'above_ema50': record.get('above_ema50'), 'above_vwap': record.get('above_vwap'),
        },
        'rule_triggers': {
            'confirmed': bool(signals), 'count': len(signals), 'signals': signals,
            'note': 'Deterministic strategy match on completed candles; independent of technical score and option liquidity.',
        },
        'data_quality': {
            'data_provider': record.get('data_provider'), 'bar_interval': record.get('bar_interval'),
            'as_of_utc': record.get('as_of_utc'), 'as_of_time_meaning': record.get('as_of_time_meaning'),
            'last_completed_session_date': record.get('last_completed_session_date'),
            'last_completed_candle_age_days': record.get('last_completed_candle_age_days'),
            'data_age_minutes': record.get('data_age_minutes'),
            'quote_timestamp_utc': None, 'quote_freshness': 'unknown',
            'bar_quality': record.get('bar_quality') or {},
        },
        'options': None,
        'warnings': warnings,
    }


def rank_key(record: dict):
    """Documented ordering: confirmed rule trigger first, then technical score, then symbol.

    This is an ORDER, not a blended score; the components stay separate in the output.
    """
    return (not record.get('strategy_signals'), -(record.get('technical_alignment_score') or 0), record['symbol'])


RANK_BASIS = 'confirmed_rule_trigger_then_technical_alignment_score_then_symbol (no combined score)'


def option_right_for(direction: str | None) -> str | None:
    return {'long': 'call', 'short': 'put'}.get(direction)


def options_eligibility(candidate: dict, policy: OptionsPolicy) -> tuple[bool, str]:
    if not policy.enabled:
        return False, 'options qualification disabled'
    if option_right_for(candidate['research_direction']) is None:
        return False, 'no directional bias or trigger side (mixed)'
    if policy.require_trigger:
        return (True, '') if candidate['rule_triggers']['confirmed'] else (False, 'no confirmed rule trigger (required by policy)')
    if candidate['rule_triggers']['confirmed']:
        return True, ''
    score = candidate['stock_technical']['technical_alignment_score'] or 0
    return (True, '') if score >= policy.min_technical_score else (
        False, f'technical score {score} below options minimum {policy.min_technical_score}')


def qualify_options(candidate: dict, policy: OptionsPolicy, screen_options: Callable[..., dict],
                    now: datetime) -> dict:
    """Run the existing options screen, then reject contracts whose last trade is stale/unknown."""
    right = option_right_for(candidate['research_direction'])
    block: dict[str, Any] = {'status': 'error', 'right': right, 'reason': '', 'contracts': [],
                             'contracts_examined': 0, 'contracts_passing_filters': 0,
                             'rejected_stale_or_unknown_last_trade': 0,
                             'underlying_reference_price': None, 'underlying_price_source': None,
                             'underlying_quote_timestamp_utc': None, 'warnings': []}
    try:
        res = screen_options(candidate['symbol'], right, policy)
    except Exception as exc:  # provider failures must not abort the whole snapshot
        block['reason'] = f'{type(exc).__name__}: {exc}'[:200]
        return block
    block.update({'contracts_examined': res.get('contracts_examined', 0),
                  'contracts_passing_filters': res.get('contracts_passing_filters', 0),
                  'underlying_reference_price': res.get('underlying_reference_price'),
                  'underlying_price_source': res.get('underlying_price_source'),
                  'expirations_checked': res.get('expirations_checked', []),
                  'screen_errors': res.get('errors') or {},
                  'warnings': list(res.get('warnings') or [])})
    kept, rejected = [], 0
    for c in res.get('contracts') or []:
        age = c.get('last_trade_age_minutes')
        if policy.max_last_trade_age_minutes and (age is None or age > policy.max_last_trade_age_minutes):
            rejected += 1
            continue
        if c.get('bid') is None or c.get('ask') is None or not (c['ask'] > c['bid'] > 0):
            rejected += 1  # defensive: never publish a locked/crossed/one-sided quote
            continue
        kept.append(c)
    kept = kept[:policy.contracts_per_underlying]
    block['rejected_stale_or_unknown_last_trade'] = rejected
    block['contracts'] = kept
    if block['underlying_reference_price'] is None:
        block['warnings'].append('Underlying reference price unavailable: moneyness and break-even % are unknown')
    if kept:
        block['status'], block['reason'] = 'qualified', ''
    else:
        block['status'] = 'none_qualified'
        block['reason'] = ('all passing contracts had a stale/unknown last trade' if rejected
                           else 'no contracts passed liquidity filters')
    return block


# ---------------------------------------------------------------- pipeline

ScanFn = Callable[[list[str], str], dict]          # -> {'records': [...], 'errors': {sym: msg}, 'provider': str}
UniverseFn = Callable[[PipelineConfig, int], tuple[list[str], list[str], int | None]]


def _chunks(items: list[str], size: int):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def run_pipeline(config: PipelineConfig, *, scan: ScanFn, resolve_universe: UniverseFn,
                 screen_options: Callable[..., dict], universe_offset: int = 0,
                 now: datetime | None = None) -> dict:
    """Scan -> reject stale -> rank (components separate) -> qualify options. Returns a snapshot."""
    config.validate()
    now = _aware(now)
    symbols, notes, next_offset = resolve_universe(config, universe_offset)
    symbols = list(dict.fromkeys(symbols))
    modes_out: dict[str, dict] = {}
    options_budget = config.options.max_underlyings
    options_done: dict[tuple[str, str], dict] = {}
    for mode in config.modes:
        records, errors, providers = [], {}, set()
        for batch in _chunks(symbols, config.batch_size):
            try:
                res = scan(batch, mode)
            except Exception as exc:
                errors.update({s: f'scan failed: {type(exc).__name__}'[:160] for s in batch})
                continue
            providers.add(res.get('provider'))
            records += res.get('records') or []
            errors.update(res.get('errors') or {})
        for s in symbols:  # symbols absent from both records and errors are reported, never dropped silently
            if s not in errors and not any(r.get('symbol') == s for r in records):
                errors[s] = 'no result returned'
        fresh, stale = [], []
        for rec in records:
            reason = stale_reason(rec, mode, config.stale, now)
            if reason:
                stale.append({'symbol': rec.get('symbol'), 'reason': reason})
            else:
                fresh.append(rec)
        eligible = [r for r in fresh if (r.get('technical_alignment_score') or 0) >= config.min_technical_score]
        eligible.sort(key=rank_key)
        candidates = [build_candidate(r, mode, i + 1) for i, r in enumerate(eligible[:config.top_n])]
        for cand in candidates:
            ok, why = options_eligibility(cand, config.options)
            if not ok:
                cand['options'] = {'status': 'skipped', 'reason': why, 'contracts': []}
                continue
            key = (cand['symbol'], option_right_for(cand['research_direction']))
            if key in options_done:
                cand['options'] = options_done[key]
                continue
            if options_budget <= 0:
                cand['options'] = {'status': 'skipped', 'reason': 'options underlying budget exhausted', 'contracts': []}
                continue
            options_budget -= 1
            cand['options'] = options_done[key] = qualify_options(cand, config.options, screen_options, now)
        modes_out[mode] = {
            'bar_interval': '5m' if mode == 'day' else '1d', 'data_providers': sorted(p for p in providers if p),
            'scanned': len(symbols), 'evaluated': len(records), 'fresh': len(fresh),
            'candidates': candidates, 'rejected_stale': stale, 'errors': errors,
            'rank_basis': RANK_BASIS,
        }
    ttl = min(config.stale.ttl_minutes_day if m == 'day' else config.stale.ttl_minutes_swing for m in config.modes)
    all_cands = [c for m in modes_out.values() for c in m['candidates']]
    has_issues = any(m['errors'] or m['rejected_stale'] for m in modes_out.values())
    status = 'empty' if not all_cands else 'partial' if has_issues else 'ok'
    snap = {
        'schema_version': SCHEMA_VERSION,
        'snapshot_id': '',
        'generated_at_utc': now.astimezone(UTC).isoformat(),
        'expires_at_utc': (now + timedelta(minutes=ttl)).astimezone(UTC).isoformat(),
        'status': status, 'read_only': True, 'execution_enabled': False,
        'config': config.to_dict(),
        'universe': {'scope': config.scope, 'symbols_scanned': symbols, 'universe_offset': universe_offset,
                     'next_universe_offset': next_offset, 'notes': notes},
        'modes': modes_out,
        'summary': {
            'candidates': len(all_cands),
            'confirmed_rule_triggers': sum(c['rule_triggers']['confirmed'] for c in all_cands),
            'options_qualified': sum(bool(c['options'] and c['options']['status'] == 'qualified') for c in all_cands),
            'rejected_stale': sum(len(m['rejected_stale']) for m in modes_out.values()),
            'errors': sum(len(m['errors']) for m in modes_out.values()),
        },
        'disclaimers': list(DISCLAIMERS),
    }
    digest = hashlib.sha256(json.dumps(snap, sort_keys=True, default=str).encode()).hexdigest()[:10]
    snap['snapshot_id'] = f'{now.astimezone(UTC):%Y%m%dT%H%M%SZ}-{digest}'
    validate_snapshot(snap)
    return snap


def rotate_offset(next_offset: int | None) -> int:
    """Next scheduled universe slice; wraps to 0 once the universe has been fully covered."""
    return int(next_offset) if isinstance(next_offset, int) and next_offset > 0 else 0


# ---------------------------------------------------------------- validation + Overview contract

def _walk_keys(obj, path=''):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield f'{path}/{k}', k
            yield from _walk_keys(v, f'{path}/{k}')
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _walk_keys(v, f'{path}[{i}]')


def validate_snapshot(snap: dict) -> None:
    """Fail closed on structure that could blur the read-only / separated-evidence contract."""
    if not isinstance(snap, dict) or snap.get('schema_version') != SCHEMA_VERSION:
        raise ValueError('unsupported or missing schema_version')
    if snap.get('read_only') is not True or snap.get('execution_enabled') is not False:
        raise ValueError('snapshot must declare read_only=true and execution_enabled=false')
    for key in ('snapshot_id', 'generated_at_utc', 'expires_at_utc', 'status', 'modes', 'summary'):
        if key not in snap:
            raise ValueError(f'missing {key}')
    if snap['status'] not in ('ok', 'partial', 'empty'):
        raise ValueError('invalid status')
    for path, key in _walk_keys(snap):
        if str(key).lower() in FORBIDDEN_KEYS:
            raise ValueError(f'forbidden key in snapshot: {path}')
    for mode in snap['modes'].values():
        for c in mode['candidates']:
            for part in ('stock_technical', 'rule_triggers', 'data_quality'):
                if part not in c:
                    raise ValueError(f'candidate {c.get("symbol")} missing {part}')
            if c['stock_technical'].get('score_is_probability') is not False:
                raise ValueError('technical score must not be flagged as a probability')
            if c['options'] and any('liquidity_score' in str(k) for k in c['stock_technical']):
                raise ValueError('liquidity must not appear in stock_technical')


OVERVIEW_CONTRACT = 'tradeflipper.overview.opportunities/1'


def to_overview_payload(snapshot: dict | None, *, now: datetime | None = None) -> dict:
    """Compact, read-only payload for TradeFlipper Overview.

    An expired or missing snapshot yields ``items: []`` so stale research is never displayed
    as current. Callers must show ``freshness`` and ``notices``.
    """
    now = _aware(now)
    base = {'contract_version': OVERVIEW_CONTRACT, 'read_only': True, 'execution_enabled': False,
            'items': [], 'notices': list(DISCLAIMERS)}
    if not snapshot:
        return {**base, 'freshness': 'missing', 'snapshot_id': None, 'generated_at_utc': None,
                'expires_at_utc': None, 'age_minutes': None, 'status': 'missing',
                'reason': 'No opportunity snapshot has been persisted yet.', 'summary': None}
    generated, expires = utc_stamp(snapshot['generated_at_utc']), utc_stamp(snapshot['expires_at_utc'])
    age = max(0, round((utc_stamp(now) - generated).total_seconds() / 60))
    expired = utc_stamp(now) > expires
    out = {**base, 'snapshot_id': snapshot['snapshot_id'], 'generated_at_utc': snapshot['generated_at_utc'],
           'expires_at_utc': snapshot['expires_at_utc'], 'age_minutes': age, 'status': snapshot['status'],
           'summary': snapshot['summary'], 'freshness': 'expired' if expired else 'fresh'}
    if expired:
        out['reason'] = 'Snapshot is past its expiry; items withheld until a new scan completes.'
        return out
    items = []
    for mode, block in snapshot['modes'].items():
        for c in block['candidates']:
            opt = c.get('options')
            items.append({
                'symbol': c['symbol'], 'mode': mode, 'rank': c['rank'],
                'research_direction': c['research_direction'],
                'stock_technical': c['stock_technical'],
                'rule_triggers': {'confirmed': c['rule_triggers']['confirmed'],
                                  'signals': c['rule_triggers']['signals']},
                'option_liquidity': None if not opt else {
                    'status': opt['status'], 'right': opt.get('right'), 'reason': opt.get('reason', ''),
                    'underlying_reference_price': opt.get('underlying_reference_price'),
                    'contracts': [{k: ct.get(k) for k in (
                        'contract', 'expiration', 'dte_calendar_days', 'strike', 'bid', 'ask', 'spread_pct',
                        'volume', 'open_interest', 'liquidity_score', 'expiration_breakeven_underlying_price',
                        'expiration_breakeven_move_pct', 'moneyness', 'greeks_complete',
                        'quote_freshness', 'executable_quote_verified', 'last_trade_age_minutes')}
                        for ct in opt['contracts']]},
                'data_quality': c['data_quality'], 'warnings': c['warnings'],
            })
    out['items'] = items
    out['rejections'] = {m: {'stale': len(b['rejected_stale']), 'errors': len(b['errors'])}
                         for m, b in snapshot['modes'].items()}
    return out
