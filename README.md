# CUMES E VALES — Topografia de Liquidez (WIN · B3)

Aplicação web estática (Three.js + Tailwind via CDN) para análise topográfica do
fluxo financeiro e telemetria operacional do EA de Mini Índice (WIN) baseado em
ondas ABC. Roda 100% no navegador e publica direto no **GitHub Pages**.

## O que mostra

- **Terreno 3D** `(tempo × faixa de preço × volume financeiro)` — o "mapa" de
  onde a liquidez institucional se acumula durante o pregão.
- **Ondas ABC** do setup, sobrepostas ao relevo: verde neon (`#00FF88`) para
  Gain, vermelho coral (`#FF3366`) para Stop, cinza tracejado para filtradas.
- **Telemetria do trade** ("rastro dourado"): seta de entrada, pulso dourado
  enquanto a operação está aberta, linha dourada seguindo a topografia e
  pegadas nas barras intermediárias até a consolidação na saída.
- **3 fontes de dados**: demonstração sintética, upload de CSV (backtest) e
  tempo real via WebSocket.

## Publicar no GitHub Pages

1. Faça commit desta pasta no repositório.
2. No GitHub, vá em **Settings → Pages**.
3. Em *Source*, escolha `Deploy from a branch` → branch `master` → pasta `/ (root)`.
4. Salve. A app fica em:

   ```
   https://<usuario>.github.io/<repositorio>/cumes-vales/
   ```

   (ex.: `https://kleberherlon.github.io/mt5-binhardi-ea/cumes-vales/`)

Não há etapa de build — os arquivos são servidos como estão.

## Modo Demonstração (padrão)

Abre imediatamente ao carregar a página, simulando pregão, montanhas de volume,
ondas ABC e trades ao vivo. Útil para visualização rápida.

## Modo Backtest (CSV)

Arraste um ou mais `.csv` para o painel (ou use o botão "Parâmetros" → zona de
arrastar). O formato é **autodetectado** pelo cabeçalho:

1. **Candles OHLCV** — colunas `time,open,high,low,close,volume` (aceita também
   `tick_volume`/`real_volume`, `date`/`datetime` no lugar de `time`).
2. **Pivôs ABC** — colunas `a_time,a_price,b_time,b_price,c_time,c_price` +
   `scenario,direction,result`.
3. **Trades** — dois formatos já usados pelo `abc-trader`:
   - `backtest_2026_WIN_M1.csv`: `symbol,timeframe,scenario,direction,entry_time,exit_time,entry,stop,target,exit_price,exit_reason,result_r,...`
   - `historico_operacoes.csv` (separador `;`): `status;abertura;fechamento;...;lado;...;entrada;stop;alvo;saida;resultado;...`

Tudo é processado no navegador (client-side) — nenhum dado sensível sai da sua
máquina.

## Modo Tempo Real (Bridge Local)

O GitHub Pages roda em HTTPS, então o navegador **bloqueia `ws://`** (Mixed
Content). Para usar ao vivo:

1. Instale as dependências e rode a ponte (o terminal MT5 precisa estar aberto
   e logado na conta):

   ```powershell
   pip install MetaTrader5 websockets
   python bridge_mt5.py --symbol WINZ26 --timeframe M1 --port 8000
   ```

   A ponte faz a **cobertura ao vivo completa**:
   - transmite candles/ticks em tempo real;
   - calcula as **ondas ABC com os mesmos requisitos do robô
     Piolho Agressivo** (usa o `config_piolho_agressivo.json` + o detector
     `abc_trader.pattern`), emitindo só os padrões válidos;
   - lê as **operações reais do MT5** (posições abertas + deals encerrados,
     filtrados por `magic`), alimentando o rastro dourado.

   Ajuste o robô com `--config` (padrão `../abc-trader/config_piolho_agressivo.json`)
   e o `--magic` (padrão: `execution.magic` do config).

2. **Local** (para testar abrindo `index.html` direto do disco):
   no campo *WebSocket* use `ws://localhost:8000/ws`.

3. **Via GitHub Pages** (HTTPS): abra um túnel seguro e use `wss://` no HUD:

   ```powershell
   cloudflared tunnel --url http://localhost:8000
   # ou
   ngrok http 8000
   ```

   Copie a URL `https://...` gerada e cole no campo *WebSocket* trocando
   `https` por `wss` (ex.: `wss://xyz.trycloudflare.com/ws`).

## Parâmetros (painel "Parâmetros")

- **Símbolo / Timeframe** — WIN, WDO ou outro; M1/M5/M15.
- **Sessão** — janela do pregão (padrão 09:00–18:00), editável.
- **Faixa de preço** (degraus do Volume Profile, padrão 100 pts) e **bucket de
  tempo** (padrão 15 min).
- **Fuso** — deslocamento vs UTC para converter timestamps (B3 = `-3`).
- **Render** — malha suavizada ou barras 3D instanciadas.
- **Histórico** — número de barras (demo) / profundidade da ponte.

## Estrutura

```
cumes-vales/
  index.html      # app autocontido (Three.js + Tailwind via CDN): terreno 3D,
                  # heatmap, ondas ABC, telemetria "rastro dourado" e 3 fontes
  bridge_mt5.py   # ponte WebSocket MT5 -> JSON
  README.md
```

Todo o motor 3D (HTML/CSS/JS) vive dentro do `index.html`, sem etapa de build —
basta abrir no navegador ou publicar no GitHub Pages.
