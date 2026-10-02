"""
STAT TESTS — independent, registered spread-quality tests.

Registry ``STAT_TESTS`` maps a name to the callable used by the test harness and
(optionally) by screening. Each test is self-contained in its own module:

    hurst         : Hurst exponent of a spread (H < 0.5 mean-reverting)
    half_life     : AR(1)/OU half-life of mean reversion
    variance_ratio: Lo-MacKinlay variance ratio (VR < 1 mean-reverting)
    johansen      : Johansen trace / max-eigenvalue cointegration test
"""
from modules.stattests.hurst import hurst_exponent, rolling_hurst
from modules.stattests.half_life import half_life_ar1, variance_ratio, rolling_half_life
from modules.stattests.johansen import johansen_test, rolling_johansen
from modules.stattests.persistence import mean_crossings, rolling_mean_crossings

# name -> ("univariate" | "pair", callable)
STAT_TESTS = {
    "hurst": ("univariate", hurst_exponent),
    "half_life": ("univariate", half_life_ar1),
    "variance_ratio": ("univariate", variance_ratio),
    "johansen": ("pair", johansen_test),
}

__all__ = [
    "hurst_exponent", "rolling_hurst", "half_life_ar1", "variance_ratio",
    "rolling_half_life", "johansen_test", "rolling_johansen",
    "mean_crossings", "rolling_mean_crossings", "STAT_TESTS",
]
