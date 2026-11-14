# Changelog

## 0.4.4 (2026-11-14)
- Fairer stock lists for the momentum and earnings tests: every stock that traded enough in ANY year of the test, including companies that were later delisted, instead of only the stocks that trade today. Before, the tests only saw the survivors, which made results look better than they were. The first run downloads more data and takes longer.

## 0.4.3 (2026-11-13)
- AI trader test, version 3: the AI reacts to news published DURING market hours. It uses its plan from the evening before, and the entry is delayed realistically (the AI's real answer time plus a minute). The results show how much being slower than professional traders costs.

## 0.4.2 (2026-11-13)
- AI trader test, version 2: the evening before, the AI reviews each company and writes a plan (what's expected, what good or bad news would look like). At 9:20 AM it compares the new headlines and the pre-market move with its plan and scores each company. Each day it buys the 2 best and shorts the 2 worst. It's tested on days version 1 didn't use.

## 0.4.1 (2026-11-13)
- New in the AI tab: AI trader test. Your AI reads pre-market news for past days and decides buy, short or skip; it checks whether those trades would have made money (vs the S&P 500, after costs). The rules are fixed in advance, and the AI only gets to trade on its own if it passes.
- Fix: if crash protection was switched from 'short' to another setting while holding the inverse fund (SH), Eimeria now sells it instead of leaving it behind.

## 0.4.0 (2026-11-13)
- Four new strategy tests in the Backtest tab, each with rules fixed in advance and a PASS/FAIL result: insider clusters and CEO/CFO buys, earnings drift after big earnings beats, momentum (last year's winners), and trend following across stocks, bonds, gold and real estate.
- Backtests now use prices adjusted for stock splits and dividends. Before, a split could look like a crash or a huge gain. Please re-run your swing backtest: results may change a little.
- Live trading is only added for strategies that pass.

## 0.3.2 (2026-11-13)
- Day trading removed from Settings: every version tested on real data lost money (chart signals, confirmed gaps, S&P 500 late-day momentum). It's switched off even if it was on before. The strategies can still be backtested.

## 0.3.1 (2026-11-13)
- New backtest: S&P 500 late-day momentum (a published effect). The rules are fixed in advance and the result says PASS or FAIL: it must make money both before 2024 and from 2024 on.
- Confirmed-gap day trading is now labelled as having lost money in testing (2-year test on all liquid US stocks).
- Backtest tab: settings that don't apply to the chosen strategy are hidden or greyed out.

## 0.3.0 (2026-11-13)
- New: day trading on confirmed gaps. Trades overnight news only after the first 30 minutes confirm the move (buy at ~10:06, stop at the 30-minute low, sell after 60 minutes). Off by default: backtest it first (Backtest tab > Day trading: confirmed gaps).
- Gap trading has its own real-money lock: 30 closed paper gap trades with a profit factor of 1.2 or more.
- Safety fix: if the broker fails when day trades are sold at the end of the day, Eimeria now keeps retrying until the close instead of trying once.
- The old chart-pattern day trading is labelled as having lost money in testing.

## 0.2.6 (2026-11-13)
- Backtest tab: new 'Copy results' button to paste the results anywhere
- Backtest tab: the account-value chart is no longer squashed (the tab scrolls, the chart keeps its height, dates are readable)
- Selected table rows are highlighted in blue instead of red; the log panel starts smaller

## 0.2.5 (2026-11-13)
- Fix: after an update, Windows showed an error instead of the 'Updated from X to Y' message (and repeated it on every start)

## 0.2.4 (2026-11-13)
- Self-test: missing live quotes outside market hours are reported as normal instead of as a warning

## 0.2.3 (2026-11-13)
- Updates: 'Check for updates now' always shows a clear answer (your version, the newest version on GitHub, or the exact error)
- Updates: old update settings from early versions are corrected automatically, and if one GitHub address is blocked a second one is tried
- Self-test: new 'Updates (GitHub)' check

## 0.2.2 (2026-11-13)
- Alpaca keys: spaces, line breaks and invisible characters picked up when copying are removed automatically
- If Alpaca rejects your keys, Eimeria now explains why: paper vs live keys, swapped Key ID and Secret, or an outdated secret

## 0.2.1 (2026-11-13)
- Fix: the Windows app now finds new versions (GitHub's download links refused the quick 'is it there yet?' check)

## 0.2.0 (2026-11-13)
- Windows app: download Eimeria-windows-0.2.0.zip from the Releases page, unzip, and run Eimeria.exe - no Python needed
- The Windows app updates itself too: each release is built and tested on Windows by GitHub, and the update is offered once that build is ready
- Security: blocked a Windows-only path trick that could have pointed outside the Eimeria folder

## 0.1.2 (2026-11-12)
- Updates are now published on GitHub (repository catsandlights-ops/Eimeria) and downloaded by exact commit, with an extra check that the downloaded version matches
- Includes all 0.1.1 security, safety and reliability fixes

## 0.1.1 (2026-11-12)
- Security: hardened update installer (size limits, strict version tags, never touches files outside the app folder), hardened parsing of SEC filings, no pickle files
- Safety: kill switch now also stops orders that were mid-flight, no duplicate orders while an order is pending, unfilled remainders of partly filled orders are cancelled
- Reliability: crash log and on-screen message for unexpected errors, a damaged settings.yaml no longer stops the app, news feeds can't hang the bot, fewer broker API calls, swing exits on early-close days, old learning data pruned
- Removed a dead news feed; added a stress test (python -m tests.stress_sim)

## 0.1.0 (2026-11-12)
- First Eimeria release, with built-in updates
- Swing trading on large insider purchases, optional day trading, crash protection, backtesting, AI test, strategy health monitor and self-test
