# Why-it-moved evidence pack: offline before and after

_2026-10-19, main 110-case set, offline pipeline (rules router + template answers), replayed tools. The held-out sets were not used for any of this; v1 and v2 were re-run only to check nothing broke (both still 100%, gates pass). v3 was not run._

The explain_move plan now calls `attribute_move` as well, and the scorer has a new "no unsupported cause" check (`evals/causes.py`): an answer fails if it names a driver category the pack didn't flag, or leaves out "no clear driver found" when the pack sets `no_clear_driver`. explain_move cases must also quote the pack's market-implied part and coin-specific residual (when the pack has them; BTC is the benchmark, so its move isn't split).

| | Before (`90fb87c0`) | After (`e4a98309`) |
|---|---|---|
| Pass rate, all 110 cases | 100.0% | 100.0% |
| explain_move | 15 / 15 | 15 / 15 |
| Numbers checked (explain_move) | 1,034 (422) | 1,119 (488) |
| Number faithfulness | 100.0% | 100.0% |
| Key numbers present | 100.0% | 100.0% |
| No unsupported cause (19 answers with a pack) | n/a | 100.0% |

No case outside explain_move changed its pass/fail result, status or failure list. Two broken-tools cases list the explain plan's tools explicitly, so `attribute_move` was added to their expected tool list.

What the packs found on the 2026-10-18 16:27 UTC snapshot (a quiet Sunday): 12 of the 14 explain answers with a pack say "no clear driver found" (5 of them because the move was under the 0.5% floor). Two flag drivers: BTC over 7 days (order book tilted toward asks, -47.38%; news tone shifted -0.111) and LINK over 7 days (news tone shifted -0.145). Volume was below its trailing average in every explain case.

Offline answers are templates that only name flagged drivers, so 100% on the new check is expected here; the check matters for live runs, where the model writes the answer. The live run is still to do.

Fixtures: 39 new `attribute_move` fixtures (13 assets x 3 windows). They hold only the pack's own inputs (daily candles, funding history) fetched up to each asset's existing metrics snapshot, so they line up with the fixtures recorded on 2026-10-18; the metrics, order book and news parts are read from the existing fixtures. No existing fixture changed.
