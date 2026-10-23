"""Scheduled, read-only opportunity discovery agent (v0.3).

  python -m mcp_server.opportunity_agent run   [--config cfg.json] [--symbols A,B] [--modes swing,day]
  python -m mcp_server.opportunity_agent show  [--id ID]
  python -m mcp_server.opportunity_agent serve [--port 8766]      # loopback-only, GET-only
  python -m mcp_server.opportunity_agent prune [--keep N]

`run` is one-shot: schedule it with systemd timer or cron (see deploy/). It reuses the MCP
research helpers from server.py, writes a structured snapshot, and never orders or changes settings.
Run it with the TradeFlipper checkout as the working directory (deploy/run_opportunity_agent.sh).
"""
from __future__ import annotations

import argparse
import fcntl
import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

from .opportunity import PipelineConfig, run_pipeline, to_overview_payload
from .overview_api import make_server
from .snapshot_store import SnapshotStore

log = logging.getLogger('opportunity_agent')


def build_runtime():
    """Wire the pipeline to the existing MCP research functions (imports the TradeFlipper backend)."""
    from . import server as S

    def resolve_universe(cfg: PipelineConfig, offset: int):
        if cfg.scope == 'symbols':
            return list(cfg.symbols), [], None
        pool, notes = S._resolve_pool(cfg.scope, '')
        clean = list(dict.fromkeys(s for s in pool if isinstance(s, str) and len(s) <= 10
                                   and s and s[0].isalpha() and all(c.isalnum() or c in '.-' for c in s)))
        if cfg.scope != 'universe':
            return clean[:cfg.batch_size], notes, None
        chosen = clean[offset:offset + cfg.batch_size]
        nxt = offset + len(chosen)
        return chosen, notes, (nxt if nxt < len(clean) else None)

    def scan(symbols: list[str], mode: str) -> dict:
        interval, period = ('5m', '5d') if mode == 'day' else ('1d', '1y')
        provider = S.get_provider()
        frames = provider.bars_many(symbols, period, interval)
        records, errors = [], {}
        for sym in symbols:
            raw = frames.get(sym)
            if raw is None or raw.empty:
                errors[sym] = 'market data unavailable'
                continue
            try:
                df, quality = S._prepare(raw, interval)
                records.append(S._evaluate(sym, df, mode, interval, provider.name, quality))
            except (ValueError, KeyError, TypeError) as exc:
                errors[sym] = str(exc)[:160]
        return {'records': records, 'errors': errors, 'provider': provider.name}

    def screen_options(symbol: str, right: str, policy) -> dict:
        return S.screen_options(symbol, right, min_dte=policy.min_dte, max_dte=policy.max_dte,
                                max_debit_usd=policy.max_debit_usd, min_volume=policy.min_volume,
                                min_open_interest=policy.min_open_interest,
                                max_spread_pct=policy.max_spread_pct, top_n=max(policy.contracts_per_underlying * 3, 6))

    return scan, resolve_universe, screen_options


def load_config(args) -> PipelineConfig:
    raw = json.loads(Path(args.config).read_text(encoding='utf-8')) if args.config else {}
    if args.symbols:
        raw.update(scope='symbols', symbols=[s for s in args.symbols.split(',') if s.strip()])
    if args.modes:
        raw['modes'] = [m.strip() for m in args.modes.split(',') if m.strip()]
    return PipelineConfig.from_dict(raw)


def cmd_run(args, store: SnapshotStore) -> int:
    cfg = load_config(args)
    store._ensure()
    with open(store.dir / '.run.lock', 'w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            log.error('another opportunity run is in progress; skipping')
            return 3
        scan, resolve_universe, screen_options = build_runtime()
        state = store.load_state()
        offset = state.get('universe_offset', 0) if cfg.scope == 'universe' else 0
        snap = run_pipeline(cfg, scan=scan, resolve_universe=resolve_universe, screen_options=screen_options,
                            universe_offset=offset if isinstance(offset, int) else 0)
        path = store.save(snap, keep=cfg.retain_snapshots)
        if cfg.scope == 'universe':
            store.save_state({'universe_offset': snap['universe']['next_universe_offset'] or 0,
                              'updated_at_utc': datetime.now(UTC).isoformat()})
    print(json.dumps({'snapshot_id': snap['snapshot_id'], 'status': snap['status'], 'path': str(path),
                      'summary': snap['summary']}, indent=1))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description='Read-only scheduled opportunity discovery (no order execution)')
    ap.add_argument('--dir', help='snapshot directory (default: TRADEFLIPPER_OPPORTUNITY_DIR or ~/.local/share/...)')
    sub = ap.add_subparsers(dest='cmd', required=True)
    r = sub.add_parser('run')
    r.add_argument('--config')
    r.add_argument('--symbols')
    r.add_argument('--modes')
    sh = sub.add_parser('show')
    sh.add_argument('--id')
    sv = sub.add_parser('serve')
    sv.add_argument('--host', default='127.0.0.1')
    sv.add_argument('--port', type=int, default=8766)
    pr = sub.add_parser('prune')
    pr.add_argument('--keep', type=int, default=200)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    store = SnapshotStore(args.dir)
    if args.cmd == 'run':
        return cmd_run(args, store)
    if args.cmd == 'show':
        print(json.dumps(store.load(args.id) if args.id else to_overview_payload(store.latest()), indent=1))
        return 0
    if args.cmd == 'prune':
        print(json.dumps({'removed': store.prune(args.keep)}))
        return 0
    server = make_server(store, args.host, args.port)
    log.info('serving read-only Overview API on http://%s:%s', *server.server_address[:2])
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == '__main__':
    sys.exit(main())
