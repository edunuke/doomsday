"""AlphaCubeStageV5 — augments the base cube with orthogonal sources.

Drop-in: keeps all base columns so downstream (walk_forward) is unchanged; adds
``Dispersion_Z`` / ``OU_Scale`` columns and applies a configurable dispersion
tilt to ``Parametric_Alpha`` so higher-dispersion regimes favour reversion.
"""
from __future__ import annotations

import numpy as np

from modules.processing import AlphaCubeStage
from modules.alpha.sources import dispersion_z, ou_scale


class AlphaCubeStageV5(AlphaCubeStage):
    def run(self, ctx: dict) -> None:
        super().run(ctx)
        params = self.params
        if not bool(params.get("alpha_v5_enabled", True)):
            return
        cube = ctx.get("alpha_cube", {})
        prices = ctx["prices_full"]
        dz = dispersion_z(prices,
                          int(params.get("dispersion_lookback", 252)),
                          int(params.get("dispersion_window", 21)))
        ctx["dispersion_z"] = dz
        tau = float(params.get("alpha_v5_ou_tau", 5.0))
        tilt = float(params.get("alpha_v5_dispersion_tilt", 0.25))
        gate = float(params.get("alpha_v5_dispersion_gate", 0.0))
        for p, df in cube.items():
            if "Half_Life" in df.columns:
                df["OU_Scale"] = ou_scale(df["Half_Life"], tau).values
            df["Dispersion_Z"] = dz.reindex(df.index).fillna(0.0).values
            if "Parametric_Alpha" in df.columns and tilt:
                mult = 1.0 + tilt * np.tanh(df["Dispersion_Z"].values)
                if gate:
                    mult = mult * np.where(df["Dispersion_Z"].values > 0, 1.0 + gate, 1.0)
                df["Parametric_Alpha"] = df["Parametric_Alpha"].values * mult
        print(f"[AlphaV5] orthogonal sources added "
              f"(dispersion tilt={tilt}, ou_tau={tau}); pairs={len(cube)}", flush=True)
