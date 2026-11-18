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
   o CSV exportado do MT5.
3. Se a hipótese áurea sobreviver, transformar os "planos de corte" em um
   indicador com nível de confiança (não só visual).
