---
title: News sentiment scoring (VADER)
url: https://github.com/cjhutto/vaderSentiment
---
TickerSage scores recent headlines with VADER, a rule-based sentiment model that combines a word lexicon with rules for punctuation, capitalisation and negation. Each headline gets a compound score from -1 (most negative) to +1 (most positive).

Headlines with a compound score of 0.05 or more are labelled positive, -0.05 or less negative, and anything in between neutral. Headline sentiment is a rough summary of tone in recent coverage. It does not prove why a price moved, and correlation between news tone and price moves on a given day is not causation.
