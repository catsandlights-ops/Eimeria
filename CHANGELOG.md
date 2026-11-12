# Changelog

## 0.1.1 (2026-11-12)
- Security: hardened update installer (size limits, strict version tags, never touches files outside the app folder), hardened parsing of SEC filings, no pickle files
- Safety: kill switch now also stops orders that were mid-flight, no duplicate orders while an order is pending, unfilled remainders of partly filled orders are cancelled
- Reliability: crash log and on-screen message for unexpected errors, a damaged settings.yaml no longer stops the app, news feeds can't hang the bot, fewer broker API calls, swing exits on early-close days, old learning data pruned
- Removed a dead news feed; added a stress test (python -m tests.stress_sim)

## 0.1.0 (2026-11-12)
- First Eimeria release, with built-in updates
- Swing trading on large insider purchases, optional day trading, crash protection, backtesting, AI test, strategy health monitor and self-test
