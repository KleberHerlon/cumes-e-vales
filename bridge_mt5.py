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
from urllib.parse import unquote

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

CLIENT_SUBS: dict = {}
SYMBOL = "WINZ26"
TIMEFRAME = "M1"
HISTORY: list = []
HISTORY_COUNT = 600
HISTORY_DAYS = 30
MAGIC = 20261022
DETECTOR = None
WATCHLIST: list = []
HISTORIES: dict = {}

SYMBOL_ALIASES = {
    "WIN": ("WIN", "WINZ26"),
    "WINZ26": ("WIN", "WINZ26"),
    "WDO": ("WDO", "WDOZ26"),
    "WDOZ26": ("WDO", "WDOZ26"),
    "IND": ("IBOV", "IBOV"),
    "IBOV": ("IBOV", "IBOV"),
}


def resolve_mt5_symbol(sym: str | None) -> tuple[str, str]:
    s = (sym or SYMBOL or "WINZ26").strip().upper()
    if s in SYMBOL_ALIASES:
        return SYMBOL_ALIASES[s]
    if s.startswith("WIN"):
        return ("WIN", s)
    if s.startswith("WDO"):
        return ("WDO", s)
    if s.startswith("IND"):
        return ("IBOV", "IBOV")
    return (s, s)


def log(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def extract_req_path(websocket, args) -> str:
    if args and isinstance(args[0], str):
        return args[0]
    req = getattr(websocket, "request", None)
    if req is not None and getattr(req, "path", None):
        return req.path
    return getattr(websocket, "path", "") or ""


def parse_symbol_from_path(path) -> str | None:
    if not path or "symbol=" not in path:
        return None
    try:
        for part in path.split("?", 1)[1].split("&"):
            if part.startswith("symbol="):
                val = unquote(part.split("=", 1)[1]).strip().upper()
                return val or None
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


def get_symbol_data(mt5_sym: str, ui_sym: str, timeframe: str = "M1", refresh: bool = False) -> dict:
    cache_key = (mt5_sym, timeframe)
    cached = HISTORIES.get(cache_key)
    now_ts = datetime.now().timestamp()
    if cached and not refresh and (now_ts - cached.get("loaded_at", 0) < 60):
        return cached

    mt5.symbol_select(mt5_sym, True)
    tf = TIMEFRAMES[timeframe.upper()]
    rates_m1 = mt5.copy_rates_from_pos(mt5_sym, tf, 0, 4000)
    if rates_m1 is not None and len(rates_m1) > 0:
        m1_rows = [rate_to_plain(r) for r in rates_m1]
    else:
        log(f"fallback histórico de {mt5_sym}: {mt5.last_error()}")
        m1_rows = []

    rates_d1 = mt5.copy_rates_from_pos(mt5_sym, mt5.TIMEFRAME_D1, 0, 120)
    d1_msgs = [bar_to_msg(ui_sym, "D1", r) for r in rates_d1] if rates_d1 is not None and len(rates_d1) > 0 else []
    prev_close = None
    if rates_d1 is not None and len(rates_d1) >= 2:
        prev_close = float(rates_d1[-2]["close"])
    elif rates_d1 is not None and len(rates_d1) == 1:
        prev_close = float(rates_d1[-1]["open"])

    data = {"m1": m1_rows, "d1": d1_msgs, "prev_close": prev_close, "loaded_at": now_ts}
    HISTORIES[cache_key] = data
    log(f"histórico carregado: {ui_sym} ({mt5_sym}) · M1={len(m1_rows)} · D1={len(d1_msgs)} · prev_close={prev_close}")
    return data


def tick_msg(mt5_sym: str, ui_sym: str | None = None) -> dict | None:
    ui = ui_sym or mt5_sym
    t = mt5.symbol_info_tick(mt5_sym)
    if t is None:
        return None
    last = float(t.last)
    bid = float(t.bid)
    ask = float(t.ask)
    price = last if last > 0 else (bid if bid > 0 else ask)
    if price <= 0:
        return None
    vol = int(getattr(t, "volume", 0) or 1)
    return {
        "type": "tick",
        "symbol": ui,
        "mt5_symbol": mt5_sym,
        "time": int(t.time),
        "price": price,
        "last": last,
        "bid": bid,
        "ask": ask,
        "volume": vol,
    }


def recent_tick_delta(mt5_sym: str, state: dict) -> tuple[float, float]:
    t = mt5.symbol_info_tick(mt5_sym)
    if t is None or not t.time:
        return 0.0, 0.0
    last_msc = state.get("last_delta_msc")
    if last_msc is None:
        state["last_delta_msc"] = int(t.time_msc)
        return 0.0, 0.0
    from_sec = max(0, int(last_msc // 1000) - 1)
    try:
        ticks = mt5.copy_ticks_from(mt5_sym, from_sec, 5000, mt5.COPY_TICKS_TRADE)
    except Exception:  # noqa: BLE001
        return 0.0, 0.0
    if ticks is None or len(ticks) == 0:
        return 0.0, 0.0
    buy = sell = 0.0
    max_msc = last_msc
    for item in ticks:
        msc = int(item["time_msc"])
        if msc <= last_msc:
            continue
        if msc > max_msc:
            max_msc = msc
        flags = int(item["flags"])
        vol = float(item["volume"]) if item["volume"] else 1.0
        if flags & mt5.TICK_FLAG_BUY:
            buy += vol
        elif flags & mt5.TICK_FLAG_SELL:
            sell += vol
    state["last_delta_msc"] = max_msc
    return buy, sell


def symbol_matches(deal_sym: str, ui_sym: str, mt5_sym: str) -> bool:
    s = (deal_sym or "").strip().upper()
    u = (ui_sym or "").strip().upper()
    m = (mt5_sym or "").strip().upper()
    if not s:
        return False
    if u == "WIN":
        return s.startswith("WIN")
    if u == "WDO":
        return s.startswith("WDO")
    return s in (u, m)


def open_positions_msgs(ui_sym: str, mt5_sym: str, magic: int | None) -> tuple[list[dict], set[int]]:
    out = []
    open_ids: set[int] = set()
    try:
        positions = mt5.positions_get()
    except Exception:  # noqa: BLE001
        return out, open_ids
    for p in positions or []:
        if not symbol_matches(p.symbol, ui_sym, mt5_sym):
            continue
        if magic is not None and p.magic != magic:
            continue
        open_ids.add(int(p.ticket))
        tag = f"magic {p.magic}" if p.magic else "manual"
        out.append({
            "type": "trade",
            "id": f"pos{p.ticket}",
            "symbol": ui_sym,
            "mt5_symbol": p.symbol,
            "direction": "compra" if p.type == mt5.POSITION_TYPE_BUY else "venda",
            "scenario": f"{p.symbol} #{p.ticket} ({tag})",
            "entry_time": int(p.time),
            "entry_price": float(p.price_open),
            "exit_time": None,
            "exit_price": None,
            "result": "open",
            "volume": float(p.volume),
            "profit": float(p.profit),
            "magic": int(p.magic),
        })
    return out, open_ids


def closed_deals_msgs(
    ui_sym: str,
    mt5_sym: str,
    magic: int | None,
    sent: set,
    open_pos_ids: set[int] | None = None,
    days: int = 180,
) -> list[dict]:
    out = []
    try:
        deals = mt5.history_deals_get(datetime.now() - timedelta(days=days), datetime.now() + timedelta(days=1))
    except Exception:  # noqa: BLE001
        return out
    open_set = open_pos_ids or set()
    groups: dict[int, list] = {}
    for d in deals or []:
        pos_id = int(getattr(d, "position_id", 0) or 0)
        if pos_id <= 0 or pos_id in open_set:
            continue
        if not symbol_matches(d.symbol, ui_sym, mt5_sym):
            continue
        if magic is not None and d.magic != magic:
            continue
        groups.setdefault(pos_id, []).append(d)
    for pos_id, ds in sorted(groups.items(), key=lambda kv: min(x.time for x in kv[1])):
        ins = [d for d in ds if d.entry in (mt5.DEAL_ENTRY_IN, mt5.DEAL_ENTRY_INOUT)]
        outs = [d for d in ds if d.entry in (mt5.DEAL_ENTRY_OUT, mt5.DEAL_ENTRY_INOUT)]
        if not ins or not outs:
            continue
        msg_id = f"pos{pos_id}"
        if msg_id in sent:
            continue
        sent.add(msg_id)
        entry = min(ins, key=lambda d: d.time)
        exit_deal = max(outs, key=lambda d: d.time)
        in_vol = sum(float(d.volume) for d in ins) or 1.0
        out_vol = sum(float(d.volume) for d in outs) or 1.0
        entry_price = sum(float(d.price) * float(d.volume) for d in ins) / in_vol
        exit_price = sum(float(d.price) * float(d.volume) for d in outs) / out_vol
        total_profit = sum(
            float(d.profit) + float(getattr(d, "commission", 0.0)) + float(getattr(d, "swap", 0.0))
            for d in ds
        )
        direction = "compra" if entry.type == mt5.DEAL_TYPE_BUY else "venda"
        result = "gain" if total_profit >= 0 else "stop"
        tag = f"magic {entry.magic}" if entry.magic else "manual"
        out.append({
            "type": "trade",
            "id": msg_id,
            "symbol": ui_sym,
            "mt5_symbol": entry.symbol,
            "direction": direction,
            "scenario": f"{entry.symbol} #{pos_id} ({tag})",
            "entry_time": int(entry.time),
            "entry_price": round(entry_price, 4),
            "exit_time": int(exit_deal.time),
            "exit_price": round(exit_price, 4),
            "result": result,
            "volume": round(in_vol, 2),
            "profit": round(total_profit, 2),
            "magic": int(entry.magic),
        })
    return out


async def broadcast_for_symbol(ui_sym: str, mt5_sym: str, message: dict) -> None:
    targets = [ws for ws, sub in list(CLIENT_SUBS.items()) if sub == (ui_sym, mt5_sym)]
    if not targets:
        return
    payload = json.dumps(message, ensure_ascii=False, default=str)
    dead = []
    for ws in targets:
        try:
            await ws.send(payload)
        except websockets.ConnectionClosed:
            dead.append(ws)
    for ws in dead:
        CLIENT_SUBS.pop(ws, None)


async def send_initial_snapshot(websocket, ui_sym: str, mt5_sym: str) -> None:
    data = get_symbol_data(mt5_sym, ui_sym, TIMEFRAME)
    await websocket.send(json.dumps({
        "type": "hello",
        "symbol": ui_sym,
        "mt5_symbol": mt5_sym,
        "timeframe": TIMEFRAME,
        "prev_close": data["prev_close"],
    }))
    bars = [bar_to_msg(ui_sym, TIMEFRAME, row) for row in data["m1"]]
    if not bars:
        await websocket.send(json.dumps({"type": "bars", "symbol": ui_sym, "bars": [], "more": False}))
    for i in range(0, len(bars), 2000):
        chunk = bars[i:i + 2000]
        await websocket.send(json.dumps({"type": "bars", "symbol": ui_sym, "bars": chunk, "more": (i + 2000) < len(bars)}, default=str))
    if data["d1"]:
        await websocket.send(json.dumps({"type": "d1_bars", "symbol": ui_sym, "bars": data["d1"]}, default=str))
    t_msg = tick_msg(mt5_sym, ui_sym)
    if t_msg:
        await websocket.send(json.dumps(t_msg, default=str))
    if ABC_AVAILABLE and DETECTOR is not None:
        bars_snapshot = fetch_bars(mt5_sym, TIMEFRAME, HISTORY_COUNT)
        pat = detect_pattern(DETECTOR, bars_snapshot, mt5_sym, TIMEFRAME)
        if pat:
            pat["symbol"] = ui_sym
            await websocket.send(json.dumps(pat))
    open_msgs, open_ids = open_positions_msgs(ui_sym, mt5_sym, MAGIC)
    closed_msgs = closed_deals_msgs(ui_sym, mt5_sym, MAGIC, set(), open_pos_ids=open_ids, days=180)
    all_trades = closed_msgs + open_msgs
    await websocket.send(json.dumps({"type": "trades", "symbol": ui_sym, "trades": all_trades}, default=str))


async def handler(websocket, *_args) -> None:
    req_path = extract_req_path(websocket, _args)
    req_sym = parse_symbol_from_path(req_path) or SYMBOL
    ui_sym, mt5_sym = resolve_mt5_symbol(req_sym)
    CLIENT_SUBS[websocket] = (ui_sym, mt5_sym)
    log(f"cliente conectado -> {ui_sym} ({mt5_sym}) · {len(CLIENT_SUBS)} ativo(s)")
    try:
        await send_initial_snapshot(websocket, ui_sym, mt5_sym)
        async for raw in websocket:
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            if isinstance(msg, dict) and msg.get("type") == "subscribe" and msg.get("symbol"):
                new_ui, new_mt5 = resolve_mt5_symbol(str(msg["symbol"]))
                CLIENT_SUBS[websocket] = (new_ui, new_mt5)
                log(f"cliente alterou inscrição -> {new_ui} ({new_mt5})")
                await send_initial_snapshot(websocket, new_ui, new_mt5)
    except websockets.ConnectionClosed:
        pass
    finally:
        CLIENT_SUBS.pop(websocket, None)
        log(f"cliente desconectado ({len(CLIENT_SUBS)} ativo(s))")


async def poll_loop(timeframe: str, history: int, interval: float, magic: int, detector) -> None:
    tf = TIMEFRAMES[timeframe.upper()]
    sym_states: dict[str, dict] = {}
    while True:
        try:
            active_subs = set(CLIENT_SUBS.values())
            for ui_sym, mt5_sym in active_subs:
                st = sym_states.setdefault(mt5_sym, {
                    "last_closed": None,
                    "last_forming": None,
                    "last_pattern_key": None,
                    "sent_deals": set(),
                    "cum_buy": 0.0,
                    "cum_sell": 0.0,
                    "last_delta_msc": None,
                })
                mt5.symbol_select(mt5_sym, True)

                t_msg = tick_msg(mt5_sym, ui_sym)
                if t_msg:
                    await broadcast_for_symbol(ui_sym, mt5_sym, t_msg)

                b, s = recent_tick_delta(mt5_sym, st)
                if b or s:
                    st["cum_buy"] += b
                    st["cum_sell"] += s
                    await broadcast_for_symbol(ui_sym, mt5_sym, {
                        "type": "delta",
                        "symbol": ui_sym,
                        "buy": round(st["cum_buy"]),
                        "sell": round(st["cum_sell"]),
                        "net": round(st["cum_buy"] - st["cum_sell"]),
                    })

                rates = mt5.copy_rates_from_pos(mt5_sym, tf, 0, 2)
                if rates is not None and len(rates) >= 2:
                    closed = rates[-2]
                    closed_key = int(closed["time"])
                    if closed_key != st["last_closed"]:
                        st["last_closed"] = closed_key
                        plain = rate_to_plain(closed)
                        cache = HISTORIES.get((mt5_sym, timeframe))
                        if cache:
                            cache["m1"].append(plain)
                        await broadcast_for_symbol(ui_sym, mt5_sym, bar_to_msg(ui_sym, timeframe, plain))
                        if detector is not None:
                            bars = fetch_bars(mt5_sym, timeframe, history)
                            pat = detect_pattern(detector, bars, mt5_sym, timeframe)
                            if pat and pat["key"] != st["last_pattern_key"]:
                                st["last_pattern_key"] = pat["key"]
                                pat["symbol"] = ui_sym
                                await broadcast_for_symbol(ui_sym, mt5_sym, pat)

                if rates is not None and len(rates) >= 1:
                    forming = rates[-1]
                    f_sig = (
                        int(forming["time"]),
                        float(forming["close"]),
                        float(forming["high"]),
                        float(forming["low"]),
                        int(forming["real_volume"]) or int(forming["tick_volume"]),
                    )
                    if f_sig != st["last_forming"]:
                        st["last_forming"] = f_sig
                        f_msg = bar_to_msg(ui_sym, timeframe, rate_to_plain(forming))
                        f_msg["forming"] = True
                        await broadcast_for_symbol(ui_sym, mt5_sym, f_msg)

                open_msgs, open_ids = open_positions_msgs(ui_sym, mt5_sym, magic)
                prev_open = st.setdefault("open_sigs", {})
                for m in open_msgs:
                    sig = (m["id"], round(m.get("profit") or 0.0, 2))
                    if prev_open.get(m["id"]) != sig:
                        prev_open[m["id"]] = sig
                        await broadcast_for_symbol(ui_sym, mt5_sym, m)
                for stale_id in [k for k in list(prev_open) if k not in {m["id"] for m in open_msgs}]:
                    prev_open.pop(stale_id, None)
                for m in closed_deals_msgs(ui_sym, mt5_sym, magic, st["sent_deals"], open_pos_ids=open_ids, days=7):
                    await broadcast_for_symbol(ui_sym, mt5_sym, m)
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
    p.add_argument("--history-days", type=int, default=int(os.environ.get("CV_HISTORY_DAYS", "30")), help="dias de histórico a carregar (padrão 30)")
    p.add_argument("--refresh", action="store_true", help="força novo download do histórico (ignora cache)")
    p.add_argument("--interval", type=float, default=0.5)
    p.add_argument("--config", default=str(DEFAULT_CONFIG), help="JSON de config do robô (detector + magic)")
    p.add_argument("--magic", type=int, default=None, help="Magic a filtrar (padrão: execution.magic do config)")
    p.add_argument("--mt5-path", default=os.environ.get("CV_MT5_PATH", r"C:\Program Files\MetaTrader 5\terminal64.exe"))
    p.add_argument("--login", type=int, default=None)
    p.add_argument("--password", default=None)
    p.add_argument("--server", default=None)
    return p


async def main() -> None:
    global SYMBOL, TIMEFRAME, MAGIC, DETECTOR, HISTORY_COUNT, HISTORY_DAYS
    args = build_parser().parse_args()
    timeframe = args.timeframe.upper()
    if timeframe not in TIMEFRAMES:
        log(f"timeframe inválido: {args.timeframe}")
        sys.exit(2)

    config_path = Path(args.config)
    detector = None
    magic = args.magic
    if config_path.exists():
        if ABC_AVAILABLE:
            detector = build_detector(config_path)
            log(f"detector ABC carregado do config: {config_path.name}")
    else:
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

    ui_sym, mt5_sym = resolve_mt5_symbol(args.symbol)
    log(f"MT5 conectado | símbolo padrão={ui_sym} ({mt5_sym}) timeframe={timeframe} magic={'todos' if magic is None else magic}")

    for s in ["WINZ26", "WDOZ26", "IBOV", "PETR4", "VALE3", "ITUB4", "BBDC4"]:
        mt5.symbol_select(s, True)

    SYMBOL = mt5_sym
    TIMEFRAME = timeframe
    DETECTOR = detector
    HISTORY_COUNT = args.history
    HISTORY_DAYS = args.history_days

    get_symbol_data(mt5_sym, ui_sym, timeframe, refresh=args.refresh)

    async with websockets.serve(handler, args.host, args.port):
        log(f"ouvindo em ws://{args.host}:{args.port}/ws (multi-ativo dinâmico)")
        await poll_loop(timeframe, args.history, args.interval, magic, detector)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        mt5.shutdown()
        log("encerrado")
