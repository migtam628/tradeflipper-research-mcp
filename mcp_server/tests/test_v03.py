"""v0.3 regression tests: scheduled opportunity pipeline, persistence and Overview contract.

All offline: scan, universe and option screening are injected fakes. Nothing here may
reach a network, a broker, or TradeFlipper's database.
"""
from __future__ import annotations

import json
import os
import stat
import threading
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from mcp_server.opportunity import (OVERVIEW_CONTRACT, OptionsPolicy, PipelineConfig, StalePolicy,
                                    direction_for, qualify_options, rank_key, rotate_offset,
                                    run_pipeline, stale_reason, to_overview_payload, validate_snapshot)
from mcp_server.overview_api import make_server
from mcp_server.snapshot_store import SnapshotStore

NOW = datetime(2026, 10, 23, 18, 0, tzinfo=UTC)  # Friday 14:00 ET


def rec(symbol, score=70, bias='bullish', signals=None, mode='swing', session='2026-10-22', as_of=None):
    base = {'symbol': symbol, 'mode': mode, 'bar_interval': '1d' if mode == 'swing' else '5m',
            'data_provider': 'fake', 'bias': bias, 'technical_alignment_score': score,
            'score_is_probability': False, 'close': 100.0, 'rsi14': 55.0, 'relative_volume': 1.2,
            'momentum_last_5_bars_pct': 1.0, 'atr_pct': 2.0, 'above_ema21': True, 'above_ema50': True,
            'above_vwap': None, 'as_of_utc': as_of or '2026-10-22T00:00:00+00:00',
            'as_of_time_meaning': 'daily session-date label', 'last_completed_session_date': session,
            'last_completed_candle_age_days': 1, 'data_age_minutes': None, 'quote_timestamp_utc': None,
            'quote_age_minutes': None, 'warnings': [], 'bar_quality': {},
            'strategy_signals': signals or [], 'has_current_strategy_signal': bool(signals)}
    return base


def signal(side='long', confidence=0.8, name='pullback'):
    return {'strategy': name, 'side': side, 'confidence': confidence, 'entry': 100.0, 'stop': 95.0,
            'target': 110.0, 'reward_risk': 2.0, 'reasons': ['test']}


def contract(symbol='AAA', right='call', age=10, bid=2.0, ask=2.1, score=80):
    return {'contract': f'{symbol}261120{right[0].upper()}00100000', 'right': right, 'expiration': '2026-11-20',
            'dte_calendar_days': 28, 'strike': 100.0, 'bid': bid, 'ask': ask, 'spread_pct': 4.9,
            'volume': 500, 'open_interest': 2000, 'liquidity_score': score,
            'expiration_breakeven_underlying_price': 102.1, 'expiration_breakeven_move_pct': 2.1,
            'moneyness': 'ATM', 'greeks': {'delta': None}, 'greeks_complete': False,
            'quote_freshness': 'unknown', 'executable_quote_verified': False,
            'last_trade_age_minutes': age}


def fake_options(contracts=None, calls=None):
    def screen(symbol, right, policy):
        if calls is not None:
            calls.append((symbol, right))
        return {'contracts': contracts if contracts is not None else [contract(symbol, right)],
                'contracts_examined': 50, 'contracts_passing_filters': 1,
                'underlying_reference_price': 100.0, 'underlying_price_source': 'fake',
                'expirations_checked': ['2026-11-20'], 'errors': {}, 'warnings': ['Yahoo caveat']}
    return screen


def universe(symbols):
    return lambda cfg, offset: (list(symbols), [], None)


def scanner(records, errors=None, calls=None):
    def scan(batch, mode):
        if calls is not None:
            calls.append((list(batch), mode))
        found = [r for r in records if r['symbol'] in batch]
        return {'records': [dict(r, mode=mode) for r in found], 'errors': dict(errors or {}), 'provider': 'fake'}
    return scan


def cfg(**kw):
    kw.setdefault('symbols', ('AAA', 'BBB', 'CCC'))
    return PipelineConfig.from_dict(kw)


def run(records, config=None, options=None, **kw):
    config = config or cfg()
    return run_pipeline(config, scan=scanner(records, kw.pop('errors', None), kw.pop('scan_calls', None)),
                        resolve_universe=universe(config.symbols or ['AAA', 'BBB', 'CCC']),
                        screen_options=options or fake_options(), now=NOW, **kw)


# ------------------------------------------------------------------ config

def test_config_rejects_unknown_keys_bad_modes_and_unbounded_values():
    with pytest.raises(ValueError, match='Unknown config keys'):
        PipelineConfig.from_dict({'symbols': ['AAA'], 'place_orders': True})
    with pytest.raises(ValueError, match='Unknown options'):
        PipelineConfig.from_dict({'symbols': ['AAA'], 'options': {'auto_trade': True}})
    for bad in ({'modes': ['scalp']}, {'scope': 'everything'}, {'batch_size': 41}, {'top_n': 0},
                {'scope': 'symbols', 'symbols': []}, {'symbols': ['aaa bbb']},
                {'options': {'min_dte': 30, 'max_dte': 10}}, {'stale': {'max_daily_age_days': 99}}):
        with pytest.raises(ValueError):
            PipelineConfig.from_dict({'symbols': ['AAA'], **bad})


def test_config_normalises_symbols_and_universe_scope_needs_no_symbols():
    assert PipelineConfig.from_dict({'symbols': ['aapl', 'AAPL', 'msft']}).symbols == ('AAPL', 'MSFT')
    assert PipelineConfig.from_dict({'scope': 'universe'}).scope == 'universe'


# ------------------------------------------------------------------ stale-data rejection

def test_swing_record_older_than_limit_is_rejected_but_weekend_gap_is_not():
    policy = StalePolicy()
    assert stale_reason(rec('A', session='2026-10-22'), 'swing', policy, NOW) is None
    assert stale_reason(rec('A', session='2026-10-19'), 'swing', policy, NOW) is None  # 4 days: at limit
    assert 'days old' in stale_reason(rec('A', session='2026-10-18'), 'swing', policy, NOW)
    assert 'future' in stale_reason(rec('A', session='2026-10-30'), 'swing', policy, NOW)
    assert 'no valid' in stale_reason(rec('A', session=None), 'swing', policy, NOW)


def test_intraday_age_is_recomputed_from_candle_timestamp_not_scan_time_flag():
    policy = StalePolicy()
    fresh = rec('A', mode='day', as_of=(NOW - timedelta(minutes=10)).isoformat())
    fresh['data_age_minutes'] = 999  # a stale cached flag must not decide
    assert stale_reason(fresh, 'day', policy, NOW) is None
    old = rec('A', mode='day', as_of=(NOW - timedelta(minutes=90)).isoformat())
    assert 'minutes old' in stale_reason(old, 'day', policy, NOW)
    assert 'unavailable' in stale_reason(dict(fresh, as_of_utc=None), 'day', policy, NOW)
    assert 'unexpected bar interval' in stale_reason(rec('A', mode='day'), 'swing', policy, NOW)


def test_stale_stock_is_reported_and_never_ranked_or_given_options():
    calls = []
    snap = run([rec('AAA', 95, signals=[signal()], session='2026-10-10'), rec('BBB', 60)],
               options=fake_options(calls=calls))
    block = snap['modes']['swing']
    assert [c['symbol'] for c in block['candidates']] == ['BBB']
    assert block['rejected_stale'][0]['symbol'] == 'AAA' and 'days old' in block['rejected_stale'][0]['reason']
    assert all(sym != 'AAA' for sym, _ in calls)
    assert snap['status'] == 'partial'


# ------------------------------------------------------------------ ranking and separation

def test_confirmed_trigger_orders_first_without_a_blended_score():
    records = [rec('AAA', 90), rec('BBB', 60, signals=[signal()]), rec('CCC', 80)]
    snap = run(records, config=cfg(options={'enabled': False}))
    order = [c['symbol'] for c in snap['modes']['swing']['candidates']]
    assert order == ['BBB', 'AAA', 'CCC']
    assert sorted(records, key=rank_key)[0]['symbol'] == 'BBB'
    assert 'no combined score' in snap['modes']['swing']['rank_basis']
    flat = json.dumps(snap)
    assert 'combined_score' not in flat and 'composite_score' not in flat


def test_stock_score_trigger_and_option_liquidity_live_in_separate_blocks():
    snap = run([rec('AAA', 85, signals=[signal()])])
    cand = snap['modes']['swing']['candidates'][0]
    assert 'technical_alignment_score' in cand['stock_technical']
    assert cand['stock_technical']['score_is_probability'] is False
    assert not any('liquidity' in k or 'trigger' in k or 'signal' in k for k in cand['stock_technical'])
    assert cand['rule_triggers']['confirmed'] is True and cand['rule_triggers']['signals'][0]['strategy'] == 'pullback'
    assert 'technical_alignment_score' not in cand['rule_triggers']
    opt = cand['options']
    assert opt['status'] == 'qualified' and opt['contracts'][0]['liquidity_score'] == 80
    assert 'liquidity_score' not in cand['stock_technical'] and 'liquidity_score' not in cand['rule_triggers']


def test_no_trigger_means_no_confirmed_flag_even_with_a_perfect_score():
    snap = run([rec('AAA', 100)], config=cfg(options={'enabled': False}))
    cand = snap['modes']['swing']['candidates'][0]
    assert cand['rule_triggers'] == {**cand['rule_triggers'], 'confirmed': False, 'count': 0, 'signals': []}
    assert snap['summary']['confirmed_rule_triggers'] == 0


def test_trigger_side_conflicting_with_bias_is_flagged_not_blended():
    snap = run([rec('AAA', 80, bias='bearish', signals=[signal('long')])], config=cfg(options={'enabled': False}))
    cand = snap['modes']['swing']['candidates'][0]
    assert cand['research_direction'] == 'long'
    assert any('differs from technical bias' in w for w in cand['warnings'])


def test_min_technical_score_and_top_n_apply_after_stale_filter():
    records = [rec(s, sc) for s, sc in (('AAA', 90), ('BBB', 70), ('CCC', 40))]
    snap = run(records, config=cfg(min_technical_score=60, top_n=1, options={'enabled': False}))
    assert [c['symbol'] for c in snap['modes']['swing']['candidates']] == ['AAA']


# ------------------------------------------------------------------ options qualification

def test_direction_and_right_selection():
    assert direction_for(rec('A', bias='bullish')) == 'long'
    assert direction_for(rec('A', bias='bearish')) == 'short'
    assert direction_for(rec('A', bias='mixed')) is None
    assert direction_for(rec('A', bias='bullish', signals=[signal('short')])) == 'short'
    calls = []
    run([rec('AAA', 80, bias='bearish'), rec('BBB', 80, bias='bullish'), rec('CCC', 80, bias='mixed')],
        options=fake_options(calls=calls))
    assert sorted(calls) == [('AAA', 'put'), ('BBB', 'call')]


def test_options_gate_require_trigger_and_min_score_and_budget():
    calls = []
    records = [rec('AAA', 95), rec('BBB', 60, signals=[signal()]), rec('CCC', 72)]
    snap = run(records, config=cfg(options={'require_trigger': True}), options=fake_options(calls=calls))
    assert calls == [('BBB', 'call')]
    skipped = {c['symbol']: c['options'] for c in snap['modes']['swing']['candidates']}
    assert skipped['AAA']['status'] == 'skipped' and 'required by policy' in skipped['AAA']['reason']

    calls.clear()
    snap = run(records, config=cfg(options={'min_technical_score': 90}), options=fake_options(calls=calls))
    assert sorted(calls) == [('AAA', 'call'), ('BBB', 'call')]  # AAA by score, BBB by trigger
    assert 'below options minimum' in [c for c in snap['modes']['swing']['candidates'] if c['symbol'] == 'CCC'][0]['options']['reason']

    calls.clear()
    snap = run(records, config=cfg(options={'max_underlyings': 1, 'min_technical_score': 0}),
               options=fake_options(calls=calls))
    assert len(calls) == 1
    assert [c['options']['reason'] for c in snap['modes']['swing']['candidates']].count('options underlying budget exhausted') == 2


def test_contracts_with_stale_or_unknown_last_trade_or_bad_quotes_are_rejected():
    contracts = [contract(age=5), contract(age=500), contract(age=None), contract(bid=0.0),
                 contract(bid=2.0, ask=2.0), contract(bid=3.0, ask=2.0)]
    snap = run([rec('AAA', 90)], options=fake_options(contracts))
    opt = snap['modes']['swing']['candidates'][0]['options']
    assert len(opt['contracts']) == 1 and opt['contracts'][0]['last_trade_age_minutes'] == 5
    assert opt['rejected_stale_or_unknown_last_trade'] == 5
    assert opt['contracts'][0]['quote_freshness'] == 'unknown'
    assert opt['contracts'][0]['executable_quote_verified'] is False


def test_all_contracts_stale_yields_none_qualified_not_stale_contracts():
    snap = run([rec('AAA', 90)], options=fake_options([contract(age=900)]))
    opt = snap['modes']['swing']['candidates'][0]['options']
    assert opt['status'] == 'none_qualified' and opt['contracts'] == [] and 'stale' in opt['reason']


def test_zero_last_trade_limit_disables_trade_age_rejection():
    snap = run([rec('AAA', 90)], config=cfg(options={'max_last_trade_age_minutes': 0}),
               options=fake_options([contract(age=900)]))
    assert snap['modes']['swing']['candidates'][0]['options']['status'] == 'qualified'


def test_option_provider_error_is_contained_and_missing_greeks_stay_null():
    def boom(*a, **k):
        raise RuntimeError('yahoo down')
    snap = run([rec('AAA', 90), rec('BBB', 80)], options=boom)
    assert snap['modes']['swing']['candidates'][0]['options']['status'] == 'error'
    assert 'yahoo down' in snap['modes']['swing']['candidates'][0]['options']['reason']
    good = run([rec('AAA', 90)])['modes']['swing']['candidates'][0]['options']['contracts'][0]
    assert good['greeks'] == {'delta': None} and good['greeks_complete'] is False


def test_missing_underlying_reference_adds_warning():
    def screen(symbol, right, policy):
        return {'contracts': [contract()], 'underlying_reference_price': None, 'warnings': []}
    opt = qualify_options({'symbol': 'AAA', 'research_direction': 'long'}, OptionsPolicy(), screen, NOW)
    assert any('reference price unavailable' in w for w in opt['warnings'])


# ------------------------------------------------------------------ scan robustness / batching

def test_scan_batches_universe_and_reports_missing_and_failed_symbols():
    symbols = tuple(f'T{i:02d}' for i in range(45))
    calls = []
    config = cfg(symbols=symbols, options={'enabled': False}, batch_size=40)
    snap = run([rec(symbols[0], 70)], config=config, scan_calls=calls, errors={symbols[1]: 'market data unavailable'})
    assert [len(b) for b, _ in calls] == [40, 5]
    errors = snap['modes']['swing']['errors']
    assert errors[symbols[1]] == 'market data unavailable' and errors[symbols[2]] == 'no result returned'
    assert snap['modes']['swing']['scanned'] == 45


def test_scan_exception_marks_batch_errors_without_aborting_snapshot():
    def scan(batch, mode):
        raise ConnectionError('offline')
    snap = run_pipeline(cfg(), scan=scan, resolve_universe=universe(['AAA']), screen_options=fake_options(), now=NOW)
    assert snap['status'] == 'empty' and snap['modes']['swing']['errors']['AAA'].startswith('scan failed')


def test_day_and_swing_modes_are_scanned_independently():
    calls = []
    day = rec('AAA', 70, mode='day', as_of=(NOW - timedelta(minutes=5)).isoformat())
    swing = rec('AAA', 70)

    def scan(batch, mode):
        calls.append(mode)
        return {'records': [day if mode == 'day' else swing], 'errors': {}, 'provider': 'fake'}
    snap = run_pipeline(cfg(modes=['swing', 'day'], options={'enabled': False}), scan=scan,
                        resolve_universe=universe(['AAA']), screen_options=fake_options(), now=NOW)
    assert calls == ['swing', 'day'] and set(snap['modes']) == {'swing', 'day'}
    assert snap['expires_at_utc'] == (NOW + timedelta(minutes=15)).isoformat()  # shortest TTL wins


def test_universe_rotation_wraps():
    assert rotate_offset(40) == 40 and rotate_offset(None) == 0 and rotate_offset(0) == 0


def test_pipeline_is_deterministic_for_fixed_inputs():
    a, b = run([rec('AAA', 80)]), run([rec('AAA', 80)])
    assert a == b and a['snapshot_id'].startswith('20261023T180000Z-')


# ------------------------------------------------------------------ validation

def test_validate_rejects_execution_flags_forbidden_keys_and_probability_scores():
    snap = run([rec('AAA', 80)])
    validate_snapshot(snap)
    for mutate in (lambda s: s.update(execution_enabled=True), lambda s: s.update(read_only=False),
                   lambda s: s.update(schema_version='9'), lambda s: s['config'].update(api_key='x'),
                   lambda s: s['modes']['swing']['candidates'][0]['stock_technical'].update(score_is_probability=True),
                   lambda s: s['modes']['swing']['candidates'][0].update(combined_score=99)):
        bad = json.loads(json.dumps(snap))
        mutate(bad)
        with pytest.raises(ValueError):
            validate_snapshot(bad)


# ------------------------------------------------------------------ persistence

def test_store_round_trip_permissions_latest_and_retention(tmp_path):
    store = SnapshotStore(tmp_path / 'snaps')
    ids = []
    for minutes in range(4):
        snap = run_pipeline(cfg(options={'enabled': False}), scan=scanner([rec('AAA', 70 + minutes)]),
                            resolve_universe=universe(['AAA']), screen_options=fake_options(),
                            now=NOW + timedelta(minutes=minutes))
        path = store.save(snap, keep=3)
        ids.append(snap['snapshot_id'])
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE((tmp_path / 'snaps').stat().st_mode) == 0o700
    assert store.ids() == list(reversed(ids))[:3]
    assert store.latest()['snapshot_id'] == ids[-1]
    assert store.load(ids[-1])['modes']['swing']['candidates'][0]['stock_technical']['technical_alignment_score'] == 73
    assert not list((tmp_path / 'snaps').glob('.tmp-*'))
    with pytest.raises(FileNotFoundError):
        store.load(ids[0])


def test_store_rejects_traversal_ids_and_invalid_or_tampered_files(tmp_path):
    store = SnapshotStore(tmp_path)
    for bad in ('../etc/passwd', 'latest', '', 'x' * 40):
        with pytest.raises(ValueError):
            store.load(bad)
    snap = run([rec('AAA', 70)])
    store.save(snap)
    (tmp_path / f'snapshot_{snap["snapshot_id"]}.json').write_text(json.dumps({**snap, 'execution_enabled': True}))
    with pytest.raises(ValueError):
        store.load(snap['snapshot_id'])
    with pytest.raises(ValueError):
        store.save({**snap, 'read_only': False})


def test_store_state_and_empty_directory(tmp_path):
    store = SnapshotStore(tmp_path / 'new')
    assert store.latest() is None and store.ids() == [] and store.load_state() == {}
    store.save_state({'universe_offset': 80})
    assert store.load_state()['universe_offset'] == 80


def test_default_directory_is_outside_the_repository(monkeypatch):
    from mcp_server.snapshot_store import default_dir
    monkeypatch.delenv('TRADEFLIPPER_OPPORTUNITY_DIR', raising=False)
    assert Path(__file__).resolve().parents[2] not in default_dir().resolve().parents
    monkeypatch.setenv('TRADEFLIPPER_OPPORTUNITY_DIR', '/tmp/custom')
    assert str(default_dir()) == '/tmp/custom'


# ------------------------------------------------------------------ Overview contract

def test_overview_payload_fresh_expired_and_missing():
    snap = run([rec('AAA', 85, signals=[signal()])])
    fresh = to_overview_payload(snap, now=NOW + timedelta(minutes=30))
    assert fresh['contract_version'] == OVERVIEW_CONTRACT and fresh['freshness'] == 'fresh'
    assert fresh['read_only'] is True and fresh['execution_enabled'] is False and fresh['age_minutes'] == 30
    item = fresh['items'][0]
    assert set(item) >= {'stock_technical', 'rule_triggers', 'option_liquidity', 'data_quality', 'warnings'}
    assert item['option_liquidity']['contracts'][0]['liquidity_score'] == 80
    assert 'liquidity_score' not in item['stock_technical'] and 'technical_alignment_score' not in item['option_liquidity']

    expired = to_overview_payload(snap, now=NOW + timedelta(hours=7))
    assert expired['freshness'] == 'expired' and expired['items'] == [] and 'withheld' in expired['reason']

    missing = to_overview_payload(None, now=NOW)
    assert missing['freshness'] == 'missing' and missing['items'] == [] and missing['status'] == 'missing'


def test_overview_payload_omits_raw_scan_internals():
    payload = json.dumps(to_overview_payload(run([rec('AAA', 85)]), now=NOW))
    for leaked in ('"config"', 'symbols_scanned', 'greeks":', 'strategy_signals'):
        assert leaked not in payload


# ------------------------------------------------------------------ read-only HTTP API

@pytest.fixture
def api(tmp_path):
    store = SnapshotStore(tmp_path)
    snap = run_pipeline(cfg(options={'enabled': False}), scan=scanner([rec('AAA', 75)]),
                        resolve_universe=universe(['AAA']), screen_options=fake_options(),
                        now=datetime.now(UTC))
    store.save(snap)
    server = make_server(store, '127.0.0.1', 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f'http://127.0.0.1:{server.server_address[1]}', snap
    server.shutdown()
    server.server_close()


def fetch(url, method='GET'):
    req = urllib.request.Request(url, method=method, data=b'{}' if method != 'GET' else None)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as err:
        return err.code, json.loads(err.read())


def test_api_serves_latest_snapshot_and_listing_and_health(api):
    base, snap = api
    code, body = fetch(base + '/v1/opportunities/latest')
    assert code == 200 and body['freshness'] == 'fresh' and body['items'][0]['symbol'] == 'AAA'
    assert fetch(base + '/healthz')[1] == {'ok': True, 'read_only': True}
    assert fetch(base + '/v1/opportunities/snapshots')[1]['snapshot_ids'] == [snap['snapshot_id']]
    assert fetch(f'{base}/v1/opportunities/snapshots/{snap["snapshot_id"]}')[1]['snapshot_id'] == snap['snapshot_id']


def test_api_is_get_only_and_validates_ids(api):
    base, _ = api
    for method in ('POST', 'PUT', 'PATCH', 'DELETE'):
        assert fetch(base + '/v1/opportunities/latest', method)[0] == 405
    assert fetch(base + '/v1/opportunities/snapshots/..%2Fsecret')[0] == 400
    assert fetch(base + '/v1/opportunities/snapshots/20200101T000000Z-0123456789')[0] == 404
    assert fetch(base + '/v1/orders')[0] == 404


def test_api_refuses_non_loopback_binding(tmp_path):
    for host in ('0.0.0.0', '192.168.1.5', ''):
        with pytest.raises(ValueError, match='loopback-only'):
            make_server(SnapshotStore(tmp_path), host, 0)


# ------------------------------------------------------------------ read-only guarantee

def test_new_modules_contain_no_order_or_broker_write_surface():
    root = Path(__file__).resolve().parents[1]
    text = '\n'.join((root / name).read_text() for name in
                     ('opportunity.py', 'snapshot_store.py', 'overview_api.py', 'opportunity_agent.py'))
    for forbidden in ('submit_order', 'cancel_order', 'place_stock_instruction',
                      'requests.post', 'alpaca', 'webull', 'ExecutionSetting', 'SessionLocal'):
        assert forbidden.lower() not in text.lower(), forbidden
