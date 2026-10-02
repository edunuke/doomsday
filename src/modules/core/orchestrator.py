"""
PIPELINE ORCHESTRATOR

Iterates an ordered list of PipelineStage classes, instantiating each
with the shared Config and calling stage.run(ctx) in sequence.

Creates results/{N}/ at pipeline START and appends every line of output
to logs.txt as it happens (live). When --verbose is set, the same lines
are mirrored to the terminal immediately.

Usage::
    orchestrator = PipelineOrchestrator(config, flow, verbose=False)
    ctx = orchestrator.run()
"""

import os
import sys
from typing import Any, Dict, List, Type

from modules import PipelineStage
from modules.core.logger import LogCapture
from modules.core.runs import get_next_run_dir


class PipelineOrchestrator:
    """Executes a declarative list of pipeline stages."""

    def __init__(
        self,
        config: "Config",
        flow: List[Type[PipelineStage]],
        verbose: bool = False,
        results_dir: str = "results",
    ):
        self.config = config
        self.verbose = verbose
        self.results_dir = results_dir
        self.flow = [stage(config) for stage in flow]

    def run(self) -> Dict[str, Any]:
        """Run all stages in order, returning the final context.

        The next results/{N}/ folder is created up front and its logs.txt
        is opened for appending. stdout is redirected through a LogCapture
        so every print lands on disk immediately.

        Context keys produced:
          _log_capture : LogCapture — live capture (close + restore at end)
          _output_dir  : str       — the results/{N}/ folder for this run
          _verbose     : bool
        """
        # ---- Create run directory at START --------------------------------
        run_dir = get_next_run_dir(self.results_dir)
        log_path = os.path.join(run_dir, "logs.txt")

        # ---- Redirect stdout to terminal + live log file ------------------
        capture = LogCapture(verbose=self.verbose, log_path=log_path)
        sys.stdout = capture

        ctx: Dict[str, Any] = {}
        ctx["_log_capture"] = capture
        ctx["_output_dir"] = run_dir
        ctx["_verbose"] = self.verbose

        # ---- Configuration header (written to disk immediately) ------------
        print("=" * 85, flush=True)
        print("STAT-ARB v3.2 — RELAXED BETA NEUTRALITY", flush=True)
        print(f"Pipeline flow: {' → '.join(s.__class__.__name__ for s in self.flow)}", flush=True)
        print(f"Verbose      : {'YES' if self.verbose else 'NO'}", flush=True)
        print(f"Results dir  : {os.path.abspath(run_dir)}", flush=True)
        print("=" * 85, flush=True)
        for k in self.config.keys():
            v = self.config[k]
            if isinstance(v, float):
                print(f"  {k:35s} : {v}", flush=True)
            elif isinstance(v, str):
                print(f"  {k:35s} : {v}", flush=True)
            else:
                print(f"  {k:35s} : {v}", flush=True)
        print("=" * 85, flush=True)
        print(f"\n{'=' * 85}", flush=True)
        print("STARTING STAT-ARB ENGINE v3.2", flush=True)
        print(f"{'=' * 85}", flush=True)

        for stage in self.flow:
            name = stage.__class__.__name__
            print(f"\n>>> Stage: {name}", flush=True)
            try:
                stage.run(ctx)
            except Exception:
                # Capture the traceback INTO the run log so failures are never
                # silent (stderr is not teed to logs.txt).
                import sys as _sys
                import traceback as _tb
                print(f"\n!!! Stage {name} FAILED — traceback:", flush=True)
                _tb.print_exc(file=_sys.stdout)
                print(f"<<< Stage: {name} ABORTED.", flush=True)
                raise
            print(f"<<< Stage: {name} complete.", flush=True)

        print("\n[Orchestrator] Pipeline complete.", flush=True)

        # NOTE: sys.stdout is NOT restored here — the ReportingStage
        # closes the log handle and restores stdout at the very end.
        return ctx