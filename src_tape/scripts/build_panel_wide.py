"""
Build the WIDE LatAm panel (data/LW_daily.csv + LW_volume.csv) for breadth
expansion: the original ILF-41 constituents + curated yfinance LatAm ADRs /
locals. Same market reference (ILF). Only components the pipeline needs.

Sector/country maps are hardcoded (research-grade, from public GICS
classifications) to avoid per-ticker network calls.
"""
import os
import pandas as pd
import yfinance as yf

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
START, END = "2016-08-01", "2026-09-24"

# NEW tickers only (not in the existing ILF-41 panel). Yahoo-style tickers.
NEW = {
    # Brazil (NYSE ADRs / OTC majors)
    "BRFS": ("BRF S.A.", "Consumer Staples", "Brazil"),
    "CIG":  ("Copel", "Utilities", "Brazil"),
    "EBR":  ("Eletrobras", "Utilities", "Brazil"),
    "SID":  ("Gerdau PN ADR", "Materials", "Brazil"),
    "SUZ":  ("Suzano", "Materials", "Brazil"),
    "UGP":  ("Ultrapar", "Energy", "Brazil"),
    "WEGEY": ("WEG ADR", "Industrials", "Brazil"),
    # Mexico
    "ASR":  ("ASUR", "Industrials", "Mexico"),
    "CX":   ("Cemex", "Materials", "Mexico"),
    "FMX":  ("FEMSA", "Consumer Staples", "Mexico"),
    "KOF":  ("Coca-Cola FEMSA", "Consumer Staples", "Mexico"),
    "OMAB": ("OMA", "Industrials", "Mexico"),
    "TX":   ("Ternium", "Materials", "Mexico"),
    "VIST": ("Vista Energy", "Energy", "Mexico"),
    # Chile
    "AKO-A": ("Andina", "Consumer Staples", "Chile"),
    "BCH":   ("Banco de Chile", "Financials", "Chile"),
    "ENIC":  ("Enel Chile", "Utilities", "Chile"),
    "EOC":   ("Enel Gen Chile", "Utilities", "Chile"),   # delisted 2022 — survivorship realism
    # Peru
    "BVN":  ("Buenaventura", "Materials", "Peru"),
    # Argentina
    "ARKO": ("Arcos Dorados", "Consumer Discretionary", "Argentina"),
    "BMA":  ("Banco Macro", "Financials", "Argentina"),
    "CEPU": ("Central Puerto", "Utilities", "Argentina"),
    "DESP": ("Despegar", "Consumer Discretionary", "Argentina"),
    "EDN":  ("Edenor", "Utilities", "Argentina"),
    "GGAL": ("Grupo Galicia", "Financials", "Argentina"),
    "GLOB": ("Globant", "Information Technology", "Argentina"),
    "IRS":  ("IRSA", "Real Estate", "Argentina"),
    "LOMA": ("Loma Negra", "Materials", "Argentina"),
    "MELI": ("MercadoLibre", "Consumer Discretionary", "Argentina"),
    "PAM":  ("Pampa Energia", "Energy", "Argentina"),
    "SUPV": ("Grupo Supervielle", "Financials", "Argentina"),
    "TEO":  ("Telecom Argentina", "Communication Services", "Argentina"),
    "TGS":  ("TGS", "Energy", "Argentina"),
    "VIST2": None,  # placeholder guard (VIST already above)
    "YPF":  ("YPF", "Energy", "Argentina"),
}
NEW.pop("VIST2", None)

# Base panel provides the original 41 + ILF reference.
base_px = pd.read_csv(os.path.join(HERE, "data", "ILF_daily.csv"),
                      index_col=0, parse_dates=True)
base_px = base_px[~base_px.index.duplicated()].sort_index()
base_px = base_px.drop(columns=["ISZ6", "MCBZ6"])   # ILF-specific futures out of the wide pool
base_vol = pd.read_csv(os.path.join(HERE, "data", "ILF_volume.csv"),
                       index_col=0, parse_dates=True).reindex(base_px.index)

tickers = list(NEW.keys())
print(f"downloading {len(tickers)} new tickers...")
raw = yf.download(tickers, start=START, end=END, progress=False, auto_adjust=False)
close = raw["Close"]
vol = raw["Volume"]
got, failed = [], []
for t in tickers:
    s = close[t] if t in close.columns else None
    if s is None or s.dropna().shape[0] < 60:
        failed.append(t)
        continue
    got.append(t)
print(f"downloaded OK: {len(got)} | failed/empty: {failed}")

new_close = close[got].reindex(base_px.index)
new_vol = vol[got].reindex(base_px.index)
# rename Yahoo quirks to panel names
new_close = new_close.rename(columns={"AKO-A": "AKO-A"})

panel = pd.concat([base_px, new_close], axis=1)
panel_vol = pd.concat([base_vol, new_vol], axis=1)
panel = panel[~panel.index.duplicated()].sort_index()
panel = panel.dropna(axis=1, how="all")

# coverage report
cov = panel.notna().mean().sort_values()
print("\ncoverage (fraction of days):")
print(cov.round(2).to_string())
print("\nfull-coverage names:", int((cov >= 0.999).sum()),
      "| >=60%:", int((cov >= 0.60).sum()))

panel.to_csv(os.path.join(HERE, "data", "LW_daily.csv"))
panel_vol.to_csv(os.path.join(HERE, "data", "LW_volume.csv"))

# constituents + country map (original 41 sectors from the existing file + new)
con0 = pd.read_csv(os.path.join(HERE, "data", "ILF_constituents_gics.csv"))
rows = con0[["Symbol", "GICS Sector"]].to_dict("records")
cmap0 = pd.read_csv(os.path.join(HERE, "data", "ILF_country_map.csv"))
cmap_rows = cmap0.to_dict("records")
for t in got:
    name, sector, country = NEW[t]
    if t in set(r["Symbol"] for r in rows):
        continue
    rows.append({"Symbol": t, "GICS Sector": sector})
    cmap_rows.append({"Symbol": t, "Country": country})
pd.DataFrame(rows).to_csv(os.path.join(HERE, "data", "LW_constituents_gics.csv"), index=False)
pd.DataFrame(cmap_rows).to_csv(os.path.join(HERE, "data", "LW_country_map.csv"), index=False)
print(f"\nwrote LW_daily.csv {panel.shape}, LW_volume.csv, "
      f"LW_constituents_gics.csv ({len(rows)} names), LW_country_map.csv")
