# Changelog

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
