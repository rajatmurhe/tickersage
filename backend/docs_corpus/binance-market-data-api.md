---
title: Binance spot market data endpoints
url: https://developers.binance.com/docs/binance-spot-api-docs/rest-api/market-data-endpoints
---
Binance publishes read-only market data endpoints that need no API key. The 24-hour ticker endpoint (/api/v3/ticker/24hr) returns the last price, the price change and percent change over a rolling 24-hour window, the 24-hour high and low, base asset volume and quote asset volume.

The klines endpoint (/api/v3/klines) returns candlesticks for a symbol and interval, each with open time, open, high, low, close, volume, close time and quote asset volume. The depth endpoint (/api/v3/depth) returns current bids and asks at each price level. The exchange information endpoint (/api/v3/exchangeInfo) lists every trading pair and its status.

The 24-hour statistics are a rolling window ending at the time of the request, not a calendar day.
