# Bot de tendência BTC-USDC (Coinbase Advanced)

Estratégia long-only: compra BTC quando a EMA rápida cruza acima da EMA lenta (com filtro opcional de EMA200) e volta para USDC quando cruza abaixo. Decide só no fechamento do candle. **Modo padrão: SIMULAÇÃO**, sem enviar nenhuma ordem.

## Arquivos
| Arquivo | Função |
|---|---|
| `backtest.py` | Baixa ~4 anos de candles da Coinbase e testa os parâmetros (treino 70% / teste 30%), com taxa de 0,10% + slippage de 0,05% |
| `bot.py` | Loop 24h: simulação (padrão) ou modo real com travas |
| `strategy.py` | Regra da estratégia (usada pelo backtest E pelo bot, então os dois seguem a mesma regra) |
| `data_source.py` | Candles públicos da Coinbase (sem chave) |

## Passo 1: rodar o backtest com dados reais (obrigatório)
No servidor (console do app no Easypanel, ou qualquer máquina com Python 3.12):
```bash
pip install -r requirements.txt
python backtest.py              # 4h, 4 anos
python backtest.py --tf 1d --days 2500
```
Ele imprime os melhores parâmetros no treino, o resultado no período de teste e um veredito com 5 checagens. **Se der "NÃO PASSOU", não vá para o modo real.**

## Passo 2: deploy no Easypanel (simulação)
1. Suba esta pasta para um repositório GitHub privado.
2. No Easypanel, crie um **App** → Source: GitHub → Build: **Dockerfile**.
3. Em **Mounts**, crie um volume montado em `/data`, para guardar o estado entre reinícios.
4. Em **Environment**, preencha com os parâmetros escolhidos no backtest:
```
PRODUCT=BTC-USDC
TF=4h
FAST=50
SLOW=150
TREND=0
PAPER_CAPITAL=1000
TELEGRAM_TOKEN=        # opcional
TELEGRAM_CHAT_ID=      # opcional
```
5. Faça o deploy e acompanhe pelos **Logs**.

O histórico das operações simuladas fica em `/data/trades.csv`, e o patrimônio por candle fica em `/data/equity.csv` (e no log).

### Painel web (opcional)
Mostra posição, preço, patrimônio simulado contra o "comprar e segurar", distância entre as EMAs, gráfico e operações. É só leitura: não envia ordens.
1. Em **Environment**, adicione `PANEL_PASSWORD=uma-senha-forte`. Sem essa variável, o painel não liga.
2. Em **Domains**, adicione um domínio apontando para a **porta 8080** do app (o Easypanel cuida do HTTPS).
3. Faça o deploy e abra o domínio. Usuário: `admin`, senha: a que você definiu.

O gráfico começa a ser preenchido a partir do primeiro candle depois da atualização.

## Passo 3 (só depois de semanas em simulação): modo real
Exige **todas** estas variáveis. Sem qualquer uma delas, o bot aborta:
```
LIVE=1
CONFIRM_LIVE=SIM_EU_ENTENDO_O_RISCO
LIVE_MAX_USDC=500
COINBASE_API_KEY=organizations/.../apiKeys/...
COINBASE_API_SECRET=-----BEGIN EC PRIVATE KEY-----...
```
Crie a chave em coinbase.com/settings/api (CDP) só com permissão **View + Trade**, **sem Transfer/Withdraw**, e restrita ao IP do seu servidor. Ao trocar de simulação para real, apague o `/data/state.json` (o bot se recusa a ligar com o estado do outro modo).

No modo real o bot trabalha com uma "subconta" própria: começa com até `LIVE_MAX_USDC` e **só vende o BTC que ele mesmo comprou**. O resto do saldo da conta não é tocado. O ideal é usar um portfólio separado na Coinbase mesmo assim.

## Limitações conhecidas
- Spot e long-only: em mercado de baixa, o melhor que o bot faz é ficar em USDC.
- Ordem a mercado: o slippage real pode ser maior em momentos de pânico.
- Backtest bom não garante resultado futuro, e um único período de teste pode passar por sorte.
