# CUMES & VALES — Caderno de Pesquisa

> Ideias abertas para exploração posterior. Nada aqui é afirmação de ganho —
> são hipóteses a validar com dado real.

## 1. Hipótese principal: Fibonacci no eixo de VOLUME (planos de corte)

**Pergunta:** a distribuição de volume financeiro ao longo do preço respeita
proporções áureas (Φ ≈ 1.618 e 1/Φ ≈ 0.618)?

**O que já existe na ferramenta:** os "planos de corte" do Fibonacci aplicados ao
eixo Z (volume). O usuário ancora dois pontos (ex: vale = baixo volume, cume =
alto volume) e a ferramenta projeta planos horizontais em 38.2%, 50%, 61.8%,
100%, 127.2% e 161.8% da faixa de volume.

**Por que pode haver algo aqui:**
- Volume é uma medida de "esforço" (esforço vs. resultado, lei de Wyckoff).
- Se zonas de exaustão/defesa se formam em níveis proporcionais ao pico de
  volume, os cortes áureos marcariam *onde* o volume "vira" (fim da defesa,
  início da distribuição).
- O projeto já calcula VWSD (desvio do volume) e skewness — dá para cruzar com
  os níveis áureos e testar se a densidade de volume se concentra neles.

**Como validar (futuro):**
1. Para cada dia, normalizar o volume por preço (buckets de 50 pts).
2. Calcular a distribuição empírica do volume e comparar com os níveis 0.382 /
   0.5 / 0.618 / 1.618.
3. Testar se há acúmulo estatisticamente significativo de volume *próximo* aos
   níveis áureos (janela ±x pts) versus o que seria esperado ao acaso.
4. Se houver, testar poder preditivo: o preço "respeita" (reverte) ou "rompe"
   esses níveis com mais frequência que o baseline?

## 2. Hipóteses secundárias

- **POC como equilíbrio:** a POC (maior volume) age como ímã ou barreira?
  Medir taxa de rejeição/aceitação do preço ao cruzar a POC.
- **Zonas de liquidez por hora-do-dia:** o mapa cíclico revela nós de volume em
  horários específicos (abertura/fechamento). Testar se esses nós se comportam
  como suporte/resistência intradiários recorrentes.
- **Absorção real vs. proxy:** o delta por agressor (tick) é muito mais fiel que
  o proxy OHLC (sinal do candle). Validar se o proxy se aproxima do real o
  suficiente para uso sem tick.

## 3. Dados necessários

- **Tick com lado agressor** (MT5 `copy_ticks` com flags TICK_FLAG_BUY/SELL) —
  para delta/CVD reais.
- **Histórico 6+ meses** de WIN/WDO (já suportado pela ponte com cache).

## 4. Próximos passos sugeridos (ordem)

1. Implementar delta real por tick na ponte e comparar com o proxy.
2. Rodar a validação estatística do item 1 num notebook (Python/pandas) reusando
   o CSV exportado do MT5. *(Concluído via `validar_fibonacci_volume.py`)*
3. Se a hipótese áurea sobreviver, transformar os "planos de corte" em um
   indicador com nível de confiança (não só visual). *(Implementado no HUD: `Aderência Φ (Volume)`)*

## 5. Resultados Empíricos (18/11/2026 — `validar_fibonacci_volume.py`)

Execução causal (sem *look-ahead* após os primeiros 60 min do pregão, horizonte de 15 min) sobre os 7 ativos B3 (`amostra_*_m1.csv`, bandas áureas `0.382 / 0.500 / 0.618 ± 0.035`, baseline uniforme = `21.0%`):

| Ativo | Dias | Buckets | Freq Φ | Enriq. | Vol Φ | Vol Preço Φ (`H-L`) | Rev Φ (15m) | Rev Ctrl (15m) | Rej POC (15m) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| **WIN** | 69 | 4.018 | 14.1% | 0.67x | 31.3% | **1.15x** | 47.6% | 46.7% | 45.6% |
| **WDO** | 34 | 752 | 18.1% | 0.86x | 26.9% | **1.63x** | 41.6% | 42.9% | 41.6% |
| **IND** | 124 | 6.789 | 14.8% | 0.70x | 25.2% | **1.18x** | 45.2% | 47.6% | 46.6% |
| **PETR4** | 124 | 2.237 | 12.1% | 0.57x | 22.1% | **1.24x** | 38.5% | 38.1% | 37.3% |
| **VALE3** | 124 | 3.133 | 9.0% | 0.43x | 20.7% | **1.07x** | 43.5% | 42.6% | 41.8% |
| **ITUB4** | 124 | 2.104 | 8.8% | 0.42x | 18.8% | **1.06x** | 38.6% | 38.6% | 37.8% |
| **BBDC4** | 124 | 981 | 13.3% | 0.63x | 23.3% | **1.07x** | 27.3% | 26.5% | 22.9% |

**Achados objetivos:**
- **Distribuição de Volume (`Vol/MaxVol`):** A frequência de faixas nas proporções áureas fica abaixo do uniforme (`8.8%–18.1%`, $p < 0.05$ em todos os ativos) porque o perfil de volume é fortemente assimétrico (muitas faixas de baixo volume vs. pico único da POC). Contudo, no **WIN** e **WDO**, os níveis nessas bandas acumulam `31.3%` e `26.9%` do volume não-POC.
- **Poder preditivo isolado (eixo Z):** A taxa de reversão em 15 minutos ao tocar cortes áureos de volume (`Rev Φ`) é indistinguível do controle (`Rev Ctrl`) — ex. WIN `47.6%` vs `46.7%`, WDO `41.6%` vs `42.9%`. Não há vantagem direcional isolada sem filtro de contexto/agressão.
- **Concentração no eixo Preço (`H-L`):** Todos os 7 ativos concentram mais volume na região áurea da amplitude diária (`1.06x–1.63x`, destaque para **WDO 1.63x** e **PETR4 1.24x**), consistente com a *Value Area* central do Market Profile.
- **POC como equilíbrio:** A rejeição em 15m na POC (`22.9%–46.6%`) confirma que a POC atua primariamente como zona de equilíbrio/ímã de preço.
