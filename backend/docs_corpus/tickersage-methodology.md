---
title: How TickerSage computes its metrics
url: https://en.wikipedia.org/wiki/Rate_of_return
---
TickerSage calculates every figure in Python from exchange candles; the language model only explains them. For a 24-hour window it uses 24 hourly candles, for 7 days 42 four-hour candles, and for 30 days 30 daily candles.

Return is the last close divided by the first candle's open, minus one. Volatility is the standard deviation of log returns between consecutive candles, and annualized volatility scales that by the square root of the number of candles per year. RSI uses 14 periods with Wilder smoothing. VWAP uses each candle's typical price weighted by volume. Maximum drawdown is the largest decline from a running peak of closing prices within the window.
