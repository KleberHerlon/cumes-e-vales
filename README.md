# CUMES E VALES — Topografia de Liquidez (WIN · B3)

Visualizador topográfico 3D de fluxo financeiro e telemetria operacional do EA
de Mini Índice (WIN) baseado em ondas ABC. Roda 100% no navegador (Three.js +
Tailwind via CDN), sem build — publicado direto no GitHub Pages.

Acesso: **https://kleberherlon.github.io/cumes-e-vales/**

## O que mostra

- **Relevo topográfico** `(tempo do pregão × faixa de preço × volume financeiro)`:
  montanhas/vales de liquidez com heatmap térmico (azul → verde/amarelo → vermelho).
- **Ondas ABC** (douradas) do setup, sobrepostas ao relevo.
- **Telemetria das operações** — "rastro dourado": seta de entrada, pulso enquanto
  aberta, linha dourada seguindo a topografia e pegadas até a saída.

## Modos de operação

- **Simulação** (padrão) — pregões sintéticos, abre direto.
- **Carregar CSV** — arraste os `amostra_*.csv` (ou exportações do MT5). O formato
  é autodetectado: candles OHLCV, pivôs ABC ou trades (backtest).
- **Tempo Real** — ponte WebSocket local (ver abaixo).

## Controles

- **Período** (Dia / Semana / Mês) — janela de dias sobreposta no mapa.
- **Zoom Temporal** (30m → 15m → 10m → 5m → 1m) — quebra o pregão em blocos menores.
- **Filtro de horário** (eixo X) e **faixa de preço** (eixo Y).
- **Inclinação do preço** (0–45°) — inclina o preço no eixo Y.
- Visões de câmera: Isométrica / Topo (heatmap 2D) / Perfil (volume profile).

## Tempo Real (Bridge Local)

O GitHub Pages roda em HTTPS, então o navegador bloqueia `ws://`. Para usar ao vivo:

```powershell
pip install MetaTrader5 websockets
python bridge_mt5.py --symbol WINZ26 --timeframe M1 --port 8000
```

Depois abra um túnel seguro e use `wss://` no campo WebSocket do HUD:

```powershell
cloudflared tunnel --url http://localhost:8000
# ou
ngrok http 8000
```

> Nota: a detecção de ondas ABC com os requisitos do robô usa o pacote
> `abc_trader` (mantido em repositório privado). Sem ele, a ponte transmite
> candles/ticks e as operações reais do MT5 normalmente.

## Arquivos

```
index.html               # app autocontido (Three.js + Tailwind via CDN)
bridge_mt5.py            # ponte WebSocket MT5 -> JSON
amostra_win_m1.csv       # 3 dias de WIN M1 (OHLCV) de exemplo
amostra_trades.csv       # trades de exemplo (formato backtest)
```
