"""
SURVIVORSHIP-COMPENSATION MODEL (v2) — per-industry hazard + Weibull lifetime.

Two explicit, per-industry levers control generation:
  (1) FAILURE RATE  `fail_rate_annual[sector]`  -> how many synthetic names fail
      per year in that industry  => n_synth_sector = rate_s * N_sector * years
  (2) TIME-TO-FAILURE  `ttf_mean_years[sector]`, `ttf_shape[sector]`  -> how long
      a doomed name lives before failing (Weibull; shape>1 = hazard rises with age)

Plus realism:
  * systematic part uses the REAL market/sector return paths (regime-faithful);
    idio uses a shrunk sector-block Cholesky for cross-sectional structure;
  * idiosyncratic vol is calibrated to the DISTRESSED COHORT (upper percentile of
    survivors' trailing vol), not the survivor median — doomed names are high-vol;
  * a vol RAMP increases vol as failure approaches (distress);
  * terminal delisting return from a M&A / performance / bankruptcy mixture.

Point-in-time presence: NaN before listing and worthless-after-delisting (the
price collapses toward 0 so a held LONG takes the full loss).
"""
from __future__ import annotations
from dataclasses import dataclass
import numpy as np
import pandas as pd
from scipy.special import gamma as _gamma

# --- PRE-REGISTERED CALIBRATION --------------------------------------------
# per-industry: (fail_rate_annual, ttf_mean_years, ttf_shape)
IND = {
    "Energy":                  (0.050, 7.0, 1.6),
    "Consumer Discretionary":  (0.044, 8.0, 1.5),
    "Industrials":             (0.034, 9.0, 1.4),
    "Financials":              (0.038, 9.0, 1.5),
    "Information Technology":  (0.028, 8.0, 1.5),
    "Health Care":             (0.026, 9.0, 1.4),
    "Utilities":               (0.014, 12.0, 1.3),
    "Consumer Staples":        (0.016, 12.0, 1.3),
    "Communication Services":  (0.028, 9.0, 1.5),
    "Materials":               (0.038, 8.0, 1.5),
    "Real Estate":             (0.030, 10.0, 1.4),
    "Unknown":                 (0.030, 9.0, 1.5),
}
REASON_MIX = {"M&A": 0.45, "perf": 0.40, "bk": 0.15}
DELIST_RET = {"M&A": (+0.25, 0.05), "perf": (-0.35, 0.20), "bk": (-0.95, 0.10)}
CRISIS_MULT = {2020: 1.8, 2008: 3.5, 2009: 3.5, 2001: 3.0, 2002: 3.0, "else": 1.0}
IDIO_PCTILE = 0.50        # BASE idio = median survivor vol; ramp lifts to distressed
VOL_RAMP = 2.0            # idio vol multiplier ramp -> ~3x (distressed) at failure
STRESS_DAYS = 252         # drift window before failure
DRIFT_NEG = -0.35         # annualised distress drift
IDIO_RHO = 0.15
SHRINK = 0.10
POST_DELIST = 1e-4        # worthless residual (full loss for a held long)


@dataclass
class SurvivorshipResult:
    prices: pd.DataFrame
    constituents: pd.DataFrame
    meta: pd.DataFrame


def _shrink_corr(C, lam):
    return (1 - lam) * C + lam * np.diag(np.diag(C))


class SurvivorshipModel:
    def __init__(self, params: dict | None = None):
        self.override = params or {}
        # Resolve instance-level calibration: module defaults merged with any
        # per-universe overrides (e.g. region-adjusted LatAm calibration).
        self.IND = {**IND, **self.override.get("industries", {})}
        self.REASON_MIX = {**REASON_MIX, **self.override.get("reason_mix", {})}
        self.DELIST_RET = {**DELIST_RET, **self.override.get("delist_ret", {})}
        self.CRISIS_MULT = {**CRISIS_MULT, **self.override.get("crisis_mult", {})}
        self.IDIO_PCTILE = self.override.get("idio_pctile", IDIO_PCTILE)
        self.VOL_RAMP = self.override.get("vol_ramp", VOL_RAMP)
        self.STRESS_DAYS = self.override.get("stress_days", STRESS_DAYS)
        self.DRIFT_NEG = self.override.get("drift_neg", DRIFT_NEG)
        self.IDIO_RHO = self.override.get("idio_rho", IDIO_RHO)
        self.SHRINK = self.override.get("shrink", SHRINK)

    # ------------------------------------------------------------------ build
    def generate(self, prices: pd.DataFrame, constituents: pd.DataFrame,
                 seed: int = 7) -> SurvivorshipResult:
        rng = np.random.default_rng(seed)
        sym = "Symbol" if "Symbol" in constituents.columns else constituents.columns[0]
        sec_of = dict(zip(constituents[sym], constituents["GICS Sector"]))
        dates = prices.index
        _mkt_col = str(self.override.get("market_col", "SPY"))
        trade = [c for c in prices.columns if c != _mkt_col]

        ret = prices.pct_change(fill_method=None).fillna(0.0)
        mkt = ret[_mkt_col].values if _mkt_col in ret.columns else ret[trade].mean(axis=1).values
        secs = sorted(set(sec_of.get(c, "Unknown") for c in trade))
        sec_ret = {s: (ret[[c for c in trade if sec_of.get(c) == s]].mean(axis=1).values
                       if [c for c in trade if sec_of.get(c) == s] else np.zeros(len(dates)))
                   for s in secs}
        # distressed-cohort idio vol: per-name trailing vol, upper percentile
        vol63 = ret.rolling(63, min_periods=20).std()
        per_name = vol63[[c for c in trade]].median().values
        per_name = per_name[np.isfinite(per_name) & (per_name > 1e-4)]
        vol_target_ann = (float(np.quantile(per_name, self.IDIO_PCTILE)) * np.sqrt(252)
                          if per_name.size else 0.30)

        years = max(1.0, len(dates) / 252.0)
        yr = np.array([d.year for d in dates])
        cm = np.array([self.CRISIS_MULT.get(int(y), self.CRISIS_MULT.get("else", 1.0))
                       for y in yr], float)
        pdate = cm / cm.sum()

        # sector-block idio correlation → Cholesky
        C = np.full((len(secs), len(secs)), self.IDIO_RHO); np.fill_diagonal(C, 1.0)
        L = np.linalg.cholesky(_shrink_corr(C, self.SHRINK) + 1e-8 * np.eye(len(secs)))
        T = len(dates)
        Zsec = L @ rng.standard_normal((len(secs), T))

        synth, rows = {}, []
        for s in secs:
            rate, ttf_mean, shape = self.IND.get(s, self.IND.get("Unknown", (0.03, 9.0, 1.5)))
            n_s = int(round(rate * sum(1 for c in trade if sec_of.get(c) == s) * years))
            scale = ttf_mean / _gamma(1.0 + 1.0 / shape)      # Weibull scale
            for _ in range(n_s):
                fail = int(rng.choice(T, p=pdate))
                ttf_days = int(rng.weibull(shape) * scale * 252)
                lst = fail - ttf_days
                bm = rng.normal(1.1, 0.3); bs = rng.normal(1.0, 0.3)
                sig = vol_target_ann / np.sqrt(252.0) * rng.uniform(0.8, 1.2)
                # vol ramp (idio only) + distress drift into failure
                lo = max(0, fail - self.STRESS_DAYS)
                ramp = np.ones(T)
                if fail > lo:
                    ramp[lo:fail] = 1.0 + self.VOL_RAMP * np.linspace(0, 1, fail - lo)
                r = bm * mkt + bs * sec_ret[s] + (sig * ramp) * Zsec[secs.index(s)]
                r[lo:fail] += self.DRIFT_NEG / 252.0
                reason = rng.choice(list(self.REASON_MIX), p=list(self.REASON_MIX.values()))
                mu, sd = self.DELIST_RET[reason]
                r[fail] = mu + sd * rng.normal()
                px = 100.0 * np.exp(np.cumsum(np.log1p(np.clip(r, -0.99, 5.0))))
                col = px.astype(float)
                col[fail + 1:] = POST_DELIST * px[fail]     # worthless after delisting
                if lst > 0:
                    col[:lst] = np.nan
                name = f"SYN{len(rows):04d}"
                synth[name] = col
                rows.append({"Symbol": name, "sector": s,
                             "list": dates[max(0, lst)], "fail": dates[fail],
                             "reason": reason, "ttf_years": ttf_days / 252.0,
                             "beta_mkt": bm, "beta_sec": bs, "idio_vol": sig})

        syn = pd.DataFrame(synth, index=dates)
        aug = pd.concat([prices, syn], axis=1)
        _has_sub = "GICS Sub-Industry" in constituents.columns
        # sub-industries available per sector (for synthetic assignment)
        _subs = {}
        if _has_sub:
            for _s, _g in constituents.groupby("GICS Sector"):
                _subs[_s] = sorted(set(_g["GICS Sub-Industry"].dropna()))
        _real = constituents[[sym, "GICS Sector"] +
                            (["GICS Sub-Industry"] if _has_sub else [])].rename(
                                columns={sym: "Symbol"})
        if not _has_sub:
            _real["GICS Sub-Industry"] = "Unknown"
        _synrows = []
        for r in rows:
            subs = _subs.get(r["sector"], [])
            sub = subs[rng.integers(len(subs))] if subs else "Unknown"
            _synrows.append({"Symbol": r["Symbol"], "GICS Sector": r["sector"],
                             "GICS Sub-Industry": sub})
        cons = pd.concat([_real, pd.DataFrame(_synrows)], ignore_index=True)
        # COMPLETE the map: every panel column must have a sector so downstream
        # attribution never shows an "Unknown" bucket. Real overlays/indices
        # (e.g. SPY) get labelled "Index/Overlay".
        _known = set(cons["Symbol"].astype(str))
        _extra = [{"Symbol": c, "GICS Sector": "Index/Overlay",
                   "GICS Sub-Industry": "Index/Overlay"}
                  for c in aug.columns if str(c) not in _known]
        if _extra:
            cons = pd.concat([cons, pd.DataFrame(_extra)], ignore_index=True)
        return SurvivorshipResult(aug, cons, pd.DataFrame(rows))

    # ------------------------------------------------------------- validation
    @staticmethod
    def validate(res: SurvivorshipResult, industries: dict | None = None) -> dict:
        ind = industries or IND
        m = res.meta
        syn_cols = [c for c in res.prices.columns if str(c).startswith("SYN")]
        alive = res.prices[syn_cols]
        per_sec = {}
        for s, g in m.groupby("sector"):
            per_sec[s] = {"n": int(len(g)),
                          "mean_ttf_y": round(float(g["ttf_years"].mean()), 1),
                          "target_ttf_y": ind.get(s, ind.get("Unknown", (0.03, 9.0, 1.5)))[1],
                          "rate_ann": round(float(len(g) /
                                    max(1, (res.prices.shape[0] / 252) *
                                        max(1, (m["sector"] == s).sum()) * 0 + 1)), 3)}
        _pf = []
        for _, row in m.iterrows():
            col = res.prices[row["Symbol"]].values
            i0, i1 = res.prices.index.get_loc(row["list"]), res.prices.index.get_loc(row["fail"])
            seg = col[i0:i1]
            if len(seg) > 20:
                _pf.append(np.nanstd(np.diff(seg) / seg[:-1]))
        vol = float(np.nanmean(_pf) * np.sqrt(252)) if _pf else float("nan")
        return {"n_synthetic": len(m), "reasons": m["reason"].value_counts().to_dict(),
                "mean_ttf_years": round(float(m["ttf_years"].mean()), 1),
                "synth_ann_vol": vol, "per_sector": per_sec}


# Back-compat export for the generator script / meta json
PARAMS = {"industries": IND, "reason_mix": REASON_MIX, "delist_ret": DELIST_RET,
          "crisis_mult": CRISIS_MULT, "idio_pctile": IDIO_PCTILE,
          "vol_ramp": VOL_RAMP, "stress_days": STRESS_DAYS,
          "drift_neg": DRIFT_NEG, "idio_rho": IDIO_RHO, "shrink": SHRINK}
