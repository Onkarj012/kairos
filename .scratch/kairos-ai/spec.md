# Kairos AI: the plan

Status: ready-for-agent (owner authorized execution on 2026-11-15; tickets 30 and 32-34 remain ready-for-human)
Date: 2026-11-14 IST
Written from: Onkar's own account of what he wants (`/tmp/kairos-research/owner-vision.md`), six Luna research reports (`/tmp/kairos-research/out-*.md`), the earlier audits, and checks against the code and data.
Relation to Kairos v2: this plan replaces its strategy section (4.3), target-portfolio/default-ETF mandate, and heavy gate process. Adopted execution, risk, and operations requirements are summarized in subordinate `.scratch/kairos-ai/retained-system-requirements.md`; this spec and the decisions log prevail where they differ.

Words in **bold** the first time they appear are explained in the glossary at the end.

---

## 1. What we are making

Two things, built on one shared base so that they can become one system if the research works.

**A. The daily picks system (the one that should make money).**
Every trading day before 09:00 it publishes a short list:
- for a new buy: the price range to buy in, how many shares, the target price, the **stop-loss**, and how many days it expects the trade to take;
- for a stock already held: hold or sell, and if sell, in what price range and by when.
It paper trades these by itself first. Real money comes later, starting from Rs 10,000.

**B. The ripple network (the research idea).**
A model that treats the market as a web: stocks linked to their industry, their index, their business group, futures, and overseas markets. When one part moves, the model estimates how the linked parts will move next. We test honestly whether the links add anything.

**How they fit together.** Both use the same data, the same test method, and the same picks engine. The ripple model is one more candidate model. If it beats the others on the blind test, it becomes the brain of the picks system. If it does not, the picks system runs on whichever model did best, and the ripple work is written up as research.

## 2. The outcome we are aiming for

Success is measured in money and honesty, in this order:

1. **It trains.** A working deep learning model exists within the first two weeks of work. This has never happened in five or six attempts, so it is the first goal.
2. **It is honest.** Every number we quote comes from data the model never saw: the whole of 2025 and 2026.
3. **It beats simple things.** On those blind years the model's picks make more, after all costs, than (a) picking at random, (b) two simple rules, and (c) just holding a Nifty index fund.
4. **It survives paper trading.** Live paper results match what the blind test predicted.
5. **It makes money with real capital,** starting small and growing only on evidence.

What would count as a good result, stated before we start so we cannot move the goalposts:

| Measure | Realistic good result | Why not higher |
|---|---|---|
| Next-day up/down accuracy, all stocks | 52 to 55% | Careful published studies on actively traded stocks land a little above 50% |
| Accuracy on the most confident tenth of predictions | 56 to 62% | Confidence filtering helps, but less than older papers report |
| Average gain per trade after costs | Above zero, ideally 0.3 to 1% | Costs are 0.2 to 0.5% per completed trade in India |
| Result against holding an index fund | Ahead over the two blind years | Otherwise the fund is the better choice |

**About the 75 to 80% figure from the published paper (reviewed 2026-11-14 from the project files on the Mac; full report in `/tmp/kairos-research/out-major-pro.md`).**

| Confidence cut-off | Right on test stocks (2024) | Share of predictions kept |
|---|---|---|
| none | 68.7% | 100% |
| 60% | 75.6% | 66% |
| 65% | 76.0% | 60% |
| 70% | 77.9% | 48% |
| 75% | 82.9% | 34% |

These numbers are real and reproducible from the saved run, but they measure something easier than "will the stock close higher tomorrow":
- **The thing being predicted was a smoothed trend, not the raw next-day move.** The code averages the future move with its neighbours using a window centred on the day. For the one-day case that average appears to include the move that has already happened today, which the model can see in its inputs. This reading comes from the agent's code review and is the first thing to confirm in the rebuild.
- **The paper itself reports about 50.2% on the raw next-day direction**, which is a coin toss.
- **The paper's own trading test was mixed:** +11.5% in 2025 (85 trades), -3.6% in January to June 2026 (40 trades), and -64.3% when re-run year by year over 2021 to 2026.
- **Two smaller leaks:** each stock's fixed traits (how jumpy it is, how closely it follows the market) were worked out from its whole history, including the future; and gaps in market inputs were filled from later dates.
- **The confidence output was never trained.** Its weight in training was set to zero. The "confidence" used for the cut-offs is simply how far the direction output is from 50/50. Only direction and size were trained, although the paper lists nine training goals.

What is good and carries over: the stock and date separation was done properly (test stocks were never trained on, with a 10-day gap), scaling used training data only, costs were included, and the losing 2026 result was reported honestly. The model code, the news scripts and the saved run can be reused.

What this means for targets: the table above stays as written. The rebuild (model 2) is tested two ways, on the raw next-day move and on a multi-day trend built only from future prices with no overlap with today. A multi-day trend is a sensible thing to predict for swing trades, and it may well be more predictable than one day; it just has to be scored honestly.

One caution: the original design was already run on 2025 and early 2026 in the paper. Its design choices have therefore seen those years once. The rebuilt five-encoder model keeps the paper's settings fixed for that reason, and its blind result is read with that in mind.

## 3. What we learned from the research

**Your earlier attempts.**
- No deep learning model was ever written. `meridian/models/stockxpert_v3.py` is a 20-line placeholder that refuses to run "until earlier stages pass".
- The only models trained were simple ones (LightGBM and linear) predicting the Nifty future 15 and 60 minutes ahead. They were barely better than guessing: about 37% right on a three-way up/flat/down call where chance is 33%. Predicting the size of the move was no better than predicting "no change".
- The work changed target several times (futures rules, intraday prediction, graph inputs, daily stocks) and each change kept the old gates and added new ones.
- Your ripple idea was stopped before any model was built because a few overseas data series were 97% complete against a 98% requirement.

**The data we have.**
- Daily prices: good enough to start today. 2.66 million rows for 3,172 stocks from October 2019 to December 2024, already adjusted for splits and bonuses. 2,166 stocks have at least two years of history.
- Minute-by-minute stock prices: none on this Linux machine, but the Mac has them (found 2026-11-14): one-minute prices for 531 stocks from February 2015 to June 2026, 22 GB, plus the Nifty 50 index by the minute from 2015. This is the largest data find so far.
- News: timestamps for 24,940 exchange announcements covering just 23 companies, with no text.
- Links between stocks: index membership snapshots and industry labels, both partial. No business-group or supplier links.
- Overseas markets: seven daily series (US futures, Nikkei, Hang Seng, Kospi, Brent crude, dollar-rupee), about 1,700 days each. Usable.
- Missing: Indian index levels, futures and options history, company financials, delivered-volume figures.

**The ripple idea in published research.**
- There is real evidence that information spreads between linked stocks: big firms lead smaller ones in the same industry, and customers lead their suppliers, over days to weeks. Most of it is older US data. Indian evidence is thin.
- Several published models built on links report gains, each on its own chosen data. Nobody has shown a fair, cost-included test where they beat strong simple models, and none was tested on Indian stocks.
- The decisive test is simple: the same model with true links, with no links, and with links shuffled at random.

**From predictions to trades.**
- Next-day up or down is the easiest thing to score but the least useful to trade: it gives no buy range, target or holding time.
- A more useful target is "will this stock reach its target before its stop-loss within N days", plus the expected high and low. That maps directly onto the picks you described.

## 4. The models we will try

All are trained on the same data and judged by the same test, so the comparison is fair. They are listed from simplest to most ambitious; each must beat the one before it to earn its place.

| # | Model | What it is, in plain words | Why it is in the list |
|---|---|---|---|
| 0 | Simple yardsticks | Always say "up"; copy yesterday's direction; buy last year's winners | Anything that cannot beat these is not learning |
| 1 | Table model | **Gradient-boosted trees** fed a table of about 40 hand-made measures per stock per day | Fast, hard to beat on this kind of data, and tells us which inputs matter |
| 2 | Your five-encoder model, rebuilt | Three "readers" of price history (last 7, 21 and 60 days), one for market mood, one for news, combined by an attention step that decides which reader to trust each day | Your published design, re-tested fairly |
| 3 | One-reader model | A single small network reading the last 60 days | Tells us whether five readers are really better than one |
| 4 | Ripple model | Model 2 or 3 for each stock, plus a step where each stock receives information from the stocks it is linked to | Your research idea |
| 5 | Blend | The average of the best two or three | Blends are usually steadier than any single model |

Notes on model 2:
- The first version uses prices, volume and market mood only. The news reader is built but switched off until we have news data; the model is designed to work with a reader missing.
- **Fibonacci levels** go in as inputs, and we test the model with and without them to see whether they help.

Notes on model 4, the links we will try, each one dated so the model never knows a link before it existed:
1. Same industry.
2. Same index (Nifty 50, Nifty 500, sector indices).
3. Price links measured from the past only: which stocks have tended to move together, and which have tended to move first.
4. Overseas markets and the Nifty future as "market nodes" every stock connects to.
5. Later, if data is found: same business group (Tata, Adani and so on), customer and supplier, and companies named in the same news item.

What every model outputs, for each stock each evening:
1. **Direction**: chance the stock closes higher tomorrow, and higher over the next 5 and 10 trading days.
2. **Size**: expected move in percent over those periods, compared with the market.
3. **Range**: expected high and low for tomorrow and for the holding period.
4. **Target-before-stop**: chance the price reaches the target before the stop-loss within the holding period.
5. **Confidence**: how sure the model is, checked so that "70% sure" really is right about 70% of the time (**calibration**).

These five replace the paper's four outputs. Direction, size and confidence carry over; the Fibonacci level output becomes the range and target-before-stop outputs, because those are what a buy range and target are built from.

## 5. The dataset

**One row = one stock on one day,** using only what was known by that evening.

Inputs, built in three steps so that step 1 is enough to start:

| Step | Inputs | Source | Status |
|---|---|---|---|
| 1 | Last 60 days of open, high, low, close, volume, value traded; simple measures (recent returns, how jumpy the stock is, distance from recent high and low, volume against normal); day of week, days to month end | Existing daily panel | Ready |
| 1 | Market mood: average move of all stocks, share of stocks rising, overseas markets overnight | Existing panel and overseas series | Ready after small clean-up |
| 2 | Nifty and sector index levels, **India VIX**, delivered-volume share, futures and options daily figures (**open interest**), foreign and domestic institution buying | NSE daily files | To collect; history available |
| 2 | Industry labels and index membership for all stocks, with dates | NSE and niftyindices.com | Partly held; to extend |
| 3 | Company announcements with exact time (results, orders, board meetings) | NSE announcements feed | To collect; start saving now |
| 3 | News headlines and a mood score for each | To be chosen | To collect going forward |

What the model learns to predict (the "answers" in training) is computed from the days after each row: next-day direction and move, 5 and 10 day move, the highs and lows reached, and whether target came before stop.

**More history.** Training data before 2025 is only five and a quarter years, and it contains one crash (2020) and one long rise. NSE publishes daily price files going back much further. Extending to 2010 would roughly triple the training data and add several more market moods. This is the single most valuable data task. It is not needed to start.

**Minute data.** Already on hand on the Mac at `/Users/onkarj012/Projects/market/intranet_optinet/data/nifty500`: 536 files, 439 million rows, 531 stocks, February 2015 to June 2026. It gives three things: (a) daily prices back to 2015, nearly doubling the training history; (b) what the intraday style needs; (c) ripple tests at the speed of minutes, where the effect should be strongest. Open points: this machine has 11 GB free, so the files are packed into a compact format on the Mac first (expected a few GB); whether the prices are adjusted for splits is not yet known and is checked against the daily panel; the 2025 and 2026 part goes straight into the blind vault. The paid Zerodha subscription is then needed only to keep it current and to fill missing stocks.

**Rules for the data, kept short on purpose:**
1. Three checks, each reported as a count, run in minutes:
   - Can every date and price be read, and are prices positive?
   - Does every row use only information from that day or earlier?
   - How many rows were dropped, and why?
2. Gaps do not stop work. A missing input is marked as missing and the model is told. A stock with too little history is left out and counted.
3. Only two things stop work: a row whose answer cannot be computed correctly, and anything that would let 2025 or 2026 leak into training.

## 6. How training works

**The split by time.** Never shuffled, always in date order.

| Period | Use | Name |
|---|---|---|
| Start of data to end of 2022 | The model learns from this | Training |
| 2023 | Used to choose settings and decide when to stop training | Tuning |
| 2024 | Used once per model to check confidence and fix the confidence cut-off | Checking |
| 2025 and 2026 | Never seen until the final test | Blind |

- A gap of 10 trading days is left between periods so that a 10-day answer in one period cannot overlap the next.
- The blind years are moved into a separate file that the training code cannot open. Each opening is written to a log with the date and the model's fingerprint.
- Honest note: we already know one thing about 2025. The two old simple rules were tested through September 2025 and had a bad year. That knowledge must not be used to shape the new models, and it will not be.

**Before training (pre-training steps).**
1. Build the dataset for step 1 inputs.
2. Run the three checks and read the counts.
3. Run the simple yardsticks (model 0) so we know the numbers to beat.
4. Write down the settings and the pass marks for the next model before running it.

**Training.**
- Runs on this machine's graphics card (GTX 1650, 4 GB). The table model takes minutes. The deep models should take a few hours each for the 300 to 500 most traded stocks; this is an estimate and will be measured on the first run.
- If a model is too big for the card, the first answer is fewer stocks or a smaller model, and the second is renting a graphics card online by the hour.
- Each model is trained three times from different random starts. If the three disagree a lot, the result is luck, not learning.

**After training (post-training steps).**
1. Score on the tuning year against the yardsticks.
2. Check confidence on the checking year and fix the cut-off there.
3. Run the picks engine over the tuning and checking years as a full replay with costs (Section 7).
4. Only a model that passes all three goes to the blind test.
5. Record everything: the data version, the code version, the settings, the scores. One line per run in a single results table anyone can read.

## 7. How we test (backtest and evaluation)

A **backtest** replays history day by day: each evening the model sees only the past, makes its picks, and the replay then fills or misses those orders using the next days' real prices.

**Three levels of testing, each answering a different question.**

| Level | Question | How |
|---|---|---|
| 1. Prediction | Does the model see anything real? | Accuracy against the yardsticks; how well its ranking of stocks matches what happened; whether confidence is honest |
| 2. Picks | Would its picks have made money? | Full replay with buy ranges, targets, stops, costs and tax |
| 3. Live | Does it behave the same in real time? | Paper trading, then small real money |

**Making the replay honest.**
- A buy order inside the day's range is not assumed to fill. It fills only if the price traded through it, and never on a day the stock was locked at its **price band**.
- Costs are charged in full: tax on the trade (**STT**), stamp duty, exchange fees, the fixed charge for selling shares held overnight (**DP charge**, Rs 15.34), and an allowance for getting a slightly worse price than planned (**slippage**).
- Tax on profits is included.
- Stocks that were later delisted stay in the history, so the test is not flattered by survivors only.
- Results are shown next to an index fund held over the same period.

**The mistakes that inflate results, and the guard for each.** These are the usual reasons a published accuracy does not survive real trading.

| Mistake | Guard |
|---|---|
| Shuffling days so the model trains on the future | Split strictly by date |
| Scaling inputs using figures from the whole period | Scaling learned from the training period only |
| Overlapping windows across the train/test line | 10-day gap between periods |
| Choosing the confidence cut-off on the test data | Cut-off fixed on 2024, then applied to 2025 and 2026 unchanged |
| Reporting accuracy on a tiny selected set of days | Always report how many predictions survive the cut-off |
| Testing only on stocks that still exist | Delisted stocks kept |
| News stamped with a later time than it was really known | Only items with an exact publication time, used after that time |
| Trying many versions and reporting the best | Every run is logged; the blind years are opened once per final model |

**What gets reported, every time.**
- Accuracy, and accuracy at each confidence level with the number of predictions kept.
- Average gain per trade after costs, share of winning trades, and **profit factor**.
- Yearly return, the worst fall from a peak (**drawdown**), and the same two numbers for the index fund.
- Number of trades. Below about 100 completed trades, a result is treated as a hint, not proof.

**How we decide whether a change helped.**
1. Write down the change and what it is expected to improve before running it.
2. Run old and new on the same data with the same random starts.
3. Keep the change only if it improves the picks result on the tuning year by more than the spread between random starts, and does not rely on a handful of stocks or one month.
4. Otherwise drop it. Either way, log it.
5. Once live, a new model runs on paper beside the current one for at least 20 trading days before it can take over.

**The ripple test specifically.** Three versions of model 4, identical except for the links: true links, no links, shuffled links. The idea passes only if true links beat both others on the tuning year and again on the blind years. Then each kind of link is tested alone to find which ones carry the effect.

## 8. From model to daily picks

The picks engine is plain rules, not a model, so it can be read and checked.

1. **Shortlist.** Keep stocks that are actively traded, not under exchange restrictions, and where the model's confidence is above the fixed cut-off.
2. **Rank** by expected gain after costs. A pick must expect to earn clearly more than its costs or it is dropped. Some days there will be no picks, and that is correct behaviour.
3. **Buy range.** From the predicted low to a little above yesterday's close. If the stock opens above the range, the pick is skipped, not chased.
4. **Target and stop-loss.** Set from the predicted range and how jumpy that stock normally is, so a calm stock gets a tight target and a wild one a wide target.
5. **Holding time.** The period the model is most confident about (1, 5 or 10 days). If neither target nor stop is hit by then, sell.
6. **Quantity.** Sized so that hitting the stop-loss loses a fixed small share of the account (1% to start), capped at a maximum share of the account per stock.
7. **For holdings.** Each evening every held stock is re-scored: hold, tighten the stop, or sell tomorrow in a stated range.

Two trading styles are both built, each with its own picks list and its own paper record (decided 2026-11-14):
- **Same-day (intraday)**: enter in the morning range and close the trade before the market closes. Gives both **buy** signals (stock expected to rise) and **sell** signals (stock expected to fall). A sell signal here means short selling: selling shares you do not own in the morning and buying them back before the close, which Indian brokers allow only within one day. Lower costs (about 0.1 to 0.2% a trade) and no overnight risk, but the stock must move enough in one day. Needs minute prices to train and test properly.
- **Multi-day (swing)**: hold up to 10 days. **Buy signals only**, because shares cannot be sold short overnight in the ordinary share market. Higher costs (about 0.25 to 0.5% a trade at small sizes) but larger moves to capture. Works on the daily prices already on hand, so it is built first.

Each style goes to real money only if its own blind test and paper record pass. One failing does not stop the other.

**Account size.** At Rs 10,000 the fixed selling charge is a large slice of a small trade, so the system will hold one or two positions and take few trades. As the account grows it holds more positions. The same rules apply at every size; only the number of positions changes.

## 9. The system

```
 EVENING (after market close)                MORNING (before 09:00)

 NSE daily files ─┐                          overnight overseas moves ─┐
 announcements  ──┼─> collect ─> checks ─>   early announcements ──────┼─> update
 overseas data  ──┘      │                                              │
                         v                                              v
                  build today's rows ─────────────> model ─> picks engine ─> picks list
                                                                             │
                                             ┌───────────────────────────────┤
                                             v                               v
                                       dashboard + phone alert         paper account
                                                                             │
 DURING THE DAY                                                              v
 fill or miss each order from real prices ─> update holdings ─> evening report
```

**Parts, and where each comes from.**

| Part | Job | Source |
|---|---|---|
| Collectors | Download each day's files and keep the originals | Existing (`ingest/`), plus new ones for step 2 and 3 inputs |
| Dataset builder | Turn files into model rows and answers | New |
| Training code | Train, score and log every model | New, reusing the date-splitting and leak checks from Meridian |
| Model store | Keeps each trained model with its fingerprint and scores | New |
| Blind vault | Holds 2025 and 2026; opens only for a final test | New |
| Picks engine | Section 8 | New |
| Replay | Section 7 | Rebuild on the existing account and cost code |
| Paper account | Tracks orders, fills, holdings, cash | Existing (`ops/daily_account.py`, event log) |
| Dashboard and alerts | Shows picks, holdings and results; approvals later | Existing (`serve/`), extended |
| Order placing, risk limits, server | For real money | As summarized in `.scratch/kairos-ai/retained-system-requirements.md` |

**Training and daily use are separate.** Training happens occasionally on this machine. Daily use only loads a finished model and runs it, which takes minutes and needs no graphics card.

## 10. Paper trading and the path to real money

You asked for a few weeks of paper trading. One research report asked for six months. The plan below gets real money in sooner without pretending a few weeks proves anything, because the two blind years are the main proof and paper trading only has to show that live behaviour matches them.

| Stage | Length | Money | What it must show to move on |
|---|---|---|---|
| 1. Shakedown | 3 to 4 weeks | Paper | Picks arrive before 09:00 every day; paper fills match real prices; nothing breaks |
| 2. Paper record | Until about 100 completed trades | Paper | Results inside the range the blind test predicted |
| 3. Small real money, you approve each trade | 8 to 12 weeks | Rs 10,000 | Real fills match paper fills; costs match the model |
| 4. Trades by itself within limits | Ongoing | Raised in steps | Each raise needs the previous level to have stayed inside its predicted range |

Stages 2 and 3 can overlap: real fills at Rs 10,000 are the best check of the paper fills.

It drops back a stage automatically if: results fall outside the predicted range for 30 trades, a loss limit is hit, or the account and the broker disagree about holdings.

## 11. Daily use and keeping it healthy

**Your day.**
- Morning: one tap to log in to Zerodha. Read the picks. In stage 3, approve or reject each.
- Evening: a short report: what filled, what was sold, profit and loss, and how results compare with what the model expected.

**The machine's upkeep.**
- Weekly: a health check comparing recent accuracy and confidence with the blind-test figures.
- Monthly or quarterly: retrain with the newest data. The new model must beat the current one on paper for 20 trading days before it takes over.
- Warning signs that pause new buys: accuracy on confident picks falls to chance; confidence stops being honest; picks cluster in one industry; a data source goes missing.
- Every month from 2027 onward becomes fresh blind data, so the evidence keeps growing.

## 12. Order of work

Times are estimates and will be corrected after the first phase.

| Phase | What happens | You get | Rough time |
|---|---|---|---|
| 0 | Dataset from existing prices; three checks; blind vault; yardsticks. Alongside: pack the Mac's minute prices into a compact format, put 2025 and 2026 in the vault, check them against the daily panel | A dataset and the numbers to beat; minute data ready | A few days |
| 1 | Table model, then your five-encoder model (reusing the paper's code, scored on the raw move and on an honest multi-day trend) and the one-reader model, price inputs only | **The first trained deep model**, scored against yardsticks | 1 to 2 weeks |
| 2 | Picks engine and honest replay on 2023 and 2024 | Picks with buy range, target, stop, quantity; profit after costs | 1 to 2 weeks |
| 3 | In parallel: extend daily history to 2015 from the minute prices; build the same-day dataset; collect index, futures and options, industry data; reuse the paper's news scripts and start saving announcements | Bigger and richer dataset | 2 to 3 weeks, in the background |
| 4 | Retrain with the richer data; same-day model; ripple model with the three-way link test on daily and on minute data | The answer on the ripple idea for the tuning year | 2 to 3 weeks |
| 5 | Freeze the best model and settings; open the blind years once | The honest result | 1 day |
| 6 | Paper trading stage 1 and 2; server set-up alongside | Daily picks before 09:00 | From phase 2 onward |
| 7 | Real money stages 3 and 4 | Live trading | After stage 2 passes |

Paper trading of an early model can start as soon as phase 2 is done; it does not wait for the blind test.

**When we stop or change course.**
- No model beats the yardsticks on the tuning year after phases 1 and 4: next-day and few-day prediction from this data does not work. Options then are minute data, longer holding periods, or holding an index fund while research continues.
- A model passes tuning but fails the blind years: it was fitted to the past. It is not traded, and the blind years are no longer blind for that model family; new evidence then has to come from live paper trading.
- The ripple links do not beat shuffled links: the idea is recorded as not supported on daily data, and the picks system carries on with the best other model.

## 13. Answers received (2026-11-14)

1. Paper: "HybridSwingNet for explainable swing trading using multi-encoder deep learning and confidence-calibrated signal execution", Scientific Reports, https://www.nature.com/articles/s41598-026-63497-w. Code and data are on the Mac at `/Users/onkarj012/Projects/major_pro`.
2. The 75 to 80% applied to a smaller share of predictions: 34% of them at the 75% cut-off and 66% at the 60% cut-off (table in section 2).
3. Sentiment came from a Kaggle file covering many stocks plus a data set scraped from Google.
4. The Mac folder with minute prices still exists.
5. Paying for the Zerodha data subscription and occasional graphics card rental is approved.
6. Both styles are wanted: same-day with buy and sell signals, multi-day with buy signals only (section 8).

Loss budget (decided 2026-11-15): a 10% fall from the account's highest value stops the system and returns it to paper until you review; a 5% fall halves position sizes; losing 2% in one day stops new trades for that day. Old code may be purged where the rebuild replaces it.

---

## Glossary

**Backtest**: replaying a strategy on past data to see what it would have done.
**Blind years**: data kept completely away from the model until the final test, so the result is honest.
**Calibration**: checking that the model's stated confidence matches reality; when it says 70%, it should be right about 70% of the time.
**Drawdown**: the fall from the highest account value to the lowest point after it. A 30% drawdown means the account fell 30% from its peak.
**DP charge**: a fixed fee (Rs 15.34 at Zerodha) each time you sell shares you held overnight, whatever the size of the sale.
**Fibonacci levels**: price levels at fixed fractions (such as 38.2% and 61.8%) of a recent rise or fall, where some traders expect the price to pause or turn.
**Gradient-boosted trees**: a method that builds many small yes/no decision charts, each correcting the mistakes of the last. LightGBM is one version.
**India VIX**: an index that shows how large a move traders expect in the Nifty over the next month. High means nervous.
**Open interest**: the number of futures or options contracts currently open. Rising open interest means new money is entering.
**Overfitting**: when a model memorises the past instead of learning something that repeats, so it looks excellent on old data and fails on new data.
**Price band**: the largest move a stock is allowed in one day. At the band, trading can freeze with buyers or sellers unable to trade.
**Profit factor**: total money won divided by total money lost. Above 1 means profitable; 1.5 is good.
**Slippage**: the difference between the price you expected and the price you actually got.
**Stop-loss**: a price set in advance at which a losing trade is sold to limit the loss.
**STT**: Securities Transaction Tax, charged by the government on each trade.
