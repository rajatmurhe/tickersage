---
title: Binance market data only URLs
url: https://github.com/binance/binance-spot-api-docs/blob/master/faqs/market_data_only.md
license: No license file; public API documentation from github.com/binance/binance-spot-api-docs, used for reference with attribution (Binance terms: https://www.binance.com/en/terms)
format: markdown
fetched_at: 2026-10-20T07:37:53Z
sha256: eabcec9c9739c1ba9da66e2279b1bad64cebe53e38512892399b4982cee29867
---
# Market Data Only URLs

These URLs do not require any authentication (i.e. The API key is not necessary) and serve only
public market data.

### RESTful API

On the RESTful API, these are the endpoints you can request on `data-api.binance.vision`:

- GET /api/v3/aggTrades
- GET /api/v3/avgPrice
- GET /api/v3/depth
- GET /api/v3/exchangeInfo
- GET /api/v3/klines
- GET /api/v3/ping
- GET /api/v3/ticker
- GET /api/v3/ticker/24hr
- GET /api/v3/ticker/bookTicker
- GET /api/v3/ticker/price
- GET /api/v3/time
- GET /api/v3/trades
- GET /api/v3/uiKlines

Sample request:

```
curl -sX GET "https://data-api.binance.vision/api/v3/exchangeInfo?symbol=BTCUSDT"
```

### Websocket Streams

Public market data can also be retrieved through the websocket market data using the URL
`data-stream.binance.vision`. The streams available through this domain are the same that can be
found in the WebSocket Market Streams documentation.

Note that User Data Streams **cannot** be accessed through this URL.

Sample request:

```
wss://data-stream.binance.vision:443/ws/btcusdt@kline_1m
```
