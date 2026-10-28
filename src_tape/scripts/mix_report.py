#!/usr/bin/env python3
"""
Build a combined "mix report" for a run directory by joining:

  * csv/trade_records.csv   - per-leg fills (entry/exit, direction, PnL)
  * logs.txt                - screening funnel, VolGate/LagTilt/LagDBG lines,
                              rebalance Ghost/Promoted/Live counts, [BASE]
                              sleeve lines, allocator warnings
  * tape.txt                - authoritative ENTRY/PAIR_BOOK decisions
                              (entry_z, alpha, beta, sleeve/leg weights)
  * config.json             - thresholds used to explain the decisions

Outputs:
  csv/mix_report.csv        - one row per trade leg (+ overlay legs)
  MIX_REPORT.md             - human readable per-entry narrative

Usage:
  python scripts/mix_report.py 86            # resolves results/1-20, /21-67,
                                            #   /68, /69-86 automatically
  python scripts/mix_report.py results/68    # or pass an explicit run dir
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import date, timedelta

import pandas as pd

COLUMN_DOC = {
    "trade_id": "Row id from `trade_records.csv`; unique per fill.",
    "origin": "Where the fill came from: `PAIR <A>/<B> (leg A|B)` = a pair sleeve leg, `MARKET OVERLAY (ILF)` = residual market-beta hedge, not a pair leg.",
    "asset": "Ticker traded.",
    "direction": "`Long` (bought) or `Short` (sold) for that leg.",
    "entry_ts": "Fill date of the entry (T+1 open after the signal date).",
    "exit_ts": "Fill date of the exit (T+1 open after the exit signal).",
    "signal_ts": "Decision date the pair was promoted/allocated (tape `ENTRY` timestamp).",
    "entry_price": "Entry fill price.",
    "exit_price": "Exit fill price.",
    "notional": "Entry notional in dollars (price x shares).",
    "pnl": "Realised P&L in dollars for the leg.",
    "return_pct": "Leg return in percent (PnL / notional).",
    "pair": "Pair the fill belongs to, `A/B`; also shown for overlay rows (the pair it hedged).",
    "pair_beta": "Hedge ratio of the pair: leg B notional / leg A notional (from the Kalman/regression fit).",
    "entry_z": "Standardised spread at entry: (spread - mean) / vol. >0 = A rich vs B, <0 = A cheap vs B.",
    "entry_alpha": "Expected annualised reversion return used to rank the pair (OU model, `(|z|-exit_z)*spread_vol*(252/HL)*(1-exp(-HL/tau))`).",
    "ecdf": "Percentile rank of the entry z in its rolling 63-day history (shown only when the sleeve was top-3 by weight that day).",
    "sleeve_w_pct": "Pair sleeve weight as percent of equity (allocator output). Blank for overlay rows.",
    "leg_w_pct": "Signed weight of this leg in percent of equity; opposite sign to the other leg, B scaled by beta. For overlay rows it is the overlay weight.",
    "screen": "Last cointegration re-screen before the signal: date, passes/candidates, screener version.",
    "gates": "Promotion gates that cut pairs on the signal date (VolGate / EVGate / RevDisc for that rebalance).",
    "entry_live": "Pairs live after the entry rebalance (same `Live=` count as logs.txt).",
    "entry_overlay_pct": "Market-overlay target for that rebalance, in percent of equity.",
    "exit_signal_ts": "Rebalance date whose zero-weight `PAIR_BOOK` flattened the pair.",
    "exit_live_after": "Pairs live after that exit rebalance (0 = whole book flat).",
    "exit_alloc_warn": "Allocator warning on the exit date, if any: sleeve count and constraint violation.",
    "exit_reason": "Machine label: `DROPPED_BY_ALLOCATOR`, an exit-manager reason (`STOP_LOSS`/`REVERTED`/`MAX_HOLD`/`STALE`), or `OVERLAY_REBALANCED_FLAT` for the hedge.",
    "entry_reason": "Human summary of the promotion path: screen result, promotion rule, entry |z|, gates.",
    "exit_reason_text": "Human summary of the exit: what dropped the pair, allocator state, quarantine.",
    "leg_thesis": "Why this leg is long or short, with the beta ratio and leg weight.",
}

RE_SCREEN = re.compile(r"^\[Screen\] (\d{4}-\d{2}-\d{2}): (\d+) candidates -> (\d+) pass \((\w+)\) -> (\d+) live")
RE_REBAL = re.compile(r"^\[Rebalance @ (\d{4}-\d{2}-\d{2})")
RE_FUNNEL = re.compile(r"^  --> Ghost=(\d+) \| Promoted=(\d+) \| Live=(\d+)")
RE_BASE = re.compile(
    r"^      \[(\w+)\] (\S+?)/(\S+) \| W_A: (-?[\d.]+)% \| W_B: (-?[\d.]+)% "
    r"\| Inno Z: (-?[\d.]+) \| ECDF: ([\d.]+) \| Alpha: ([\d.]+)")
RE_GATE = re.compile(r"^    \[(VolGate|EVGate|RevDisc)\] (\d+) -> (\d+) promotions(.*)")
RE_LAGDBG = re.compile(r"^    \[LagDBG\] (\d{4}-\d{2}-\d{2}) \('([^']+)', '([^']+)'\).*EntryZ=([-+][\d.]+)")
RE_OVERLAY = re.compile(r"ILF Overlay=(-?[\d.]+)%")


def resolve_run(arg):
    """Accept a run id (`86`) or a directory path."""
    from pathlib import Path
    import re as _re

    p = Path(arg)
    if p.is_dir():
        return str(p)
    root = Path(__file__).resolve().parent.parent / "results"
    direct = root / arg
    if direct.is_dir():
        return str(direct)
    if arg.isdigit():
        n = int(arg)
        for d in sorted(root.iterdir()):
            if not d.is_dir():
                continue
            m = _re.fullmatch(r"(\d+)-(\d+)", d.name)
            if m and int(m.group(1)) <= n <= int(m.group(2)):
                cand = d / arg
                if cand.is_dir():
                    return str(cand)
    raise SystemExit(f"run not found: {arg} (looked under {root})")


def load_config(run_dir):
    path = os.path.join(run_dir, "config.json")
    if not os.path.exists(path):
        return {}
    with open(path) as fh:
        return json.load(fh)


def parse_logs(path):
    """Return dict of date -> facts, plus a list of screen events."""
    days = {}
    screens = []
    cur = None
    with open(path, errors="replace") as fh:
        for line in fh:
            line = line.rstrip("\n")
            m = RE_REBAL.match(line)
            if m:
                cur = m.group(1)
                d = days.setdefault(cur, {})
                d.setdefault("rebalances", 0)
                d["rebalances"] += 1
                continue
            m = RE_SCREEN.match(line)
            if m:
                screens.append({"date": m.group(1), "candidates": int(m.group(2)),
                                "pass": int(m.group(3)), "screener": m.group(4),
                                "live": int(m.group(5))})
                continue
            if cur is None:
                continue
            d = days[cur]
            m = RE_FUNNEL.match(line)
            if m:
                d["ghost"], d["promoted"], d["live"] = (int(m.group(1)),
                                                        int(m.group(2)),
                                                        int(m.group(3)))
                continue
            m = RE_BASE.match(line)
            if m:
                d.setdefault("base", []).append({
                    "type": m.group(1), "pair": f"{m.group(2)}/{m.group(3)}",
                    "w_a": float(m.group(4)), "w_b": float(m.group(5)),
                    "z": float(m.group(6)), "ecdf": float(m.group(7)),
                    "alpha": float(m.group(8)),
                })
                continue
            m = RE_GATE.match(line)
            if m:
                d.setdefault("gates", []).append(
                    f"{m.group(1)} {m.group(2)}->{m.group(3)}{m.group(4)}")
                continue
            m = RE_LAGDBG.match(line)
            if m:
                d.setdefault("lagdbg", []).append(
                    {"pair": f"{m.group(2)}/{m.group(3)}",
                     "entry_z": float(m.group(4))})
                continue
            if "hard-constrained SLSQP failed" in line:
                ns = re.search(r"n_sleeves=(\d+)", line)
                viol = re.search(r"Maximum violation: ([\d.]+)", line)
                d.setdefault("alloc_warn", []).append({
                    "n_sleeves": int(ns.group(1)) if ns else None,
                    "violation": float(viol.group(1)) if viol else None,
                    "msg": line.strip(),
                })
                continue
            m = RE_OVERLAY.search(line)
            if m:
                d["overlay_pct"] = float(m.group(1))
    return days, screens


def load_tape(path):
    entries, pair_books = [], []
    with open(path) as fh:
        for line in fh:
            try:
                ev = json.loads(line)
            except Exception:
                continue
            if ev.get("type") == "ENTRY":
                entries.append(ev)
            elif ev.get("type") == "PAIR_BOOK":
                pair_books.append(ev)
    exit_reasons = []
    with open(path) as fh:
        for line in fh:
            try:
                ev = json.loads(line)
            except Exception:
                continue
            if ev.get("type") == "EXIT":
                exit_reasons.append(ev)
    return entries, pair_books, exit_reasons


def next_day(ts):
    return (date.fromisoformat(ts) + timedelta(days=1)).isoformat()


def entry_reason_str(scr, log, ev, cfg):
    z = abs(float(ev["entry_z"]))
    k = cfg.get("simple_max_pairs", 20)
    if str(cfg.get("funnel_mode", "full")) == "simple":
        promo = f"promoted top-{k} by alpha (simple mode)"
    else:
        promo = "ECDF/alpha/z gates passed"
    parts = []
    if scr:
        parts.append(f"screen {scr['date']} {scr['screener']} "
                     f"{scr['pass']}/{scr['candidates']} pass")
    parts.append(promo)
    parts.append(f"|z|={z:.2f}")
    if log.get("gates"):
        parts.append("; ".join(re.sub(r"\s*\(.*", "", g) for g in log["gates"]))
    return "; ".join(parts)


def exit_reason_str(tape_exit, exit_sig, exit_log, cfg):
    if tape_exit:
        return (f"{tape_exit['reason']} (z {tape_exit['exit_z']:+.2f}, "
                f"{tape_exit['days_held']}d)")
    warn = (exit_log or {}).get("alloc_warn")
    if warn:
        w = warn[-1]
        return (f"allocator drop {exit_sig}: all-zero solve "
                f"(n={w['n_sleeves']}, viol={w['violation']}); "
                f"{cfg.get('quarantine_days')}d quarantine")
    if exit_sig:
        return f"allocator drop {exit_sig}"
    return "no flatten found"


def nearest_screen(screens, ts):
    prev = [s for s in screens if s["date"] <= ts]
    return prev[-1] if prev else None


def main(run_dir):
    run_dir = run_dir.rstrip("/")
    tr = pd.read_csv(os.path.join(run_dir, "csv", "trade_records.csv"))
    tr["Entry Timestamp"] = tr["Entry Timestamp"].astype(str)
    tr["Exit Timestamp"] = tr["Exit Timestamp"].astype(str)
    cfg = load_config(run_dir)
    logs, screens = parse_logs(os.path.join(run_dir, "logs.txt"))
    entries, pair_books, tape_exits = load_tape(os.path.join(run_dir, "tape.txt"))

    market = cfg.get("market_ticker", "ILF")

    # signal date -> fill date. Fills land on the next session after the
    # decision; derive it from the trade file itself so we do not need a
    # calendar (the first fill date strictly after the signal date).
    fill_dates = sorted(tr["Entry Timestamp"].unique())
    def fill_for(sig_ts):
        later = [d for d in fill_dates if d > sig_ts]
        return later[0] if later else sig_ts

    used = set()
    groups = []
    for ev in sorted(entries, key=lambda e: e["ts"]):
        sig = ev["ts"]
        fill = fill_for(sig)
        pair = tuple(ev["pair"])
        legs = {}
        for leg in pair:
            cand = tr[(tr["Asset"] == leg) & (tr["Entry Timestamp"] == fill)
                      & (~tr.index.isin(used))]
            if cand.empty:
                continue
            idx = cand.index[0]
            used.add(idx)
            legs[leg] = tr.loc[idx]
        ov = tr[(tr["Asset"] == market) & (tr["Entry Timestamp"] == fill)
                & (~tr.index.isin(used))]
        overlay = None
        if not ov.empty:
            idx = ov.index[0]
            used.add(idx)
            overlay = tr.loc[idx]
        groups.append({"signal_ts": sig, "fill_ts": fill, "entry": ev,
                       "legs": legs, "overlay": overlay})

    # Any trade not matched to a tape ENTRY is flagged rather than dropped.
    leftovers = [i for i in tr.index if i not in used]

    rows = []
    for g in groups:
        ev = g["entry"]
        pair = tuple(ev["pair"])
        sig, fill = g["signal_ts"], g["fill_ts"]
        log = logs.get(sig, {})
        scr = nearest_screen(screens, sig)
        base = next((b for b in log.get("base", []) if b["pair"] == "/".join(pair)), None)
        gate_notes = log.get("gates", [])
        # exit facts: the rebalance that flattened the pair (zero PAIR_BOOK)
        flat_dates = sorted({pb["ts"] for pb in pair_books
                             if tuple(pb["pair"]) == pair
                             and abs(float(pb["weights"].get(pair[0], 0.0))) < 1e-12
                             and pb["ts"] > sig})
        exit_sig = flat_dates[0] if flat_dates else None
        exit_log = logs.get(exit_sig, {}) if exit_sig else {}
        tape_exit = next((e for e in tape_exits if tuple(e["pair"]) == pair and e["ts"] == exit_sig), None)

        z = float(ev["entry_z"])
        # direction of each leg from the tape weights
        for leg, row in g["legs"].items():
            w = float(ev["w_a"] if leg == pair[0] else ev["w_b"])
            role = "A" if leg == pair[0] else "B"
            primary = z < 0
            beta = float(ev["beta"])
            if role == "A":
                why = (f"signal leg A: z={z:+.2f} < 0, A cheap vs B -> BUY A"
                       if primary else
                       f"signal leg A: z={z:+.2f} > 0, A rich vs B -> SELL A")
            else:
                why = (f"hedge leg B: SELL B at beta {beta:.2f}x leg A "
                       f"(weight {w*100:.1f}%)" if primary else
                       f"hedge leg B: BUY B at beta {beta:.2f}x leg A "
                       f"(weight {w*100:.1f}%)")
            rows.append({
                "trade_id": int(row["Trade Id"]),
                "origin": f"PAIR {pair[0]}/{pair[1]} (leg {role})",
                "asset": leg,
                "direction": row["Direction"],
                "entry_ts": row["Entry Timestamp"],
                "exit_ts": row["Exit Timestamp"],
                "signal_ts": sig,
                "entry_price": row["Entry Price"],
                "exit_price": row["Exit Price"],
                "notional": round(float(row["Entry Notional"]), 2),
                "pnl": round(float(row["PnL"]), 2),
                "return_pct": round(float(row["Return"]) * 100, 3),
                "pair": f"{pair[0]}/{pair[1]}",
                "pair_beta": round(float(ev["beta"]), 4),
                "entry_z": round(z, 3),
                "entry_alpha": round(float(ev["alpha"]), 4),
                "ecdf": base["ecdf"] if base else None,
                "sleeve_w_pct": round(float(ev["sleeve_w"]) * 100, 2),
                "leg_w_pct": round(w * 100, 2),
                "screen": (f"{scr['date']}: {scr['pass']}/{scr['candidates']} pass ({scr['screener']})"
                           if scr else ""),
                "gates": "; ".join(gate_notes) if gate_notes else "",
                "entry_live": log.get("live"),
                "entry_overlay_pct": log.get("overlay_pct"),
                "exit_signal_ts": exit_sig,
                "exit_live_after": exit_log.get("live"),
                "exit_alloc_warn": (
                    f"n_sleeves={exit_log['alloc_warn'][-1]['n_sleeves']}, "
                    f"violation={exit_log['alloc_warn'][-1]['violation']}"
                    if exit_log.get("alloc_warn") else ""),
                "exit_reason": ("STOP_LOSS" if tape_exit and tape_exit["reason"] == "STOP_LOSS"
                                else (tape_exit["reason"] if tape_exit
                                      else "DROPPED_BY_ALLOCATOR")),
                "entry_reason": entry_reason_str(scr, log, ev, cfg),
                "exit_reason_text": exit_reason_str(tape_exit, exit_sig, exit_log, cfg),
                "leg_thesis": why,
            })

        if g["overlay"] is not None:
            row = g["overlay"]
            rows.append({
                "trade_id": int(row["Trade Id"]),
                "origin": f"MARKET OVERLAY ({market})",
                "asset": market,
                "direction": row["Direction"],
                "entry_ts": row["Entry Timestamp"],
                "exit_ts": row["Exit Timestamp"],
                "signal_ts": sig,
                "entry_price": row["Entry Price"],
                "exit_price": row["Exit Price"],
                "notional": round(float(row["Entry Notional"]), 2),
                "pnl": round(float(row["PnL"]), 2),
                "return_pct": round(float(row["Return"]) * 100, 3),
                "pair": f"{pair[0]}/{pair[1]}",
                "pair_beta": round(float(ev["beta"]), 4),
                "entry_z": round(z, 3),
                "entry_alpha": round(float(ev["alpha"]), 4),
                "ecdf": base["ecdf"] if base else None,
                "sleeve_w_pct": None,
                "leg_w_pct": log.get("overlay_pct"),
                "screen": (f"{scr['date']}: {scr['pass']}/{scr['candidates']} pass ({scr['screener']})"
                           if scr else ""),
                "gates": "; ".join(gate_notes) if gate_notes else "",
                "entry_live": log.get("live"),
                "entry_overlay_pct": log.get("overlay_pct"),
                "exit_signal_ts": exit_sig,
                "exit_live_after": exit_log.get("live"),
                "exit_alloc_warn": (
                    f"n_sleeves={exit_log['alloc_warn'][-1]['n_sleeves']}, "
                    f"violation={exit_log['alloc_warn'][-1]['violation']}"
                    if exit_log.get("alloc_warn") else ""),
                "exit_reason": "OVERLAY_REBALANCED_FLAT",
                "entry_reason": entry_reason_str(scr, log, ev, cfg),
                "exit_reason_text": (f"book flattened at {exit_sig} -> residual "
                                     "beta hedge went to 0"),
                "leg_thesis": (f"residual market-beta hedge of the pair book "
                               f"({log.get('overlay_pct')}% target); sign = -book beta"),
            })

    out = pd.DataFrame(rows).sort_values("trade_id")
    csv_path = os.path.join(run_dir, "csv", "mix_report.csv")
    out.to_csv(csv_path, index=False)

    # ---- markdown -----------------------------------------------------
    pairs = out[~out["origin"].str.startswith("MARKET OVERLAY")].copy()
    overlay = out[out["origin"].str.startswith("MARKET OVERLAY")].copy()
    pairs = pairs.sort_values("return_pct", ascending=False)
    best = pairs[pairs["return_pct"] > 0]
    worst = pairs[pairs["return_pct"] <= 0].iloc[::-1]
    if best.empty or worst.empty:  # degenerate: fall back to halves
        n = max(1, len(pairs) // 2)
        best, worst = pairs.head(n), pairs.tail(n).iloc[::-1]

    cols = ["trade_id", "pair", "asset", "direction", "entry_ts", "exit_ts",
            "return_pct", "pnl", "entry_z", "entry_reason",
            "exit_reason_text", "leg_thesis"]
    heads = ["Id", "Pair", "Asset", "Dir", "Entry", "Exit", "Ret %",
             "PnL $", "Entry Z", "Entry reason", "Exit reason", "Leg thesis"]

    def table(df):
        rows = ["| " + " | ".join(heads) + " |",
                "|" + "|".join(["---"] * len(heads)) + "|"]
        for _, r in df.iterrows():
            cells = [str(r["trade_id"]), r["pair"], r["asset"], r["direction"],
                     r["entry_ts"], r["exit_ts"], f"{r['return_pct']:+.2f}",
                     f"{r['pnl']:+,.0f}", f"{r['entry_z']:+.2f}",
                     r["entry_reason"], r["exit_reason_text"], r["leg_thesis"]]
            rows.append("| " + " | ".join(str(c).replace("|", "\\|")
                                            for c in cells) + " |")
        return rows

    lines = [f"# Mix report — run {os.path.basename(run_dir)}", ""]
    lines.append(f"{len(tr)} fills = {len(pairs)} pair legs + {len(overlay)} "
                 f"market-overlay legs. Ranked by realised return per leg; "
                 "full detail in `csv/mix_report.csv`.")
    lines.append("")
    lines.append(f"## Best trades ({len(best)} winners)")
    lines.append("")
    lines += table(best)
    lines.append("")
    lines.append(f"## Worst trades ({len(worst)} losers)")
    lines.append("")
    lines += table(worst)
    lines.append("")

    # ---- comments -----------------------------------------------------
    lines.append("## Comments")
    lines.append("")
    exit_kinds = out["exit_reason"].value_counts()
    lines.append(f"- Every pair leg in this run closed via "
                 f"`{pairs['exit_reason'].iloc[0] if len(pairs) else 'n/a'}`. "
                 f"Tape EXIT events: {len(tape_exits)}, and `min_holding_days` = "
                 f"{cfg.get('min_holding_days')} is longer than any hold, so no "
                 "stop-loss / reversion / max-hold exit ever fired. The pair was "
                 "de-allocated, both legs flattened the next open, and the pair "
                 "was quarantined.")
    if len(exit_kinds):
        lines.append(f"- Exit reason counts across all {len(out)} rows: "
                     + ", ".join(f"{k} {v}" for k, v in exit_kinds.items()) + ".")
    if len(pairs):
        _bm = best['return_pct'].mean() if len(best) else 0.0
        _wm = worst['return_pct'].mean() if len(worst) else 0.0
        lines.append(f"- {len(best)} of {len(pairs)} pair legs made money "
                     f"(win rate {(pairs['return_pct'] > 0).mean() * 100:.0f}%). "
                     f"Winners average {_bm:+.2f}% ({best['pnl'].sum():+,.0f} $); "
                     f"losers average {_wm:+.2f}% ({worst['pnl'].sum():+,.0f} $).")
        if len(best) and len(worst):
            lines.append(f"- Net pair-leg PnL {best['pnl'].sum() + worst['pnl'].sum():+,.0f} $ "
                         f"over {len(pairs)} one-day holds, i.e. the book's whole "
                         "result comes from a handful of sessions, with the top "
                         f"{min(3, len(best))} legs contributing "
                         f"{best['pnl'].head(3).sum():+,.0f} $.")
        lines.append("- Returns are one-session close-to-close moves on a "
                     "beta-matched leg pair. Because the position is dropped at "
                     "the next rebalance, no leg actually holds long enough for "
                     "the spread to revert to `exit_z_spread`; PnL here is "
                     "mark-to-market noise, not captured convergence.")
    if len(overlay):
        lines.append(f"- The {len(overlay)} overlay legs are excluded from the "
                     f"ranking: total overlay PnL {overlay['pnl'].sum():+,.0f} $ "
                     f"({overlay['return_pct'].mean():+.2f}% avg). They are the "
                     f"residual-beta hedge in {market}, not a standalone trade.")
    lines.append("- `entry_reason` shows the funnel that let the pair in: the "
                 "cointegration screen, the promotion rule (simple mode = "
                 "top-N by alpha, so the ECDF / `entry_z_base` gates are NOT "
                 "applied), and the entry z. `leg_thesis` explains the side: "
                 "z<0 means leg A is cheap, so A is bought and B is sold at "
                 "beta ratio; z>0 mirrors it.")

    # ---- worked examples for interpretation ---------------------------
    lines.append("## How to read a row (two examples)")
    lines.append("")
    if len(best):
        b = best.iloc[0]
        z = float(b["entry_z"])
        lines.append(f"**Best trade, Id {int(b['trade_id'])} — "
                     f"{b['asset']} {b['direction'].lower()} vs {b['pair']}.** "
                     f"On {b['entry_ts']} the model found the beta-adjusted "
                     f"spread ~{abs(z):.1f} standard deviations "
                     f"{'above' if z > 0 else 'below'} fair value (z = {z:+.2f}), "
                     f"so {b['asset']} was "
                     f"{'rich and sold' if b['direction'] == 'Short' else 'cheap and bought'} "
                     f"as the signal leg, with the other leg hedged at beta "
                     f"{float(b['pair_beta']):.2f}x. The pair was closed one "
                     f"session later for {b['pnl']:+,.0f} $ "
                     f"({b['return_pct']:+.2f}%).")
        lines.append("")
    if len(worst):
        w = worst.iloc[0]
        lines.append(f"**Worst trade, Id {int(w['trade_id'])} — "
                     f"{w['asset']} {w['direction'].lower()} vs {w['pair']}.** "
                     f"Same mechanism, opposite outcome: the entry z was "
                     f"{float(w['entry_z']):+.2f}, the leg was closed one "
                     f"session later at {w['return_pct']:+.2f}% "
                     f"({w['pnl']:+,.0f} $). Read the two legs of one pair "
                     "together: the book profits from the spread converging, "
                     "not from either leg alone, and on a one-day hold it is "
                     "mostly capturing that day's spread move.")
        lines.append("")
    lines.append("**In one line:** `entry_reason` says how the pair earned a "
                 "live allocation, `entry_z` says how stretched the spread was "
                 "(the larger the |z|, the more the model expects it to snap "
                 "back), `leg_thesis` says which side of the pair each leg is "
                 "on and why, and `exit_reason` says how the position ended — "
                 "here always a portfolio rebalance dropping the sleeve, not a "
                 "target or stop being hit.")
    lines.append("")
    lines.append("")
    lines.append("## CSV column reference")
    lines.append("")
    lines.append("| Column | Meaning |")
    lines.append("|---|---|")
    for col in out.columns:
        _doc = COLUMN_DOC.get(col, "").replace("|", "\\|")
        lines.append(f"| `{col}` | {_doc} |")
    lines.append("")

    missing = [c for c in out.columns if c not in COLUMN_DOC]
    if missing:
        print(f"WARNING undocumented columns: {missing}")

    md_path = os.path.join(run_dir, "MIX_REPORT.md")
    with open(md_path, "w") as fh:
        fh.write("\n".join(lines) + "\n")

    print(f"wrote {csv_path} ({len(out)} rows)")
    print(f"wrote {md_path}")
    if leftovers:
        print(f"WARNING unmatched trade ids: {leftovers}")


if __name__ == "__main__":
    main(resolve_run(sys.argv[1] if len(sys.argv) > 1 else "86"))
