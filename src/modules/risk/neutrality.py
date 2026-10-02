"""
MARKET NEUTRALITY DIAGNOSTIC ENGINE

CAPM regression-based check of residual market exposure.
Evaluates beta, R², bull/bear betas, and t-statistics against
configurable thresholds.

v3.2: 5 % beta cap (relaxed) vs 1 % in 3.1.
"""

from typing import Tuple

import numpy as np
import pandas as pd
import statsmodels.api as sm


class MarketNeutralityDiagnosticEngine:
    """Tests whether strategy returns satisfy market-neutrality constraints."""

    def __init__(
        self,
        max_beta: float = 0.0500,
        max_r2: float = 0.005,
        min_regime_obs: int = 10,
    ):
        self.max_beta = max_beta
        self.max_r2 = max_r2
        self.min_regime_obs = min_regime_obs

    def analyze(
        self,
        strategy_returns: pd.Series,
        benchmark_returns: pd.Series,
    ) -> Tuple[pd.DataFrame, dict]:
        """Run CAPM OLS and return metrics DataFrame + verdict dict."""
        aligned = pd.concat([strategy_returns, benchmark_returns], axis=1).dropna()
        aligned.columns = ["Strategy", "Benchmark"]
        r_p = aligned["Strategy"]
        r_m = aligned["Benchmark"]

        capm = sm.OLS(r_p, sm.add_constant(r_m)).fit()
        alpha_ann = capm.params.get("const", 0.0) * 252.0
        beta_m = capm.params.get("Benchmark", 0.0)
        p_val_beta = capm.pvalues.get("Benchmark", 1.0)
        t_stat_beta = capm.tvalues.get("Benchmark", 0.0)
        r2 = capm.rsquared

        bull = r_m > 0
        bear = r_m < 0

        beta_bull = (
            sm.OLS(r_p[bull], sm.add_constant(r_m[bull])).fit().params.get("Benchmark", 0.0)
            if bull.sum() > self.min_regime_obs
            else 0.0
        )
        beta_bear = (
            sm.OLS(r_p[bear], sm.add_constant(r_m[bear])).fit().params.get("Benchmark", 0.0)
            if bear.sum() > self.min_regime_obs
            else 0.0
        )

        def _status(val, threshold, less_than=True):
            if less_than:
                return "PASS" if abs(val) <= threshold else "FAIL"
            return "PASS" if val >= threshold else "FAIL"

        def _status_t(val):
            return "PASS" if abs(val) < 1.96 else "FAIL"

        rows = [
            ("Annualized Alpha", f"{alpha_ann:.2%}", "N/A", "INFO"),
            ("Market Beta (β_m)", f"{beta_m:.4f}", f"< |{self.max_beta}|", _status(beta_m, self.max_beta)),
            ("Beta p-value", f"{p_val_beta:.4f}", ">= 0.05", _status(p_val_beta, 0.05, less_than=False)),
            ("Beta t-statistic", f"{t_stat_beta:.4f}", "< |1.96|", _status_t(t_stat_beta)),
            ("Variance Explained (R²)", f"{r2:.2%}", f"< {self.max_r2:.1%}", _status(r2, self.max_r2)),
            ("Bull Market Beta (R_m > 0)", f"{beta_bull:.4f}", f"< |{self.max_beta}|", _status(beta_bull, self.max_beta)),
            ("Bear Market Beta (R_m < 0)", f"{beta_bear:.4f}", f"< |{self.max_beta}|", _status(beta_bear, self.max_beta)),
        ]

        metrics_df = pd.DataFrame(
            rows, columns=["Metric", "Value", "Threshold", "Status"]
        )

        failures = metrics_df[metrics_df["Status"] == "FAIL"]["Metric"].tolist()
        is_neutral = len(failures) == 0
        verdict = {
            "Market_Neutral": "TRUE" if is_neutral else "FALSE",
            "Core_Failure_Reason": "None" if is_neutral else ", ".join(failures),
        }

        return metrics_df, verdict