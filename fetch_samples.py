"""Baixa histórico M1 do MT5 e grava amostra_*.csv no formato do CUMES E VALES.

Formato de saída (igual aos arquivos já existentes):
    DATE;TIME;OPEN;HIGH;LOW;CLOSE;VOL
    2026.05.22;07:34:00;189000.0;189000.0;188825.0;188825.0;2

Uso:
    python fetch_samples.py --symbols PETR4 VALE3 ITUB4 BBDC4
    python fetch_samples.py --all --days 180
"""
import argparse
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import MetaTrader5 as mt5

HERE = Path(__file__).resolve().parent

# Ativos disponíveis nesta conta B3 (sem Forex): ações + índices.
DEFAULT_SYMBOLS = ["PETR4", "VALE3", "ITUB4", "BBDC4", "IBOV", "WINZ26", "WDOZ26"]

# slug usado no nome do arquivo amostra_<slug>_m1.csv (opcional; padrão = símbolo).
SLUG = {
    "WINZ26": "win",
    "WDOZ26": "wdo",
    "IBOV": "ind",
}


def slug_for(symbol: str) -> str:
    if symbol in SLUG:
        return SLUG[symbol]
    return "".join(c if c.isalnum() else "_" for c in symbol).lower()


def fetch(symbol: str, days: int) -> list[dict] | None:
    date_from = datetime.now() - timedelta(days=days)
    rates = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_M1, date_from, datetime.now())
    if rates is None or len(rates) == 0:
        print(f"  [{symbol}] sem dados (erro {mt5.last_error()})")
        return None
    rows = []
    for r in rates:
        t = datetime.fromtimestamp(int(r["time"]), tz=timezone.utc)
        vol = int(r["real_volume"]) if int(r["real_volume"]) > 0 else int(r["tick_volume"])
        rows.append({
            "date": t.strftime("%Y.%m.%d"),
            "time": t.strftime("%H:%M:%S"),
            "open": float(r["open"]),
            "high": float(r["high"]),
            "low": float(r["low"]),
            "close": float(r["close"]),
            "vol": vol,
        })
    return rows


def write_csv(symbol: str, rows: list[dict]) -> Path:
    slug = slug_for(symbol)
    path = HERE / f"amostra_{slug}_m1.csv"
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("DATE;TIME;OPEN;HIGH;LOW;CLOSE;VOL\n")
        for r in rows:
            f.write(
                f"{r['date']};{r['time']};{r['open']};{r['high']};{r['low']};{r['close']};{r['vol']}\n"
            )
    return path


def main() -> None:
    p = argparse.ArgumentParser(description="Baixa histórico M1 do MT5 para amostras do CUMES E VALES")
    p.add_argument("--symbols", nargs="*", default=None, help="símbolos a baixar")
    p.add_argument("--all", action="store_true", help="baixa todos os símbolos padrão")
    p.add_argument("--days", type=int, default=int(os.environ.get("CV_HISTORY_DAYS", "365")))
    args = p.parse_args()

    if args.symbols:
        symbols = args.symbols
    elif args.all:
        symbols = DEFAULT_SYMBOLS
    else:
        p.error("informe --symbols <...> ou --all")

    if not mt5.initialize(timeout=60000):
        print(f"falha ao inicializar MT5: {mt5.last_error()}")
        raise SystemExit(1)

    print(f"MT5 conectado | baixando {len(symbols)} símbolo(s) · até {args.days} dias de M1")
    try:
        for sym in symbols:
            sym = sym.strip().upper()
            if not mt5.symbol_info(sym):
                print(f"  [{sym}] símbolo não encontrado — pulando")
                continue
            rows = fetch(sym, args.days)
            if not rows:
                continue
            path = write_csv(sym, rows)
            first = rows[0]
            last = rows[-1]
            print(f"  [{sym}] {len(rows)} barras -> {path.name} ({first['date']} {first['time']} .. {last['date']} {last['time']})")
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    main()
