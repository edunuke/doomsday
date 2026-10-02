"""
Build the LatAm factor panel for signal-side residualization.

Downloads (yfinance, research-grade indicative mid for FX):
  FX      : USDBRL (BRL=X), USDMXN (MXN=X), USDCLP (CLP=X), USDCOP (COP=X),
            USDPEN (PEN=X)
  Metals  : Copper HG=F (COMEX)
  Energy  : WTI CL=F

Output: data/factors_latam.csv  — index = date, columns = log-returns of the
level series (USD per local for FX; USD price for commodities). A POSITIVE
FX log-return therefore means LOCAL CURRENCY DEPRECIATION (USD up), which is
the natural sign for equity-factor regression.

Also writes data/ILF_country_map.csv — analyst-assigned HOME country per
ticker (primary business country, NOT listing venue: VALE lists in the US
but is a Brazilian asset). Names with no dominant single-country exposure
(e.g. TIGO/Millicom, multi-country) get country "" and receive only the
global factors (market/sector/momentum/copper/oil) in the regression.
"""
import os
import sys
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

import yfinance as yf  # noqa: E402

# --- analyst-assigned home country (primary business country) ---------------
COUNTRY_MAP = {
    # Brazil
    "NU": "Brazil", "VALE": "Brazil", "ITUB": "Brazil", "PBR": "Brazil",
    "PBR/A": "Brazil", "B3SA3": "Brazil", "ITSA4": "Brazil", "WEGE3": "Brazil",
    "SBSP3": "Brazil", "ABEV": "Brazil", "BBAS3": "Brazil", "AXIA3": "Brazil",
    "RDOR3": "Brazil", "RENT3": "Brazil", "GGB": "Brazil", "BBD": "Brazil",
    "VIV": "Brazil",
    # Mexico
    "GMEXICOB": "Mexico", "GFNORTEO": "Mexico", "FEMSAUBD": "Mexico",
    "AMXB": "Mexico", "CEMEXCPO": "Mexico", "WALMEX": "Mexico", "AC": "Mexico",
    "BIMBOA": "Mexico", "FUNO11": "Mexico", "GCARSOA1": "Mexico", "PAC": "Mexico",
    # Chile
    "SQM": "Chile", "CHILE": "Chile", "BSAC": "Chile", "CENCOSUD": "Chile",
    "COPEC": "Chile", "FALAB": "Chile",
    # Peru / Colombia
    "BAP": "Peru", "SCCO": "Peru", "ISA": "Colombia", "CIB": "Colombia",
    "EC": "Colombia",
    # multi-country exposure — global factors only
    "LTM": "", "TIGO": "",
}

FX_TICKERS = {"Brazil": "BRL=X", "Mexico": "MXN=X", "Chile": "CLP=X",
              "Colombia": "COP=X", "Peru": "PEN=X"}
COMMODITY_TICKERS = {"copper": "HG=F", "oil": "CL=F"}

START = "2016-08-01"
END = "2026-09-24"


def _close(tkr: str) -> pd.Series:
    px = yf.download(tkr, start=START, end=END, progress=False, auto_adjust=False)
    if isinstance(px.columns, pd.MultiIndex):
        s = px["Close"].iloc[:, 0]
    else:
        s = px["Close"]
    s.index = pd.to_datetime(s.index)
    return s.rename(tkr)


def main():
    levels = {}
    for name, tkr in {**FX_TICKERS, **COMMODITY_TICKERS}.items():
        levels[name] = _close(tkr)
    lv = pd.DataFrame(levels).sort_index()
    print("[factors] levels shape:", lv.shape, "| range:", lv.index.min(), "→", lv.index.max())
    print("[factors] per-series coverage:")
    for c in lv.columns:
        s = lv[c].dropna()
        print(f"  {c:10s} {len(s):5d} obs  {s.index.min().date()} → {s.index.max().date()}")

    # Mask non-positive levels (bad futures ticks) and holiday NaNs → 0 return
    lv = lv.where(lv > 0)
    rets = np.log(lv).diff().fillna(0.0)
    rets.to_csv(os.path.join(HERE, "data", "factors_latam.csv"))

    cm = pd.DataFrame({"Symbol": list(COUNTRY_MAP),
                       "Country": [COUNTRY_MAP[s] for s in COUNTRY_MAP]})
    cm.to_csv(os.path.join(HERE, "data", "ILF_country_map.csv"), index=False)
    print("[factors] wrote data/factors_latam.csv + data/ILF_country_map.csv")


if __name__ == "__main__":
    main()
