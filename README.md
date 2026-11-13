# Eimeria (Windows desktop app)

> **Upgrading from "AI Day Trader"?** Unzip the new `eimeria` folder **next to** your old `ai-trader` folder (same parent folder), run `setup.bat`, then `run.bat`. On first start, Eimeria copies your settings, keys, trade history and caches from `ai-trader`. The old folder isn't changed, and you can delete it once everything looks right.

An AI-assisted trading bot for US stocks, with two strategies you can switch on or off:

- **Swing trading (recommended):** buys stocks where company executives and directors have just bought **$1 million or more** of their own stock while it's in an uptrend. It holds them for about 2 months (40 trading days), with a stop-loss. This was the only approach that showed an edge on real data (see *Research results* below).
- **Day trading** (off by default): 5-minute chart signals plus AI news reading, with every trade closed before 4 PM. On real data the chart signals **did not** beat trading costs. You can switch it on in Settings → Strategies.

It also:

- **Reads the news** before the market opens (8:45 AM New York time) and after it closes (4:15 PM), and writes you a briefing and a review.
- **Finds trades** every 5 minutes. It combines chart signals (trend, VWAP, breakouts, RSI, momentum) with AI-scored news.
- **Checks the whole market first.** In a falling market it's pickier, and in a selloff it stops buying.
- **Follows "smart money"**: company executives buying their own company's stock (public SEC filings) decide which stocks it watches.
- **Executes trades** through Alpaca with a stop-loss and take-profit. It then takes half profit early, trails the stop, closes trades that go nowhere, and closes everything before the market closes.
- **Protects you if something breaks.** It checks every minute that each position has a stop-loss at the broker and re-attaches one if it's missing. It also keeps your PC awake and alerts your phone.
- **Improves itself** from thousands of observations (shadow learning), not just its own trades.
- **Backtests**: you can test the strategy on up to 2 years of past data, with an *edge table* that shows whether any signal strength beats costs consistently.
- **Locks real-money trading until there's evidence.** Paper trades and its own measurements must show it makes money after costs.
- **Keeps costs down**: skips stocks with wide bid/ask spreads, and enters with price-capped limit orders instead of market orders.
- **Runs a free AI on your own PC** (Ollama), so there are no AI fees.

> 📊 **Research results, in short.** Swing trading on large insider purchases in uptrending stocks beat the market per trade from 2019 to 2026, in both halves of the period and in 7 of 8 years. Day-trading chart signals and "big news jump" swing trades did not. Details below.

> 📉 **What testing on real 5-minute data found (Aug–Nov 2026, 46 large US stocks, 60 trading days):** the chart signals on their own **lost money after trading costs** (profit factor 0.79, -5.2% vs -0.8% for the S&P 500). Their predictive power was tiny compared with costs, and it even **flipped direction** between the first and second half of the period. Opening-range breakouts on high-volume stocks and an adaptive momentum/reversal switch didn't work either. That's why the app now **locks real-money trading until the bot proves an edge** (see *Edge check* below). The untested part is the AI's news reading, which can only be measured by paper trading.

> ⚠️ **Read this first.** Day trading is risky, and most day traders, human or bot, lose money. This app starts in **paper trading** (fake money). The recommended path is **backtest → paper trade for 4–8 weeks → only then consider small real money**. Never trade money you can't afford to lose. This is not financial advice.

---

## Setup (about 30 minutes, one time)

### Step 1: Download Eimeria
1. Go to **https://github.com/catsandlights-ops/Eimeria/releases** and download the newest **`Eimeria-windows-<version>.zip`** (about 100 MB).
2. Unzip it somewhere you can write to, like **Documents**. Avoid *Program Files*: Eimeria keeps its settings and trade history next to the program, and updates need to replace files there.
3. Open the **Eimeria** folder and double-click **`Eimeria.exe`**.
4. Windows may show **"Windows protected your PC"**, because the app isn't code-signed (a signing certificate costs a few hundred dollars a year). Click **More info → Run anyway**. Some antivirus programs also flag apps packaged this way; Eimeria's code is public in the repository if you want to check it.

No Python install is needed: everything Eimeria needs is inside the folder.

> **Upgrading from an older Eimeria or "AI Day Trader" folder?** Unzip the new **Eimeria** folder *next to* the old `ai-trader` folder. On first start, Eimeria copies your settings, keys and trade history across.
>
> **Prefer running from the Python source?** Install Python 3.12+ (tick *Add python.exe to PATH*), download the source code from the same Releases page, then run `setup.bat` once and `run.bat` each time. Everything else works the same.

### Step 2: Install the free local AI (Ollama)
1. Download Ollama for Windows from https://ollama.com/download and install it.
2. Open **Command Prompt**: press the Windows key, type `cmd`, press Enter.
3. Paste this command and press Enter. It downloads the AI model (about 5 GB):
   ```
   ollama pull llama3.1:8b
   ```
4. Leave Ollama running (it sits in the system tray near the clock).

> **PC requirements:** 16 GB RAM is recommended. An NVIDIA graphics card makes the AI much faster, but it also works on the processor alone (slower). If your PC struggles, try a smaller model (`ollama pull llama3.2:3b`) and enter `llama3.2:3b` as the model name in Settings.

### Step 3: Create a free Alpaca paper-trading account
1. Sign up at https://alpaca.markets.
2. In the dashboard, make sure you are in **Paper Trading** (top-left account switcher).
3. On the right side, find **API Keys** and click **Generate New Keys**. Copy both the **Key ID** and the **Secret Key**. The secret is shown only once.

### Step 4: Start Eimeria
1. Double-click **`Eimeria.exe`** (or `run.bat` for the Python version).
2. Read and accept the risk notice.

The **Getting started** checklist at the top of the Dashboard walks you through the rest, one button per step: connect Alpaca, run the self-test, add your SEC contact, run the recommended swing backtest (2019 to now, 10–30 minutes the first time), and start paper trading. Each step ticks itself off. The detailed steps below cover the same ground.

### Step 5: Settings
1. Go to the **Settings** tab:
   - Paste your Alpaca **API Key ID** and **Secret Key**.
   - *(Recommended)* In **SEC contact**, type your name and email, e.g. `Jane Doe jane@example.com`. The SEC requires this to download insider-buying data. It is sent only to sec.gov.
   - *(Recommended)* Set up phone alerts (see below).
   - Click **Save settings**.
2. The top bar should show **PAPER TRADING**, and **AI: ready** in green.
3. **Run a backtest first** (see below), then click **Start Bot**. Leave the app open during market hours: 9:30 AM – 4:00 PM New York time, which is 2:30 – 9:00 PM UK time or 6:30 AM – 1:00 PM California time.

### Updates

Eimeria updates itself from its GitHub repository ([catsandlights-ops/Eimeria](https://github.com/catsandlights-ops/Eimeria)):

- It checks when it starts and every 6 hours (switch this off in **Settings → Updates**, which also has **Check for updates now**).
- When a new version is out, an **Update available** button appears at the top. Click it to see what's new, then **Install and restart**.
- **It won't install while the bot is running.** Stop the bot first, ideally after the market closes.
- Every file is checked against the release's fingerprint list (SHA-256) before anything changes. A damaged or tampered download is rejected and nothing is installed.
- **Your `settings.yaml`, `.env` (keys), `trader.db` (trade history), `cache` and `logs` are never touched.**
- The current version is backed up to `backups\` first. If anything goes wrong, it's restored automatically. **Settings → Updates → Roll back** goes back to the previous version at any time.
- After the restart, a message says whether the update worked. Details are in `logs\update.log`.
- **Windows `Eimeria.exe`:** a few minutes after each release, GitHub builds and tests the new `Eimeria.exe` on a Windows machine and attaches it to the Releases page. The update is only offered once that build exists, and the new exe installs itself over the old one.

### Self-test: check everything works

**Dashboard → Self-test** (also step 2 of the Getting started checklist). It checks every connection Eimeria needs and shows **PASS / WARNING / FAIL**, each with an explanation and what to do:

- your Alpaca keys (including whether a paper key is used in paper mode) and the account's status and type (a *cash* account gets a warning if you use the S&P 500 fund option);
- the market clock, daily prices (recent and back to 2019, for backtests), 5-minute prices and live quotes;
- recent news and the news archive (used by the AI test), and the top-movers list;
- the SEC insider data (both recent filings and the historical files);
- your local AI: is it running, is the model installed, did it answer a sample question correctly and in the exact format, and how fast;
- **order placement (optional, paper accounts only):** it places the exact order type the bot uses, 1 share of SPY at a price far below the market so it can't fill, checks Alpaca accepted it, and cancels it. It never runs on a live account;
- whether Windows sleep can be blocked while the bot runs.

**Copy report** copies the results as text (also saved to `logs\selftest_report.txt`), handy if you're asking someone for help.

### Step 6 (optional but recommended): Phone alerts
1. Install the free **ntfy** app on your phone (Google Play or App Store).
2. In the app, tap **+** and subscribe to a topic name nobody could guess, like `eimeria-7f3k9q2x`. Anyone who knows the name can read your alerts, so make it long and random.
3. Type the same name into **Settings → 6. Alerts → Phone alerts**, save, and click **Send a test alert**.

You'll be alerted about trades, the daily loss limit, errors, a position missing its stop-loss, and when the daily reports are ready. Each type can be switched off.

---

## Using the app

| Tab | What it shows |
|---|---|
| **Dashboard** | Account value, today's profit/loss, open positions, buttons to run any task now |
| **Signals** | The latest day-trading scan: each stock's score, the AI's decision and its reasoning |
| **Swing** | Tomorrow's swing plan, open swing positions with days held and days left, and results per setup |
| **News & Reports** | Pre-market briefing, after-close review, and headlines (double-click to open) |
| **Smart Money** | Stocks that company insiders have recently bought with their own money |
| **Trade Journal** | Every trade: entry, exit, profit/loss, how it ended, and why the bot took it |
| **Learning** | How much the bot trusts each signal now, how predictive each one has been, and its lessons |
| **Backtest** | Test the strategy on past data |
| **Settings** | Keys, AI model, watchlist, risk limits, exits, alerts |

The top bar shows **Market mood** (rising / normal / falling / SELLOFF). Hover over it for details.

**Buttons:**
- **Start Bot / Stop Bot** starts or pauses automatic trading. Stopping leaves positions open with their stop-loss/take-profit orders.
- **STOP & CLOSE ALL** is the emergency button. It cancels all orders and closes all positions immediately.
- **Check stop-losses** makes sure every open position has a stop-loss at the broker.
- The other task buttons (briefing, scan, review, insiders) work any time. Orders are only sent while the market is open.

**While the bot runs, your PC won't go to sleep.** The screen can still turn off. Keep the app open until after 4:00 PM New York time so it can close positions 10 minutes before the close. If the app is closed early anyway, every position keeps its stop-loss and take-profit at the broker (they're good-till-cancelled). The next time you start the app, it warns you about leftover positions and re-checks their protection.

---

## Backtesting: do this before anything else

The **Backtest** tab replays past market data through the bot's chart signals, market filter, position sizing, stops and exits, and charges a small cost on every fill.

1. Enter stocks (your watchlist is pre-filled), pick a date range (3–6 months is a good start), and click **Run backtest**.
2. Read the verdict. It checks the first and second half of your period separately and warns when they disagree. Look at the **profit factor** (money won ÷ money lost):
   - **Below 1.0**: the strategy lost money on this period.
   - **1.0 – 1.3**: roughly break-even. After real-world costs it probably wouldn't make money.
   - **Above 1.3**, with **100+ trades**: promising. Now test **different periods**, like a falling market or a calm one. A strategy that only works on one period is usually luck.
3. Compare with the **S&P 500 same period** card. If simply holding the index did better, ask yourself whether day trading is worth it.
4. The **Edge after costs** tab is the most honest view. It takes every 5-minute bar, groups them by how strong the signal was, and shows the average result of trading in the signal's direction for an hour, after costs, in each half of the period. A real edge shows **YES**: positive in both halves, with plenty of samples. If every row says "no", no setting of this strategy will make money on that data.
5. The **Signal quality** tab shows which signals actually predicted price moves (useful / weak / no edge / works backwards). **Use these signal weights in the bot** copies the suggested weights into the bot. Only do this after testing several months and many stocks.

**What a backtest can't tell you:** it can't replay the AI's news judgement fairly, so a minimum signal score stands in for AI approval. Past results don't guarantee future results, and live fills are usually a bit worse than simulated ones.

Full-market historical data (all exchanges) is free on Alpaca for anything older than 15 minutes, so backtests use it automatically.

---

## Day trading: confirmed gaps (off by default)

Day trading on chart patterns lost money in testing (see *Research results*). This day-trading strategy instead trades **overnight news, but only once the market has confirmed it**:

1. **9:30 AM:** a stock opens at least **2% above** yesterday's close (overnight news: earnings, an upgrade, a contract). It must cost $5 or more and trade at least $20M a day.
2. **Wait 30 minutes.** At 10:05 AM, if the stock is still above where it was after the first 5 minutes, the move is **confirmed**. If it fell back, it's skipped, because reversals did badly in testing.
3. **The AI reads the news** and can veto it. For example, it rejects a jump caused by a buyout offer at a fixed price, a share sale or a meme spike.
4. **Buy at about 10:06 AM.** The stop-loss is the lowest price of the first 30 minutes (never more than 4% below the entry), and each trade risks at most 0.5% of the account.
5. **Sell 60 minutes later**, or earlier if the stop-loss is hit. Positions are never held overnight.

**Research** (5-minute prices, 46 large stocks, 60 trading days, trading costs included): confirmed gap-ups made **+0.55% per trade in the first half and +0.33% in the second half** (profit factor 2.0, 81 trades). Gap-ups that reversed in the first 30 minutes, and short trades on gap-downs, made money in one half and lost it in the other. That's why shorting is a separate option, off by default.

**Treat this as promising, not proven.** 60 days is a short test. Before turning it on:
1. In the **Backtest** tab, choose *Day trading: confirmed gaps* and *My list + all liquid US stocks*, and test at least 1–2 years. This downloads 5-minute prices for gap days only, so it takes a while.
2. In the results table, *gap up, kept going (confirmed)* should be positive in **both halves**. If it isn't, leave it off.
3. Turn it on in **Settings → Strategies**, in paper mode. Real money stays locked until there are **30 closed paper gap trades** in the last 120 days with a profit factor of at least 1.2.

**Small accounts:** each gap trade is a day trade. US rules allow only 3 day trades in 5 days on margin accounts under $25,000.

---

## Swing trading

**How it works:**
1. **4:20 PM (after the close):** the bot looks at insider filings from the SEC (fetched hourly in the background) for officers and directors buying **$1M+** of their own company's stock on the open market. It keeps only stocks above their 50-day average, priced over $5, with at least $5M traded per day.
2. **The AI reads each candidate's recent news** and can veto it. For example, it rejects a stock pinned by a buyout offer, a share sale, or a promotional spike.
3. **9:45 AM next day:** buy the planned stocks with a limit order. Each position risks at most 0.5% of the account, with a stop-loss 4× the stock's typical daily range below the entry. It skips any stock that jumped more than 8% overnight, so it never chases.
4. **3:45 PM daily:** sell positions that have been held for 40 trading days.
5. Swing trades are **not** closed at the end of each day. They keep their stop-loss at the broker, even when the app is closed.

**Small accounts:** swing trades held overnight aren't "day trades", so the pattern-day-trader limit doesn't apply to them.

**Settings → Strategies:** turn each strategy on or off, choose setups, holding period, number of positions, the uptrend filter and the AI veto. The *big news jump* setup is available but off by default, because it didn't beat the market in testing.

**Two optional extras** (both off by default; your backtests use whatever you choose):
- **Keep idle cash in an S&P 500 fund.** Money not in swing trades sits in SPY, and some is sold when a swing trade needs cash. In testing, this **beat holding the S&P 500 alone in both 2019–22 (+61% vs +56%) and 2023–26 (+85% vs +69%)**. The catch: your account then moves with the whole market. In the 2020 crash it would have dropped about 35%, versus about 6% for swing trading on its own. Only switch it on if you'd be comfortable holding an index fund through a crash.
- **Market filter:** no new swing buys while the S&P 500 is below its 200-day average. This helped in 2019–22 (smaller drops) and slightly hurt in 2023–26.

**Backtest it yourself:** in the **Backtest** tab, choose *Swing trading* and *My list + every stock insiders bought*, then pick a date range (up to 8 years). The app downloads the SEC's insider records (once per quarter, then cached) and Alpaca's daily prices. Put your name and email in *SEC contact* first.

**Real money:** swing trading has its own lock. It needs **20 closed paper swing trades** in the last 180 days with a profit factor of at least 1.2. With 40-day holds, expect that to take a few months of paper trading.

---

## Research results (how the defaults were chosen)

All tests used real data, with trading costs, buying at the next day's open after the information became public.

**Day trading** (5-minute prices, 46 large stocks, Aug–Nov 2026): the chart signals lost money after costs (profit factor 0.79, -5.2%), and their direction flipped between the two halves. Opening-range breakouts and an adaptive momentum/reversal switch didn't work either. **Confirmed gap-ups** (overnight news that kept going for 30 minutes) were the exception: +0.55% and +0.33% per trade after costs in the two halves (see *Day trading: confirmed gaps*).

**Swing trading** (every insider purchase filed with the SEC 2019 – Q1 2026, about 112,000 filings, plus 7 years of daily prices for about 2,800 stocks):

| Setup | Result after costs |
|---|---|
| All insider purchases ≥ $100k | ≈ 0 vs small-cap stocks, below the S&P 500. **No edge.** |
| Insider purchases while the stock was *falling* | Clearly worse than the market (40 days: -1.2%, t = -5.8) |
| **Insider purchases ≥ $1M, stock above its 50-day average** (the default) | **+3.1% vs small caps and +2.3% vs the S&P 500 over 40 days on average** (1,002 trades, t = 3.9). Positive in 2019–22 *and* 2023–26, and beat small caps in 7 of 8 years. |
| Big news jump ("gap up ≥4% on 3× volume that held") | Worse than the market at almost every horizon. **No edge** (off by default). |

Portfolio simulation of the default (8 positions, 0.5% risk each): **+6.2% in 2020–22** (profit factor 1.34) and **+17.0% in 2023–26** (profit factor 1.75), with worst drops of about 6%.

### Crash protection (Settings → Strategies)

**Can a bot short the market before a crash and make money both ways?** Tested on 33 years of S&P 500 data (1994–2026, covering the dot-com crash, 2008, COVID and 2022), with trading costs and signals acted on the next day:

| Rule | 1994–2006 | 2007–2016 | 2017–2026 | Losses during the 4 crashes |
|---|---|---|---|---|
| Hold the S&P 500 | +10.8%/yr, worst drop -47% | +6.9%/yr, worst drop -55% | +15.0%/yr, worst drop -34% | -47%, -55%, -33%, -24% |
| **10-month rule → cash ("step aside")** | **+12.3%/yr, worst drop -19%** | **+6.9%/yr, worst drop -21%** | +9.0%/yr, worst drop -26% | **-4%, -5%, -8%, -16%** |
| 10-month rule → **short** | +10.5%/yr, worst drop -27% | +3.5%/yr, worst drop -41% | +0.5%/yr, worst drop -40% | **+35%, +59%, +20%**, -13% |
| 200-day rule → short | +7.2%/yr | -0.5%/yr | +4.3%/yr | +19%, +29%, +15%, -6% |

**What this means:**
- **No rule saw a crash coming "way before".** They all react *after* prices have started falling. Crashes can't be timed reliably.
- **Shorting made big money during crashes, then lost more in the rebounds** (-14% in 2009, -19% in 2020) and in choppy markets. Overall it did **worse than simply holding the index in every period**, with large drops of its own.
- **Stepping aside to cash worked best.** It cut the worst drop from about -50% to about -20% and kept long-run returns similar. The cost: it lagged in strong bull markets (2017–2026).

**The setting:**
- **Off** (default).
- **Step aside:** if the S&P 500 closes a month below its average of the last 10 month-end closes, Eimeria pauses new swing buys and sells the S&P 500 fund (if you use that option). It goes back to normal after a month closes above the average. That's about 1–2 switches a year.
- **Short:** the same signal, but spare cash goes into **SH**, an inverse S&P 500 fund that rises when the market falls. It has no leverage, and you can't lose more than you put in. You'll get a warning first, because it tested worse.

With the insider strategy plus the S&P 500 fund (2019–22 / 2023–26): *no protection* +61% / +85% (worst drop -35% / -20%); *step aside* +51% / +41% (worst drop -25% / -12%); *short* +28% / +12%. The Swing tab shows the current state, and you get an alert when it switches.

**Further ideas tested (each had to improve results in *both* 2019–22 and 2023–26):**

| Idea | Result | Decision |
|---|---|---|
| Idle cash in an S&P 500 fund | Beat the index in both periods; much bigger drops | Option, off by default (you choose) |
| Skip "routine" insiders who buy every year (academic research) | Only 9 of 1,010 picks affected; those lost money, but too few to matter | Not needed: the $1M threshold already excludes them |
| Skip SPAC-like shells near $10 | None got through the existing filters | Not needed |
| Trailing stop, holding winners up to 60–90 days | Much worse in 2023–26 | Rejected |
| Market filter (S&P 500 above its 200-day average) | Better in 2019–22, slightly worse in 2023–26 | Option, off by default |

**Read these caveats:**
- **Simply holding an S&P 500 index fund returned far more** in those years (+18% and +98%). Because each position is small, the strategy holds mostly cash. Its edge is per trade, not in total return.
- **A few big winners make most of the profit.** About half of trades lose, and the median trade beat small caps by less than half a percent. You need many trades, over a long time, for the average to show up.
- **Survivorship bias:** 25% of the $1M+ purchases were in companies with no price history in the test data (delisted, bought out or renamed, often SPACs in 2020–21). Their results are unknown.
- **The AI veto isn't tested.** Historical news wasn't available, so the backtests show the rules alone.
- Past results don't guarantee future results.

---

## The AI tab: is your local AI actually helping?

The AI reads the news and can **veto** swing candidates. That's the one part of the strategy the backtests couldn't test, so the **AI tab** has a tool to test it:

1. It takes past swing signals (from the SEC records) that came **after** the date you set as the *AI knowledge cutoff*.
2. For each one, it fetches only the headlines that existed before 4:20 PM that day, from Alpaca's news archive, and asks your AI the **exact** question the live bot asks.
3. It checks how each stock actually did over the holding period, compared with the S&P 500, and compares the stocks the AI approved with the ones it vetoed.

**Verdicts:**
- **HELPS:** the approved stocks clearly beat the vetoed ones. Keep the veto on.
- **NO CLEAR EFFECT:** the difference could be chance. The veto may just be removing trades.
- **HURTS:** the vetoed stocks did better. Turn the veto off in Settings → Strategies.

**Why the cutoff matters:** an AI model learns from text up to its training date, so for older events it may simply *remember* what happened to the stock. Set the cutoff later than the newest model's release date (shown on ollama.com). The default is 2025-07-01. Because the SEC's insider files lag a few months, and each signal needs 40 trading days to play out, recent cutoffs leave fewer signals to test.

**Comparing models:** enter several models, separated by commas (for example `llama3.1:8b, qwen3:8b`), after downloading each with `ollama pull <name>`. They answer the same questions, so you can compare their verdicts and speed directly. Double-click a result row to make that model Eimeria's AI. Each answer takes a few seconds to a minute depending on your PC, so 60 signals × 2 models can take a while.

**More reliable answers:** with **Ollama 0.5 or newer**, Eimeria requires the AI to answer in an exact format, so it never has to guess at a sloppy reply. A garbled answer gets one retry, and older Ollama versions fall back automatically. If the AI can't answer, swing trades are **kept**: a broken AI never blocks the strategy. The AI tab shows the speed and failure rate for the session, and has a *Speed test* button.

## Strategy health: is the edge still working?

Edges fade as more traders find them. Whenever you run a swing backtest, Eimeria remembers its per-trade results. Once you have **10+ closed swing trades** (paper or live), the Swing tab and each evening's review compare them with the backtest:
- **On track:** within normal variation.
- **Watch:** below the backtest, but it could still be bad luck.
- **Warning** (with an alert): far below the backtest, more than bad luck usually explains. Consider pausing swing trading and re-running the backtest on recent data.

---

## Edge check: the real-money lock

Real-money (live) trading stays **locked** until both of these are true over the last 30 days:

1. **Paper trading works:** at least 50 closed paper trades, with a profit factor of at least 1.2. This tests the *whole* system, including the AI's news judgement, which a backtest can't replay.
2. **The signals show a real edge:** in the bot's own scans (shadow learning), trades it would have taken averaged a profit after 0.10% costs over at least 10 days, with a confidence t-statistic of 2 or more. The statistic is computed per day, because scans within one day aren't independent. A t of 2 means roughly a 1-in-20 chance that the edge is luck.

The **Learning** tab shows progress on both. Paper trading continues normally while locked, and that's what builds the evidence. You *can* remove the lock in Settings, but you'll be asked to type `I ACCEPT THE RISK`.

---

## How it decides (in plain English)

1. **Check the market mood:** look at the S&P 500 fund (SPY) and a fund that tracks the VIX "fear index" (VIXY). In a falling market, buys need a stronger signal and more AI confidence. In a selloff (S&P down 2%+, or fear spiking), no new buys at all.
2. **Pick stocks:** your watchlist, plus stocks the morning briefing flagged, plus today's top movers, plus the top 5 stocks with recent insider buying.
3. **Score each one** from -1 (bearish) to +1 (bullish) using: trend (fast vs slow moving average), price vs VWAP, opening-range breakout, RSI extremes, and the gap from yesterday. Higher-than-normal volume makes the score stronger.
4. **Check the news:** the 5 strongest stocks get their latest headlines read and scored by the AI. Clickbait ("could soar 100x!") is filtered out, and a single article can't produce a strong score. Headline text is cleaned so it can't slip instructions to the AI.
5. **Ask the AI:** stocks still scoring strongly go to the AI with all the data, the market mood, insider buying, today's briefing and past lessons. It answers buy / short / skip with a confidence level. If the AI disagrees with the signals, the trade is skipped. If it claims high confidence on a weak signal, its confidence is capped.
6. **Risk check** (these rules can't be changed by the AI or the learner):
   - Each trade can lose at most **0.5%** of the account (stop-loss), and no position can be more than **10%** of the account.
   - No trading after losing **2%** in a day, at most **5** positions open and **10** trades a day.
   - No trades in the first 5 minutes or the last 30 minutes of the session, and no stocks under $5.
7. **Check costs:** if the gap between the bid and ask price is wider than 0.2%, skip the stock, because the trade starts too far behind.
8. **Place the order** as a *limit* order, paying at most 0.05% above the current ask, with a stop-loss and take-profit (2× the stop distance) attached. If it hasn't filled within 2 minutes, cancel it rather than chase the price. In live mode, nothing is sent while the edge-check lock is on.
9. **Manage the trade** (checked every 20 seconds):
   - When it's up by 1× the stop distance, **sell half** and move the stop to the entry price. The rest of the trade can no longer lose money.
   - Then **trail the stop** behind the best price reached.
   - If it's gone nowhere after **90 minutes**, close it (a **time stop**).
   - **10 minutes before the close**, close everything.

## How it learns

- **Shadow learning (main method):** every stock in every scan is recorded with its signal scores, whether or not it was traded. 30 and 60 minutes later the bot checks what the price actually did. Once it has 500+ observations (a few days of scans), it measures how well each signal predicts moves, and gives predictive signals more weight and useless ones less. Weights change gradually and stay between 0.2× and 2×. Tiny correlations are treated as noise and ignored.
- **Confidence calibration:** if trades in a confidence range keep losing money, it demands more confidence before trading. It never goes below your setting.
- **Lessons:** every evening the AI reviews the day's trades and the signal-quality numbers, and writes up to 3 lessons that feed into future decisions. You can delete any lesson on the Learning tab.

---

## Going live (real money): only when ready

1. Backtest several different periods, then paper-trade until the **Edge check** on the Learning tab says **UNLOCKED**. Ideally keep going to 100+ trades.
2. Paper results are usually better than real ones, because real orders can fill at worse prices.
3. In Alpaca, switch to your **live** account and generate **live** API keys (they differ from paper keys).
4. In Settings, paste the live keys, set Mode to **live** and type `LIVE` to confirm. Start with a small amount.
5. **Pattern-day-trader rule:** US margin accounts under $25,000 have historically been limited to 3 day trades per 5 business days. Regulators have been reviewing this rule, so check Alpaca's current rules. The app protects you by default; you can turn the protection off in Settings if it doesn't apply to your account.
6. **Better live data:** the free live data covers one exchange (IEX), so volume figures are partial and some stocks have gaps. Once you're serious, Alpaca's paid market-data plan gives full-market data: set **Live price data** to `sip` in Settings. Only do this after subscribing, or scans will fail.

---

## Files

| File | What it is |
|---|---|
| `settings.yaml` | All settings (created on first run; editable in the app) |
| `.env` | Your Alpaca keys. **Never share this file.** |
| `trader.db` | Trade journal, reports, lessons, learned weights, shadow observations |
| `logs/trader.log` | Detailed log, useful when something goes wrong |

| Code file | Job |
|---|---|
| `app/engine.py` | Daily schedule; scanning, deciding, trading, managing exits, protection, reviews |
| `app/indicators.py` | Chart signals and market mood (shared by live trading and backtests) |
| `app/exits.py` | Partial profit, trailing stop, time stop (shared by live trading and backtests) |
| `app/backtest.py` | The day-trading backtester |
| `app/swing.py` | Swing setups (shared by live trading and backtests) and the swing backtester |
| `app/swing_live.py` | Live swing trading: evening plan, AI veto, morning entries, timed exits |
| `app/updater.py`, `app/apply_update.py` | Checking for, verifying, installing and rolling back updates |
| `tools/make_release.py` | Prepares a new release (version number, changelog, update.json, fingerprints) |
| `app/selftest.py` | The self-test (checks every connection and order placement) |
| `app/ai_eval.py` | The AI test (does the AI's swing veto help?) |
| `app/ai_schemas.py` | The exact answer formats the AI must follow |
| `app/health.py` | Strategy health: real results vs backtest expectations |
| `app/swing_data.py` | Downloads SEC records + prices and finds swing signals (Backtest and AI tabs) |
| `app/market_timing.py` | Crash protection: the 10-month trend rule on the S&P 500 |
| `app/sec_history.py` | Downloads the SEC's historical insider-trading records for backtests |
| `app/news.py` | News feeds, AI news scoring, hype and manipulation filtering |
| `app/smart_money.py` | SEC insider-purchase tracker |
| `app/risk.py` | Safety rules and position sizing |
| `app/learner.py` | Self-improvement (shadow learning, calibration, lessons) |
| `app/alerts.py` | Windows and phone notifications |
| `app/system.py` | Keeps Windows awake while the bot runs |
| `app/broker.py` | Alpaca connection |
| `app/llm.py` | Local AI (Ollama) connection |
| `app/gui/` | The window |

**Optional extras:**
- `run_tests.bat` runs the 51 automatic tests (fake broker and fake AI, no money involved).
- `build_exe.bat` builds a standalone `Eimeria.exe` in `dist\Eimeria`. Copy your `settings.yaml` and `.env` next to the exe.

## Security and reliability

**Your keys and data:**
- Alpaca keys are stored only in `.env`, on your PC. They're never written to logs, the self-test report, or the code repository, and only ever sent to Alpaca.
- Your SEC contact (name and email) is only sent to sec.gov, which requires it.
- Phone alerts, if you set them up, go through the public ntfy.sh service. Anyone who knows your topic name can read them, so use a long random one.

**What's checked:**
- **Code scanning** (bandit): no open findings. Each remaining low-risk spot was reviewed and marked with the reason.
- **Library audit** (pip-audit): every library Eimeria uses was checked against the public vulnerability database; none had known problems.
- **Downloaded data is treated as untrusted:**
  - SEC filings are read with a hardened XML parser;
  - news text is cleaned before the AI sees it, and the AI's answers must match an exact format;
  - news-feed downloads have time and size limits.
- **Updates:**
  - files are checked against fingerprints;
  - version tags must look like `v1.2.3`;
  - downloads are size-limited (no "zip bombs");
  - nothing can be written or deleted outside the Eimeria folder;
  - user data is never touched;
  - automatic rollback if anything fails;
  - installed only when you click Install.
- **Crashes:** they're recorded in `logs\crash.log` and shown on screen. A damaged `settings.yaml` is set aside, and Eimeria starts with default settings instead of failing.

**Your GitHub account is the key to updates.** The fingerprint check catches damaged downloads, but whoever controls the `catsandlights-ops` GitHub account can publish updates. **Turn on two-factor authentication** on GitHub (Settings → Password and authentication), and only click Install for updates you expect.

**Stress testing.** `python -m tests.stress_sim --days 30 --faults 0.05` runs the real trading engine through simulated weeks against a simulated broker and market. 5% of broker and AI calls fail at random, and there's a crash day, an early-close day and an optional kill switch. After every simulated minute it checks the safety rules:
- day trades are never held overnight;
- position-count, position-size and trades-per-day limits hold;
- every position gets a stop-loss within 10 minutes;
- nothing is traded after the kill switch;
- every closed trade has a valid result.

## Troubleshooting

- **"AI: offline"**: open the Ollama app. Hover over the label to see the exact problem, such as the model not being downloaded.
- **"Not connected" / unauthorized**: re-copy your Alpaca keys, and make sure paper keys go with paper mode.
- **No trades happening**: this is normal on many days. The bot is built to skip anything unclear. Check the Signals tab to see the scores and the AI's reasoning, and the Market mood label.
- **Scans are slow**: the local AI is slow on your hardware. Use a smaller model, or raise *Scan every* to 10 minutes.
- **Backtest fails mentioning a subscription**: try a date range ending at least a day ago.
- **No Windows pop-ups**: check that Windows notifications are on for Python (Settings → System → Notifications).

## Known limits

- **No edge has been demonstrated.** On real data, the chart signals lost money after costs (see the top of this file). The AI news layer might add an edge on stocks with real news, but that's unproven until your paper trading shows it.

- You can't see what individual traders hold in real time. Insider buying (filed within 2 business days) is the best free public "smart money" data, but it predicts moves over weeks to months, so it's used to choose stocks to watch, not as a reason to buy. Congress trade disclosures can be up to 45 days late, which is too slow for day trading.
- Local 8B-parameter models are much weaker than large cloud AI models. Their news reading is useful, but treat their reasoning with skepticism.
- The backtester treats half-day sessions (e.g. the day after Thanksgiving) as full days.
