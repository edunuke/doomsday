"""Resolve archived run directories across the results/ housekeeping layout.

Run outputs are grouped by number range:

    results/1-20/<n>     results/21-67/<n>
    results/68            results/69-86/<n>

Use ``run_dir(61)`` / ``run_file(61, "csv", "metrics.csv")`` instead of
hard-coding ``results/61/...`` so scripts keep working when runs are re-banded.
"""
from __future__ import annotations

import re
from pathlib import Path

RESULTS_DIR = Path(__file__).resolve().parents[2] / "results"


def run_dir(run_id) -> Path:
    """Return the directory for a run id (``86``) or an explicit run path."""
    p = Path(str(run_id))
    if p.is_dir():
        return p
    rid = str(run_id)
    direct = RESULTS_DIR / rid
    if direct.is_dir():
        return direct
    if rid.isdigit():
        n = int(rid)
        for band in sorted(RESULTS_DIR.iterdir()):
            m = re.fullmatch(r"(\d+)-(\d+)", band.name)
            if m and band.is_dir() and int(m.group(1)) <= n <= int(m.group(2)):
                cand = band / rid
                if cand.is_dir():
                    return cand
    raise FileNotFoundError(f"run {rid} not found under {RESULTS_DIR}")


def run_file(run_id, *parts) -> str:
    """Return a path inside a run directory as a string."""
    return str(run_dir(run_id).joinpath(*parts))
