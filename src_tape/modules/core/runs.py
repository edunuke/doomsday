"""
RUN DIRECTORY HELPER

Creates the next numbered experiment folder in results/ (relative to the
current working directory) at pipeline start, so logs are appended
incrementally as the run progresses.

Runs may be stored flat (``results/87``) or archived in number bands
(``results/1-20/1``, ``results/69-86/86``). Numbering is global across both
layouts, so a new run never reuses an archived id.

Structure:
    results/{N}/
        config.json
        logs.txt
        csv/
        imgs/
"""

import os
import re

_BAND = re.compile(r"^(\d+)-(\d+)$")


def existing_run_numbers(base_dir: str = "results") -> set:
    """Numbers of runs stored flat or inside ``<lo>-<hi>`` band folders."""
    nums = set()
    if not os.path.isdir(base_dir):
        return nums
    for name in os.listdir(base_dir):
        path = os.path.join(base_dir, name)
        if not os.path.isdir(path):
            continue
        if name.isdigit():
            nums.add(int(name))
            continue
        if _BAND.match(name):
            for sub in os.listdir(path):
                if sub.isdigit() and os.path.isdir(os.path.join(path, sub)):
                    nums.add(int(sub))
    return nums


def get_next_run_dir(base_dir: str = "results") -> str:
    """Create and return the next numbered run directory under *base_dir*."""
    os.makedirs(base_dir, exist_ok=True)
    nums = existing_run_numbers(base_dir)
    run_dir = os.path.join(base_dir, str(max(nums) + 1 if nums else 1))
    os.makedirs(os.path.join(run_dir, "csv"), exist_ok=True)
    os.makedirs(os.path.join(run_dir, "imgs"), exist_ok=True)
    return run_dir
