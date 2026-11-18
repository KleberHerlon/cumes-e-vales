#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bridge_mt5.py — Ponte WebSocket do MT5 para o "CUMES E VALES".

Conecta no terminal MetaTrader 5 (Mini Índice WIN / B3) e transmite, via
WebSocket, um fluxo JSON consumido pelo index.html:

  - ticks / candles (novas barras fechadas e a barra em formação)
  - pivôs das ondas ABC **seguindo exatamente os requisitos do robô
    Piolho Agressivo** (mesmo detector config_piolho_agressivo.json)
  - operações reais (posições abertas + deals encerrados, filtrados por magic)

Uso local (o navegador no GitHub Pages conecta em ws://localhost:8000/ws):
  python bridge_mt5.py --symbol WINZ26 --timeframe M1 --port 8000

Para expor em HTTPS (GitHub Pages bloqueia ws:// por Mixed Content), rode um
túnel e aponte o campo WebSocket do HUD para wss://<tunnel>/ws:
  cloudflared tunnel --url http://localhost:8000
  ngrok http 8000
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import fields as dc_fields
from datetime import datetime, timedelta
from pathlib import Path

import MetaTrader5 as mt5
import websockets

HERE = Path(__file__).resolve().parent
ABC_TRADER = HERE.parent / "abc-trader"
DEFAULT_CONFIG = ABC_TRADER / "config_piolho_agressivo.json"

ABC_AVAILABLE = False
if ABC_TRADER.exists():
    sys.path.insert(0, str(ABC_TRADER))
try:
    from abc_trader.models import Bar, Direction  # noqa: E402
    from abc_trader.pattern import DetectorParams, PatternDetector  # noqa: E402

    ABC_AVAILABLE = True
except Exception as exc:  # noqa: BLE001
    print(f"[aviso] detector ABC indisponível ({exc}); transmitindo só candles/operações")

TIMEFRAMES = {
    "M1": mt5.TIMEFRAME_M1,
    "M5": mt5.TIMEFRAME_M5,
    "M15": mt5.TIMEFRAME_M15,
    "M30": mt5.TIMEFRAME_M30,
    "H1": mt5.TIMEFRAME_H1,
    "H4": mt5.TIMEFRAME_H4,
    "D1": mt5.TIMEFRAME_D1,
}

CLIENTS: set = set()
SYMBOL = "WINZ26"
TIMEFRAME = "M1"
HISTORY: list = []
HISTORY_COUNT = 600
MAGIC = 20261022
DETECTOR = None
WATCHLIST: list = []
HISTORIES: dict = {}


def log(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def parse_symbol_from_path(path) -> str | None:
    if not path or "symbol=" not in path:
        return None
    try:
        for part in path.split("?", 1)[1].split("&"):
            if part.startswith("symbol="):
                return part.split("=", 1)[1].strip().upper() or None
    except Exception:  # noqa: BLE001
        return None
    return None


def build_detector(config_path: Path):
    with open(config_path, encoding="utf-8") as f:
        cfg = json.load(f)
    det = cfg.get("detector", {})
    names = {f.name for f in dc_fields(DetectorParams)}
    params = DetectorParams(**{k: v for k, v in det.items() if k in names})
    return PatternDetector(params)


def direction_label(direction) -> str:
    if direction is Direction.LONG:
        return "compra"
    return "venda"


def fetch_bars(symbol: str, timeframe: str, count: int) -> list[Bar]:
    tf = TIMEFRAMES[timeframe.upper()]
    rates = mt5.copy_rates_from_pos(symbol, tf, 0, count)
    if rates is None:
        return []
    bars = []
    for r in rates:
        bars.append(
            Bar(
                time=int(r["time"]),
                open=float(r["open"]),
                high=float(r["high"]),
                low=float(r["low"]),
                close=float(r["close"]),
                tick_volume=int(r["tick_volume"]),
                real_volume=int(r["real_volume"]),
            )
        )
    return bars


def detect_pattern(detector, bars: list[Bar], symbol: str, timeframe: str) -> dict | None:
    pat = detector.analyze(bars, symbol, timeframe)
    if pat is None:
        return None
    return {
        "type": "pivot",
        "symbol": symbol,
        "o_time": pat.a.start.time,
        "o_price": pat.a.start.price,
        "a_time": pat.a.end.time,
        "a_price": pat.a.end.price,
        "b_time": pat.b.end.time,
        "b_price": pat.b.end.price,
        "c_time": pat.c.end.time,
        "c_price": pat.c.end.price,
        "scenario": pat.scenario.label,
        "direction": direction_label(pat.direction),
        "confidence": round(pat.confidence, 3),
        "result": "active",
        "key": pat.key,
    }


def bar_to_msg(symbol: str, timeframe: str, row) -> dict:
    real = int(row["real_volume"])
    tick = int(row["tick_volume"])
    return {
        "type": "bar",
        "symbol": symbol,
        "timeframe": timeframe,
        "time": int(row["time"]),
        "open": float(row["open"]),
        "high": float(row["high"]),
        "low": float(row["low"]),
        "close": float(row["close"]),
        "volume": real if real > 0 else tick,
    }


def rate_to_plain(row) -> dict:
    return {
        "time": int(row["time"]),
        "open": float(row["open"]),
        "high": float(row["high"]),
        "low": float(row["low"]),
        "close": float(row["close"]),
        "tick_volume": int(row["tick_volume"]),
        "real_volume": int(row["real_volume"]),
    }


def history_cache_path(symbol: str, timeframe: str) -> Path:
    safe = "".join(c if c.isalnum() else "_" for c in symbol)
    return HERE / f"history_{safe}_{timeframe}.json"


def load_history(symbol: str, timeframe: str, days: int, refresh: bool = False) -> list[dict]:
    path = history_cache_path(symbol, timeframe)
    if path.exists() and not refresh:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            bars = data.get("bars") or []
            if data.get("symbol") == symbol and data.get("timeframe") == timeframe and bars:
                last_ts = int(bars[-1]["time"])
                if datetime.now().timestamp() - last_ts < 86400:
                    log(f"cache carregado: {path.name} ({len(bars)} barras)")
                    return bars
        except Exception:  # noqa: BLE001
            pass
    date_from = datetime.now() - timedelta(days=days)
    log(f"baixando histórico de {days} dia(s) ({date_from:%d/%m/%Y} → hoje)...")
    rates = mt5.copy_rates_range(symbol, TIMEFRAMES[timeframe.upper()], date_from, datetime.now())
    if rates is None:
        log(f"falha ao baixar histórico de {symbol}: {mt5.last_error()}")
        return []
    rows = [rate_to_plain(r) for r in rates]
    try:
        path.write_text(json.dumps({"symbol": symbol, "timeframe": timeframe, "bars": rows}), encoding="utf-8")
        log(f"cache salvo: {path.name} ({len(rows)} barras)")
    except Exception:  # noqa: BLE001
        pass
    return rows


def tick_msg(symbol: str) -> dict | None:
    t = mt5.symbol_info_tick(symbol)
    if t is None:
        return None
    return {"type": "tick", "symbol": symbol, "time": int(t.time), "bid": float(t.bid), "ask": float(t.ask)}


def recent_tick_delta(symbol: str, from_dt) -> tuple[float, float]:
    try:
        ticks = mt5.copy_ticks_from(symbol, from_dt, 100000, mt5.COPY_TICKS_INFO)
    except Exception:  # noqa: BLE001
        return 0.0, 0.0
    if ticks is None or len(ticks) == 0:
        return 0.0, 0.0
    buy = sell = 0.0
    for t in ticks:
        flags = int(t["flags"])
        vol = float(t["volume"]) if t["volume"] else 1.0
        if flags & mt5.TICK_FLAG_BUY:
            buy += vol
        elif flags & mt5.TICK_FLAG_SELL:
            sell += vol
    return buy, sell


def open_positions_msgs(symbol: str, magic: int) -> list[dict]:
    out = []
    try:
        positions = mt5.positions_get(symbol=symbol)
    except Exception:  # noqa: BLE001
        return out
    for p in positions or []:
        if p.magic != magic:
            continue
        out.append({
            "type": "trade",
            "id": f"pos{p.ticket}",
            "symbol": symbol,
            "direction": "compra" if p.type == mt5.POSITION_TYPE_BUY else "venda",
            "scenario": "",
            "entry_time": int(p.time),
            "entry_price": float(p.price_open),
            "exit_time": None,
            "exit_price": None,
            "result": None,
        })
    return out


def closed_deals_msgs(symbol: str, magic: int, sent: set) -> list[dict]:
    out = []
    try:
        deals = mt5.history_deals_get(datetime.now() - timedelta(days=3), datetime.now() + timedelta(days=1))
    except Exception:  # noqa: BLE001
        return out
    groups: dict[int, list] = {}
    for d in deals or []:
        if d.magic != magic or d.symbol != symbol:
            continue
        groups.setdefault(d.position_id, []).append(d)
    for ds in groups.values():
        ins = [d for d in ds if d.entry in (mt5.DEAL_ENTRY_IN, mt5.DEAL_ENTRY_INOUT)]
        outs = [d for d in ds if d.entry in (mt5.DEAL_ENTRY_OUT, mt5.DEAL_ENTRY_INOUT)]
        if not ins:
            continue
        entry = min(ins, key=lambda d: d.time)
        msg_id = f"deal{entry.ticket}"
        if msg_id in sent:
            continue
        sent.add(msg_id)
        direction = "compra" if entry.type == mt5.DEAL_TYPE_BUY else "venda"
        if outs:
            exit_deal = max(outs, key=lambda d: d.time)
            result = "gain" if float(exit_deal.profit) > 0 else "stop"
            out.append({
                "type": "trade",
                "id": msg_id,
                "symbol": symbol,
                "direction": direction,
                "scenario": "",
                "entry_time": int(entry.time),
                "entry_price": float(entry.price),
                "exit_time": int(exit_deal.time),
                "exit_price": float(exit_deal.price),
                "result": result,
            })
        else:
            out.append({
                "type": "trade",
                "id": msg_id,
                "symbol": symbol,
                "direction": direction,
                "scenario": "",
                "entry_time": int(entry.time),
                "entry_price": float(entry.price),
                "exit_time": None,
                "exit_price": None,
                "result": None,
            })
    return out


async def broadcast(message: dict) -> None:
    if not CLIENTS:
        return
    payload = json.dumps(message, ensure_ascii=False, default=str)
    dead = []
    for ws in list(CLIENTS):
        try:
            await ws.send(payload)
        except websockets.ConnectionClosed:
            dead.append(ws)
    for ws in dead:
        CLIENTS.discard(ws)


async def handler(websocket, *_args) -> None:
    CLIENTS.add(websocket)
    log(f"cliente conectado ({len(CLIENTS)} ativos)")
    try:
        await websocket.send(json.dumps({"type": "hello", "symbol": SYMBOL, "timeframe": TIMEFRAME}))
        bars = [bar_to_msg(SYMBOL, TIMEFRAME, row) for row in HISTORY]
        for i in range(0, len(bars), 2000):
            await websocket.send(json.dumps({"type": "bars", "bars": bars[i:i + 2000]}, default=str))
        if ABC_AVAILABLE and DETECTOR is not None:
            bars_snapshot = fetch_bars(SYMBOL, TIMEFRAME, HISTORY_COUNT)
            pat = detect_pattern(DETECTOR, bars_snapshot, SYMBOL, TIMEFRAME)
            if pat:
                await websocket.send(json.dumps(pat))
        for m in open_positions_msgs(SYMBOL, MAGIC):
            await websocket.send(json.dumps(m, default=str))
        for m in closed_deals_msgs(SYMBOL, MAGIC, set()):
            await websocket.send(json.dumps(m, default=str))
        async for _ in websocket:
            pass
    except websockets.ConnectionClosed:
        pass
    finally:
        CLIENTS.discard(websocket)
        log("cliente desconectado")


async def poll_loop(symbol: str, timeframe: str, history: int, interval: float, magic: int, detector) -> None:
    tf = TIMEFRAMES[timeframe.upper()]
    last_sent = None
    last_pattern_key = None
    sent_deals: set = set()
    cum_buy = 0.0
    cum_sell = 0.0
    last_delta_dt = datetime.now()
    while True:
        try:
            tick = tick_msg(symbol)
            if tick:
                await broadcast(tick)

            now_dt = datetime.now()
            b, s = recent_tick_delta(symbol, last_delta_dt)
            last_delta_dt = now_dt
            if b or s:
                cum_buy += b
                cum_sell += s
                await broadcast({"type": "delta", "buy": round(cum_buy), "sell": round(cum_sell), "net": round(cum_buy - cum_sell)})

            rates = mt5.copy_rates_from_pos(symbol, tf, 0, 2)
            if rates is not None and len(rates) >= 2:
                closed = rates[-2]
                key = int(closed["time"])
                if key != last_sent:
                    last_sent = key
                    plain = rate_to_plain(closed)
                    HISTORY.append(plain)
                    await broadcast(bar_to_msg(symbol, timeframe, plain))
                    if detector is not None:
                        bars = fetch_bars(symbol, timeframe, history)
                        pat = detect_pattern(detector, bars, symbol, timeframe)
                        if pat and pat["key"] != last_pattern_key:
                            last_pattern_key = pat["key"]
                            await broadcast(pat)

            for m in open_positions_msgs(symbol, magic):
                await broadcast(m)
            for m in closed_deals_msgs(symbol, magic, sent_deals):
                await broadcast(m)
        except Exception as exc:  # noqa: BLE001
            log(f"erro no loop: {exc}")
        await asyncio.sleep(interval)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Ponte WebSocket MT5 -> CUMES E VALES (Piolho Agressivo)")
    p.add_argument("--symbol", default=os.environ.get("CV_SYMBOL", "WINZ26"))
    p.add_argument("--timeframe", default=os.environ.get("CV_TIMEFRAME", "M1"))
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=int(os.environ.get("CV_PORT", "8000")))
    p.add_argument("--history", type=int, default=int(os.environ.get("CV_HISTORY", "600")), help="barras para o detector ABC")
    p.add_argument("--history-days", type=int, default=int(os.environ.get("CV_HISTORY_DAYS", "365")), help="dias de histórico a carregar (padrão 365 = 1 ano)")
    p.add_argument("--refresh", action="store_true", help="força novo download do histórico (ignora cache)")
    p.add_argument("--interval", type=float, default=1.0)
    p.add_argument("--config", default=str(DEFAULT_CONFIG), help="JSON de config do robô (detector + magic)")
    p.add_argument("--magic", type=int, default=None, help="Magic a filtrar (padrão: execution.magic do config)")
    p.add_argument("--mt5-path", default=os.environ.get("CV_MT5_PATH", r"C:\Program Files\MetaTrader 5\terminal64.exe"))
    p.add_argument("--login", type=int, default=None)
    p.add_argument("--password", default=None)
    p.add_argument("--server", default=None)
    return p


async def main() -> None:
    global SYMBOL, TIMEFRAME, HISTORY, MAGIC
    args = build_parser().parse_args()
    timeframe = args.timeframe.upper()
    if timeframe not in TIMEFRAMES:
        log(f"timeframe inválido: {args.timeframe}")
        sys.exit(2)

    config_path = Path(args.config)
    detector = None
    magic = args.magic
    if config_path.exists():
        with open(config_path, encoding="utf-8") as f:
            cfg = json.load(f)
        if magic is None:
            magic = int(cfg.get("execution", {}).get("magic", 20261022))
        if ABC_AVAILABLE:
            detector = build_detector(config_path)
            log(f"detector ABC carregado do config: {config_path.name}")
    else:
        if magic is None:
            magic = 20261022
        log(f"config não encontrado: {config_path} (ABC desligado)")
    MAGIC = magic

    init_kwargs: dict = {"timeout": 60000}
    if args.mt5_path:
        init_kwargs["path"] = args.mt5_path
    if args.login:
        init_kwargs["login"] = int(args.login)
        if args.password:
            init_kwargs["password"] = args.password
        if args.server:
            init_kwargs["server"] = args.server
    if not mt5.initialize(**init_kwargs):
        log(f"falha ao inicializar MT5: {mt5.last_error()}")
        sys.exit(1)
    log(f"MT5 conectado | símbolo={args.symbol} timeframe={timeframe} magic={magic}")

    if not mt5.symbol_info(args.symbol):
        log(f"símbolo não encontrado: {args.symbol}")
        mt5.shutdown()
        sys.exit(1)

    history = load_history(args.symbol, timeframe, args.history_days, refresh=args.refresh)
    log(f"{len(history)} barras de histórico carregadas")

    SYMBOL = args.symbol
    TIMEFRAME = timeframe
    HISTORY = history

    global DETECTOR, HISTORY_COUNT
    DETECTOR = detector
    HISTORY_COUNT = args.history

    async with websockets.serve(handler, args.host, args.port):
        log(f"ouvindo em ws://{args.host}:{args.port}/ws")
        await poll_loop(args.symbol, timeframe, args.history, args.interval, magic, detector)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        mt5.shutdown()
        log("encerrado")
