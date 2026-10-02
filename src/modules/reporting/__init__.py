"""
REPORTING — attribution, tearsheets & experiment output

- AttributionStage: backtests, neutrality audit, MOO blotter
- CommitteeTearsheetEngine: per-pair performance tearsheets
- ReportingStage: console summary, CSV/plot/config/log export
"""

from modules.reporting.attribution import AttributionStage
from modules.reporting.tearsheet import CommitteeTearsheetEngine
from modules.reporting.reporting import ReportingStage

__all__ = ["AttributionStage", "CommitteeTearsheetEngine", "ReportingStage"]