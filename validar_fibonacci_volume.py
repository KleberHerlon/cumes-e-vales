#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""validar_fibonacci_volume.py — Validação estatística das hipóteses do PESQUISA.md.

Analisa os históricos M1 (amostra_*_m1.csv) nos 7 ativos da B3 para testar:
  1. Concentração de níveis e volume próximo às proporções áureas (0.382, 0.500, 0.618)
     no eixo Z (volume normalizado pelo pico POC do dia) e no eixo de preço (H-L).
  2. Poder preditivo (sem look-ahead): taxa de reversão/rejeição em 15 min quando o
     preço toca faixas áureas de volume acumulado vs faixas controle.
  3. POC como equilíbrio: taxa de rejeição vs aceitação/rompimento ao cruzar a POC.
"""

from __future__ import annotations

import argparse
import csv
import math
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
PHI_BANDS = (0.382, 0.500, 0.618)
TOL = 0.035  # +-3.5% -> 3 janelas de 7% = 21.0% baseline uniforme


@dataclass
class Bar:
    date: str
    time: str
    open: float
    high: float
    low: float
    close: float
    vol: float


def load_bars(csv_path: Path) -> dict[str, list[Bar]]:
    by_day: dict[str, list[Bar]] = defaultdict(list)
    with csv_path.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter=";")
        for r in reader:
            try:
                v = float(r.get("VOL") or r.get("TICKVOL") or r.get("VOLUME") or 0)
                if v <= 0:
                    continue
                by_day[r["DATE"]].append(
                    Bar(
                        date=r["DATE"],
                        time=r["TIME"],
                        open=float(r["OPEN"]),
                        high=float(r["HIGH"]),
                        low=float(r["LOW"]),
                        close=float(r["CLOSE"]),
                        vol=v,
                    )
                )
            except (KeyError, ValueError):
                continue
    return {d: bars for d, bars in by_day.items() if len(bars) >= 60}


def step_for(symbol_tag: str, day_bars: list[Bar]) -> float:
    tag = symbol_tag.upper()
    if tag in ("WIN", "IND"):
        return 50.0
    if tag == "WDO":
        return 2.5
    hi = max(b.high for b in day_bars)
    lo = min(b.low for b in day_bars)
    rng = max(0.20, hi - lo)
    return max(0.05, round(rng / 32.0, 2))


def in_phi_band(r: float, tol: float = TOL) -> bool:
    return any(abs(r - lv) <= tol for lv in PHI_BANDS)


def z_prop(k: int, n: int, p0: float) -> float:
    if n <= 0 or p0 <= 0 or p0 >= 1:
        return 0.0
    se = math.sqrt(p0 * (1.0 - p0) / n)
    return (k / n - p0) / se if se > 0 else 0.0


def two_sided_p(z: float) -> float:
    return math.erfc(abs(z) / math.sqrt(2.0))


def analyze_asset(tag: str, path: Path) -> dict:
    days = load_bars(path)
    p0 = len(PHI_BANDS) * 2.0 * TOL  # 0.21

    total_buckets = 0
    phi_buckets = 0
    total_non_poc_vol = 0.0
    phi_non_poc_vol = 0.0

    # Eixo preco (H-L): comparacao de volume medio em faixas aureas de preco vs controle
    price_phi_vol = 0.0
    price_ctrl_vol = 0.0
    price_phi_cnt = 0
    price_ctrl_cnt = 0

    # Testes causais (sem look-ahead apos os primeiros 60 candles do dia)
    phi_touches = 0
    phi_reversals = 0
    ctrl_touches = 0
    ctrl_reversals = 0

    poc_touches = 0
    poc_rejections = 0

    horizon = 15

    for _day, bars in sorted(days.items()):
        step = step_for(tag, bars)
        # 1. Perfil diario completo (para distribuicao de volume)
        profile: dict[float, float] = defaultdict(float)
        for b in bars:
            k = round(b.close / step) * step
            profile[k] += b.vol
        if len(profile) < 5:
            continue
        max_vol = max(profile.values())
        if max_vol <= 0:
            continue

        day_hi = max(b.high for b in bars)
        day_lo = min(b.low for b in bars)
        day_rng = day_hi - day_lo

        for price_lvl, v in profile.items():
            r_vol = v / max_vol
            if r_vol < 0.999:
                total_buckets += 1
                total_non_poc_vol += v
                if in_phi_band(r_vol):
                    phi_buckets += 1
                    phi_non_poc_vol += v
            if day_rng > 0:
                r_px = (price_lvl - day_lo) / day_rng
                if in_phi_band(r_px):
                    price_phi_vol += v
                    price_phi_cnt += 1
                else:
                    price_ctrl_vol += v
                    price_ctrl_cnt += 1

        # 2. Simulacao causal intradiaria (t >= 60 .. len-horizon-1)
        running: dict[float, float] = defaultdict(float)
        for i in range(min(60, len(bars))):
            k = round(bars[i].close / step) * step
            running[k] += bars[i].vol

        cooldown_phi = -99
        cooldown_ctrl = -99
        cooldown_poc = -99

        for i in range(60, len(bars) - horizon):
            b = bars[i]
            k = round(b.close / step) * step
            running[k] += b.vol

            poc_k, poc_v = max(running.items(), key=lambda kv: kv[1])
            if poc_v <= 0:
                continue
            r_cur = running[k] / poc_v
            prev_close = bars[i - 5].close
            approach_dir = 1 if b.close > prev_close else (-1 if b.close < prev_close else 0)
            if approach_dir == 0:
                continue

            future_slice = bars[i + 1 : i + 1 + horizon]
            end_move = future_slice[-1].close - b.close
            rev_hit = (end_move * approach_dir) <= -step * 0.5

            # Toque na POC intradiaria
            if abs(b.close - poc_k) <= step * 0.5 and i - cooldown_poc >= horizon:
                cooldown_poc = i
                poc_touches += 1
                if rev_hit:
                    poc_rejections += 1
            elif r_cur < 0.95:
                if in_phi_band(r_cur):
                    if i - cooldown_phi >= horizon:
                        cooldown_phi = i
                        phi_touches += 1
                        if rev_hit:
                            phi_reversals += 1
                else:
                    if i - cooldown_ctrl >= horizon:
                        cooldown_ctrl = i
                        ctrl_touches += 1
                        if rev_hit:
                            ctrl_reversals += 1

    freq_obs = phi_buckets / total_buckets if total_buckets else 0.0
    vol_share = phi_non_poc_vol / total_non_poc_vol if total_non_poc_vol else 0.0
    z_freq = z_prop(phi_buckets, total_buckets, p0)
    p_freq = two_sided_p(z_freq)

    avg_px_phi = price_phi_vol / price_phi_cnt if price_phi_cnt else 0.0
    avg_px_ctrl = price_ctrl_vol / price_ctrl_cnt if price_ctrl_cnt else 0.0
    px_vol_ratio = avg_px_phi / avg_px_ctrl if avg_px_ctrl > 0 else 1.0

    phi_rev_rate = phi_reversals / phi_touches if phi_touches else 0.0
    ctrl_rev_rate = ctrl_reversals / ctrl_touches if ctrl_touches else 0.0
    poc_rej_rate = poc_rejections / poc_touches if poc_touches else 0.0

    return {
        "tag": tag,
        "days": len(days),
        "buckets": total_buckets,
        "phi_freq": freq_obs,
        "phi_vol_share": vol_share,
        "enrich_freq": freq_obs / p0 if p0 else 0.0,
        "enrich_vol": vol_share / p0 if p0 else 0.0,
        "z_freq": z_freq,
        "p_freq": p_freq,
        "px_vol_ratio": px_vol_ratio,
        "phi_touches": phi_touches,
        "phi_rev_rate": phi_rev_rate,
        "ctrl_touches": ctrl_touches,
        "ctrl_rev_rate": ctrl_rev_rate,
        "poc_touches": poc_touches,
        "poc_rej_rate": poc_rej_rate,
    }


def main() -> None:
    p = argparse.ArgumentParser(description="Validação estatística Fibonacci no Volume (PESQUISA.md)")
    p.add_argument("--dir", default=str(HERE), help="Diretório com amostra_*_m1.csv")
    args = p.parse_args()

    files = sorted(Path(args.dir).glob("amostra_*_m1.csv"))
    if not files:
        print("Nenhum arquivo amostra_*_m1.csv encontrado.")
        return

    print(f"=== Validação Estatística Fibonacci x Volume (baseline uniforme = 21.0%, tol = ±{TOL:.3f}) ===")
    print(f"{'Ativo':<7} {'Dias':>4} {'Buckets':>7} {'FreqΦ':>7} {'Enriq':>6} {'VolΦ':>7} {'Z':>6} {'p-val':>8} {'VolPxΦ':>7} {'RevΦ':>7} {'RevCtrl':>8} {'RejPOC':>7}")
    results = []
    for f in files:
        tag = f.stem.replace("amostra_", "").replace("_m1", "").upper()
        r = analyze_asset(tag, f)
        results.append(r)
        print(
            f"{r['tag']:<7} {r['days']:>4d} {r['buckets']:>7d} "
            f"{r['phi_freq']:>6.1%} {r['enrich_freq']:>5.2f}x {r['phi_vol_share']:>6.1%} "
            f"{r['z_freq']:>+6.2f} {r['p_freq']:>8.4f} {r['px_vol_ratio']:>6.2f}x "
            f"{r['phi_rev_rate']:>6.1%} {r['ctrl_rev_rate']:>7.1%} {r['poc_rej_rate']:>6.1%}"
        )


if __name__ == "__main__":
    main()
