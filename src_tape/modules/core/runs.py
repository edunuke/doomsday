"""
RUN DIRECTORY HELPER

Creates the next numbered experiment folder in results/ (relative to the
current working directory) at pipeline start, so logs are appended
incrementally as the run progresses.

Structure:
    results/{N}/
        config.json
        logs.txt
        csv/
        imgs/
"""

import os


def get_next_run_dir(base_dir: str = "results") -> str:
    """Create and return the next numbered run directory under *base_dir*."""
    os.makedirs(base_dir, exist_ok=True)
    existing = [
        int(d) for d in os.listdir(base_dir)
        if os.path.isdir(os.path.join(base_dir, d)) and d.isdigit()
    ]
    run_dir = os.path.join(base_dir, str(max(existing) + 1 if existing else 1))
    os.makedirs(os.path.join(run_dir, "csv"), exist_ok=True)
    os.makedirs(os.path.join(run_dir, "imgs"), exist_ok=True)
    return run_dir