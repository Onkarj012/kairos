# Retained system requirements from Kairos v2

This short excerpt carries forward only the adopted requirements from Kairos v2 sections 4.4–4.6. It is subordinate to the Kairos AI spec and `decisions.md`; it does not restore the old target-portfolio strategy or default-ETF mandate.

## Execution

- Keep `apply_event` and the hash-chained event log.
- Use one broker account; swing and same-day are attribution labels, not separate sellers.
- Persist order states from planned through terminal state. Resolve an unknown order before sending anything else.
- Derive client order IDs from intent IDs; persist broker order IDs, trade IDs, and fills.
- Use limit orders in the continuous session after the opening period, with bounded repricing and explicit expiry. Do not assume a close fill.
- Reconcile holdings, T1 and sellable quantities, open orders, cash, and charges against Kite before taking new risk.

## Risk

- Keep one persistent risk state with separate permissions to add exposure, reduce exposure, reconcile, and observe. Reconciliation is never disabled.
- Enforce name, sector, gross, order-notional, daily-turnover, order-count, and participation limits at the order boundary.
- Fail closed on stale data, missing token, failed reconciliation, or unknown order state.
- Set the loss budget against total investable capital and write it down before any real-money stage.
- Contain operational emergencies immediately; use the pre-tested policy for market drawdowns.

## Operations

- Use one Linux host, a registered static IP, systemd timers, a single-run lock, encrypted backups, and a tested restore.
- Daily cycle: check token and data, reconcile, compute picks, place only when explicitly authorized by the applicable stage, reconcile again, and report. Any red step holds.
- Alert from outside the host when a job misses its heartbeat.
- Keep morning login as a one-tap human step with a reminder; DDPI is required for sells.
- Keep the dashboard on localhost behind SSH. Secrets never enter logs or agent prompts. Without a login, the book holds.
