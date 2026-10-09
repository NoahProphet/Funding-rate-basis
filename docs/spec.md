# Funding-Rate Basis Bot: Architecture and Implementation Plan

Status: design spec, v0.1. Hand this file to Claude Code as the source of
truth. Sections marked **DECISION** must be filled in by the operator before
the phase that needs them.

---

## 0. Instructions for Claude Code

- Build strictly phase by phase (section 12). At the end of each phase:
  stop, summarize what was built, list deviations from this spec, and wait
  for review. Do not start the next phase unprompted.
- Never implement withdrawals or any call that moves funds off a venue.
  The only fund movement allowed is internal spot <-> futures wallet
  transfer on the same account, and only from phase 6.
- API keys must have withdrawal permission disabled. Where the venue API
  exposes key permissions, the bot must refuse to start if withdrawal is
  enabled. Verify whether the chosen venue exposes this; do not assume.
- Do not change default risk limits (section 15) without asking.
- All prices, quantities, balances and PnL use `decimal.Decimal`. No floats
  anywhere in the money path. Round to venue tick size and step size.
- All timestamps stored and logged in UTC.
- Do not guess venue-specific behavior (endpoint names, funding intervals,
  margin modes, rate-limit weights). Read the venue docs or the library
  source, and flag anything unverified in the phase summary.
- Follow the coding and git conventions in section 13.

---

## 1. Strategy summary

Delta-neutral cash-and-carry on a single venue (v1):

- **Long spot** asset X, **short perpetual** on X, equal base quantity.
- Price moves on the two legs offset; the position earns the perp
  **funding rate** when funding is positive (longs pay shorts).
- Enter when expected funding yield, net of fees, is above an entry
  threshold for a sustained period. Exit when it falls below a lower exit
  threshold (hysteresis avoids churn).
- When funding turns persistently negative, go flat. Reverse carry
  (short spot via borrow) is out of scope for v1.

Annualized funding:

    funding_apr = funding_rate * periods_per_day * 365

The funding interval varies by venue and sometimes by symbol. Read it from
the API; never hardcode 8h.

Net expected yield used for decisions:

    net_apr = ema(funding_apr, n_periods)
              - round_trip_fees / expected_hold_days * 365

---

## 2. Open decisions

| # | Decision | Notes |
|---|----------|-------|
| D1 | **DECISION** Venue | Must offer spot and USDT-margined perps on the same account, internal transfers via API, and must permit your jurisdiction under its ToS. |
| D2 | **DECISION** Symbols | Start with BTC and ETH only. |
| D3 | **DECISION** Capital for paper, live-small and target | Live-small should be an amount you are fully prepared to lose to a bug. |
| D4 | **DECISION** Hosting provider and region | Dedicated VPS, not shared hosting. Watchdog on a different provider. |
| D5 | **DECISION** Dashboard access method | WireGuard, Tailscale or SSH tunnel. Never public. |
| D6 | Entry/exit thresholds | Set from the phase 3 backtest, not by intuition. |

---

## 3. Non-goals for v1

- Multi-venue hedging (spot on venue A, perp on venue B).
- Reverse carry / spot borrowing.
- Latency optimization, colocation, market making.
- Any directional strategy logic.
- Mobile app. The dashboard must work in a mobile browser.

---

## 4. System overview

Processes (separate containers):

```
+------------------------------- core --------------------------------+
|                                                                      |
|  market_data --> strategy --target--> risk --approved--> execution --+--> venue
|       ^                                ^  |                          |
|       |                                |  +--halt/veto--> controller |
|  venue WS/REST                   reconciler <-- venue state ---------+
|                                        |                             |
|                    accounting <--------+                             |
|                                                                      |
|  controller (state machine)   control_api (localhost, token auth)   |
|  metrics endpoint             event writer --> postgres             |
+----------------------------------------------------------------------+
        |                    |                          |
        v                    v                          v
   prometheus          notifier (telegram)        dashboard (fastapi)
   (optional grafana)  LISTEN/NOTIFY on events    reads postgres,
                       commands -> control_api    controls -> control_api

   watchdog (separate host): polls core heartbeat, alerts via telegram
```

Design principles:

1. **Core is the only process that talks to the venue with trading keys.**
   The dashboard and notifier never hold venue keys.
2. **Strategy outputs target state, not orders.** Execution diffs target
   against actual and decides orders. This makes restarts safe.
3. **The exchange is the source of truth.** Local state is a cache. The
   reconciler rebuilds state from the venue on startup and periodically.
4. **Risk engine is an independent gate.** It can veto, scale down or halt.
   Strategy cannot bypass it.
5. **Remote control can only reduce risk**, with one exception: `resume`,
   which needs explicit confirmation. No remote command can raise limits,
   change config, or move funds.
6. **A crashed bot cannot alert.** Liveness is checked from outside
   (watchdog on another host).

---

## 5. Components

### 5.1 Venue adapter (`venues/`)

Abstract interface, one implementation per venue. Methods (async):

- instrument metadata: tick size, step size, min notional, contract size,
  funding interval
- spot and perp order book top / depth, mark price, index price
- current and predicted funding rate, funding history
- balances (spot wallet, futures wallet), perp positions with liquidation
  price and margin ratio
- place order, cancel order, query order by client order id, open orders
- fills / trade history since cursor
- funding payment history (income) since cursor
- internal transfer spot <-> futures (phase 6 only)
- key permissions check (if venue supports it)

Implementation: `ccxt` async where it covers the needed endpoints; venue
SDK or raw REST where it does not. Verify coverage for funding history,
income history and internal transfers before choosing.

Rate limiting: client-side token bucket mirroring venue weights; expose
current usage as a metric.

### 5.2 Market data (`market_data/`)

- WebSocket streams for books, mark price, funding; REST polling fallback.
- Every value carries a receive timestamp. A value older than
  `max_data_staleness_s` is **stale**. Stale data blocks new risk-increasing
  orders.
- Reconnect with exponential backoff and jitter; resubscribe; emit events.
- Recorder writes funding rates and periodic price snapshots to the DB for
  backtesting and the dashboard.

### 5.3 Strategy (`strategy/`)

Pure function of market data plus config, no I/O:

- Compute `funding_apr`, its EMA, predicted next funding, `net_apr`.
- Entry condition: `net_apr >= entry_apr` for `entry_confirm_periods`
  consecutive periods.
- Exit condition: `net_apr <= exit_apr`, or predicted funding negative for
  `exit_confirm_periods` periods.
- Output per symbol: `target_hedge_notional_usd` (0 or configured size).
- Never outputs orders.

### 5.4 Risk engine (`risk/`)

Evaluates every target and every order before it goes out, and continuously
evaluates live state. Limits (defaults in section 15):

- max notional per symbol and total
- max perp leverage
- min liquidation distance: `(liq_price - mark) / mark` for a short
- max net delta in USD (spot qty minus perp qty, times price)
- max daily loss as % of NAV (realized + unrealized, UTC day)
- max single order notional, max orders per minute
- max data staleness, max clock drift vs venue server time
- max consecutive API errors

Outcomes: `allow`, `scale(new_size)`, `deny(reason)`, `halt(reason)`.
Every outcome other than `allow` is written to the events table with the
inputs that caused it.

Risk-reducing actions (re-hedging a naked leg, reducing both legs, adding
margin) are allowed in every state except `KILLED`.

### 5.5 Execution (`execution/`)

Converts the diff between target and actual into orders.

Leg policy for entry (exit is symmetric):

1. Split the change into chunks of at most `max_chunk_notional_usd`.
2. Leg A (the less liquid instrument, decided per symbol from book depth):
   limit order at or near touch.
3. On each leg A fill, immediately hedge the filled quantity on leg B with
   a marketable limit order, price capped at mid +/- `max_slippage_bps`.
4. If leg B is not filled within `hedge_timeout_s`, retry with a wider cap
   up to `max_hedge_slippage_bps`; if still unfilled, unwind the leg A fill
   and raise a CRIT alert.
5. Unhedged exposure at any moment is therefore bounded by one chunk.

Requirements:

- Deterministic client order ids (`<run_id>-<symbol>-<seq>`) persisted
  before submission. On timeout or network error, query by client id
  before resubmitting. Never blind-retry a submit.
- Quantity conversion between spot base units and perp contracts in one
  place, with tests. Residual delta from step-size rounding must stay
  within `max_net_delta_usd`.
- Orders and fills are written to the DB on every state change.

### 5.6 Reconciler (`reconcile/`)

- On startup: load all open orders, positions, balances from the venue,
  compare with DB, adopt venue state, log differences. The bot must not
  trade until this succeeds (`RECONCILING` state).
- Every `reconcile_interval_s`: repeat the comparison.
- Mismatch beyond tolerance (unknown position, unknown open order, quantity
  diff above threshold): move to `HALTED`, CRIT alert. Do not auto-fix
  unknown positions; a human decides.
- Unknown open orders with our client id prefix are cancelled; orders
  without our prefix are reported, never touched.

### 5.7 Margin manager (`margin/`, phase 6)

- Watches liquidation distance on the short perp leg.
- Below `margin_topup_trigger_pct`: transfer from spot wallet stablecoin
  balance to futures wallet, up to `max_transfer_usd` per hour.
- If no free balance: reduce both legs proportionally (risk-reducing).
- Every transfer is an event and a WARN alert.

Before phase 6, liquidation distance below threshold triggers proportional
reduction plus alert instead of a transfer.

### 5.8 Accounting (`accounting/`)

- NAV in USD: spot balances at mark + futures wallet balance + unrealized
  PnL.
- Daily PnL attribution: funding received, trading fees, basis change
  (spot PnL + perp PnL), residual delta PnL.
- Funding payments are taken from the venue income history, not estimated.
- Daily snapshot rows for the dashboard and the daily Telegram summary.

### 5.9 Controller and state machine (`core/`)

```
STARTING -> RECONCILING -> RUNNING <-> PAUSED
                              |           |
                              v           v
                            HALTED  <-----+
                              |
                              v
                            KILLED
```

| State | New entries | Exits / reductions | Re-hedge naked leg | Orders |
|-------|-------------|--------------------|--------------------|--------|
| RUNNING | yes | yes | yes | yes |
| PAUSED | no | yes | yes | yes |
| HALTED | no | only if risk engine requires | yes | risk-reducing only |
| KILLED | no | optional flatten (config) | no | cancel all, then stop |

- `HALTED` is entered automatically by the risk engine or reconciler and
  requires a manual `resume` after the cause is cleared.
- `KILLED` cancels all open orders. If `kill_flattens: true`, it closes both
  legs reduce-only with slippage caps before stopping. Default is false,
  because panic-flattening in a fast market can cost more than holding a
  hedged position.
- Where the venue supports an exchange-side dead man's switch (auto-cancel
  open orders after a countdown), core refreshes it on each loop. Verify
  availability per venue.

### 5.10 Control API (`control_api/`)

- Listens on 127.0.0.1 or a unix socket only. Bearer token from secrets.
- `GET /health`, `GET /status`
- `POST /pause`, `POST /resume`, `POST /halt`, `POST /kill`
- Every call is written to the events table with source (dashboard,
  telegram user id) and timestamp.

---

## 6. Data model (PostgreSQL)

| Table | Contents |
|-------|----------|
| `instruments` | venue, symbol, type (spot/perp), tick, step, min notional, contract size, funding interval |
| `funding_rates` | ts, symbol, rate, predicted rate, interval |
| `price_snapshots` | ts, symbol, spot bid/ask, perp mark, index |
| `orders` | client id, venue id, symbol, leg, side, type, price, qty, status, created/updated ts, reason |
| `fills` | venue fill id, client id, price, qty, fee, fee asset, ts |
| `position_snapshots` | ts, symbol, spot qty, perp qty, net delta usd, liq price, liq distance, margin ratio |
| `balance_snapshots` | ts, wallet, asset, free, locked |
| `funding_payments` | venue id, ts, symbol, amount |
| `pnl_daily` | date, nav, funding, fees, basis pnl, delta pnl, total |
| `events` | ts, severity, type, key, payload json, source |
| `risk_decisions` | ts, check, inputs json, outcome, reason |
| `config_versions` | ts, hash, full config json |

Migrations with Alembic. `events` uses `NOTIFY` on insert so the notifier
and dashboard can stream without polling.

Retention: keep everything except `price_snapshots`, which can be
downsampled after 90 days.

---

## 7. Web dashboard

Stack: FastAPI + Jinja2 templates + htmx for partial updates + a small
charting library (Chart.js or lightweight-charts). Live values via SSE fed
from Postgres NOTIFY. No SPA build chain unless there is a real need.

Access and security:

- Binds to 127.0.0.1; reached via the tunnel chosen in D5. Never exposed
  on a public interface.
- Single admin account, password hashed with argon2, TOTP second factor.
- Session cookies `HttpOnly`, `Secure`, `SameSite=Strict`; CSRF token on
  every POST.
- Read-only by default; control actions require re-entering TOTP and a
  confirmation dialog.
- Holds no venue keys; talks to core only through the control API.

Pages:

1. **Overview**: bot state (big, colored), NAV, today / 7d / 30d PnL,
   funding earned to date, net delta, worst liquidation distance, active
   alerts, last reconcile time. Control buttons: pause, resume, halt, kill.
2. **Positions**: per symbol spot qty, perp qty, net delta, entry basis,
   current basis, current and EMA funding APR, next funding time,
   liquidation price and distance, margin ratio.
3. **Funding**: funding APR history per symbol (chart), predicted vs
   realized, entry/exit thresholds drawn on the chart, funding payments
   table.
4. **PnL**: NAV curve, daily attribution stacked bars (funding, fees,
   basis, delta), cumulative fees.
5. **Orders and fills**: filterable table, status, slippage vs mid at
   submission, leg timing (time to hedge).
6. **Risk**: every limit with current value and utilization %, recent
   risk decisions with reasons.
7. **System health**: WS connection status and last message age per
   stream, data staleness, API error rate, rate-limit usage, clock drift,
   loop latency, last reconcile result, DB and notifier status, process
   uptime.
8. **Events**: searchable audit log of everything in `events`.
9. **Config**: read-only view of the active config and its hash.

---

## 8. Monitoring and metrics

Core exposes a Prometheus endpoint on localhost. Suggested metrics:

- `basis_bot_state` (gauge, enum as labels)
- `basis_bot_nav_usd`, `basis_bot_pnl_today_usd`
- `basis_bot_net_delta_usd{symbol}`
- `basis_bot_liq_distance_pct{symbol}`
- `basis_bot_funding_apr{symbol,kind="current|ema|predicted"}`
- `basis_bot_data_age_seconds{stream}`
- `basis_bot_ws_connected{stream}`
- `basis_bot_api_errors_total{endpoint,code}`
- `basis_bot_rate_limit_usage_ratio`
- `basis_bot_orders_total{leg,status}`
- `basis_bot_hedge_latency_seconds` (histogram)
- `basis_bot_unhedged_notional_usd{symbol}`
- `basis_bot_clock_drift_ms`
- `basis_bot_loop_duration_seconds` (histogram)
- `basis_bot_reconcile_mismatch_total`
- `basis_bot_heartbeat_timestamp`

Prometheus and Grafana are optional (phase 2b). The custom dashboard is the
operator UI; Grafana is for long-range system metrics.

**Watchdog** (separate host and provider):

- Polls the core heartbeat through the tunnel every 30s.
- Alerts CRIT via Telegram if the heartbeat is older than
  `watchdog_timeout_s` or the endpoint is unreachable.
- Optionally, with a read-only venue key, checks independently that
  positions are still hedged and alerts if not. This catches the case where
  core is alive but wrong.

Logs: structured JSON (structlog) to stdout, collected by Docker, rotated.
Every log line carries `run_id`, and order-related lines carry the client
order id.

---

## 9. Telegram alerts and commands

Notifier is a separate process consuming `events` via LISTEN/NOTIFY. If the
Telegram API is slow or down, core is unaffected.

### 9.1 Alerts

| Severity | Events |
|----------|--------|
| CRIT | naked leg above tolerance for longer than `hedge_timeout_s`; hedge unwind triggered; liquidation distance below minimum; reconcile mismatch; state -> HALTED or KILLED; daily loss limit hit; venue auth failure; watchdog: core unreachable |
| WARN | liquidation distance below warn level; WS disconnected longer than threshold; stale data; API error rate high; rate-limit usage above 80%; funding turned negative on a held position; order rejected; clock drift above threshold; margin transfer executed; risk engine denied or scaled a target |
| INFO | position opened, increased, reduced, closed; bot start/stop; state -> PAUSED/RUNNING; config version changed; daily summary |

Daily summary (fixed UTC time, configurable): NAV, PnL by attribution,
funding earned, positions, worst liquidation distance, alerts in the last
24h.

Delivery rules:

- Deduplicate by alert key within `alert_dedup_window_s`; repeat with a
  counter, and escalate if a WARN persists past `warn_escalate_after_s`.
- Quiet hours apply to INFO only. CRIT always delivers.
- CRIT messages include the bot state, the cause, and the suggested next
  action.

### 9.2 Commands

Accepted only from whitelisted Telegram user ids in whitelisted chat ids;
anything else is ignored and logged.

- `/status`, `/positions`, `/pnl`, `/health`
- `/pause`, `/halt`
- `/resume`: replies with a one-time code; must be confirmed within 60s
- `/kill`: same confirmation flow

No command changes config, limits or moves funds.

---

## 10. Failure handling

| Failure | Detection | Response |
|---------|-----------|----------|
| WS disconnect | no message within threshold | reconnect; data stale -> block risk-increasing orders; WARN |
| REST errors | error counter | backoff; above limit -> HALTED; WARN/CRIT |
| Rate-limit near cap | usage ratio | slow loop, defer non-essential calls; WARN |
| Leg B unfilled | hedge timer | widen cap, then unwind leg A; CRIT |
| Order submit timeout | no ack | query by client id before any retry |
| Core crash mid-entry | restart | RECONCILING; adopt venue state; re-hedge if needed |
| Reconcile mismatch | reconciler | HALTED; CRIT; human review |
| Liq distance low | risk engine | top up margin (phase 6) or reduce both legs; WARN/CRIT |
| Daily loss limit | accounting | HALTED; CRIT |
| Clock drift | server time check | block orders until fixed; WARN |
| DB unavailable | write failure | HALTED (cannot trade without audit trail); CRIT via direct Telegram call |
| Telegram down | notifier errors | log; dashboard still shows alerts |
| Venue maintenance | venue status / errors | PAUSED; INFO |
| Funding interval change | instrument refresh | recompute APR; WARN |

---

## 11. Security and deployment

Host:

- Dedicated VPS. SSH key-only, non-root user, firewall deny-all inbound
  except SSH and the tunnel port. Unattended security updates.
- chrony for time sync; drift is also checked against venue server time.
- Docker Compose services: `core`, `notifier`, `dashboard`, `postgres`,
  optional `prometheus`, `grafana`. Restart policy `unless-stopped`.
- Watchdog on a different provider.

Secrets:

- Venue API keys: trade-only, withdrawals disabled, IP whitelisted to the
  VPS. Separate subaccount for the bot if the venue supports it.
- Secrets in a file outside the repo with mode 0600 or Docker secrets;
  never in config files, env dumps, logs or the DB.
- Dashboard and notifier never receive venue keys.

Supply chain:

- Lock dependencies with hashes (uv or pip-tools). Review new packages
  before adding. Crypto-key-stealing packages on PyPI and npm are a known
  pattern.

Backups: daily `pg_dump`, encrypted, copied off-host; restore tested once
in phase 5.

---

## 12. Phased plan

Each phase ends with a review. Acceptance criteria (AC) must be met before
moving on.

### Phase 0: Skeleton

- Repo layout (section 14), `pyproject.toml`, ruff and mypy config.
- Config loading with pydantic validation; example config.
- Structured logging, `run_id`.
- DB schema and Alembic migrations.
- Docker Compose with postgres and an idle core.

AC: `docker compose up` starts and stops cleanly; an invalid config is
rejected with a clear error.

### Phase 1: Read-only venue integration

- Venue adapter (read methods only), using a **read-only key**.
- Market data streams with staleness tracking and reconnect.
- Recorder for funding rates, prices, balances, positions.

AC: runs 24h unattended; recovers from a forced WS disconnect; DB contains
continuous funding and price data.

### Phase 2: Observability first

- Dashboard: overview, funding, system health, events (read-only).
- Metrics endpoint. Optional Prometheus/Grafana (2b).
- Notifier with infra alerts (WS, staleness, errors, start/stop).
- Watchdog on a second host.

AC: killing core produces a watchdog CRIT within 2 minutes; WS drop
produces WARN; dashboard reachable only through the tunnel; login requires
TOTP.

### Phase 3: Strategy, risk, backtest, paper targets

- Funding backtester on recorded plus downloaded historical funding and
  prices, with fee and slippage assumptions. Output: thresholds and
  expected yield ranges for D6.
- Strategy and risk engine.
- Paper mode: targets and risk decisions logged and shown on the dashboard
  as hypothetical positions.

AC: backtest report reviewed and thresholds chosen; risk engine tests pass
(section 14); paper mode runs 7 days.

### Phase 4: Execution and reconciliation on simulated fills

- Execution engine with leg policy, simulated fills against the live book.
- Reconciler, state machine, control API.
- Dashboard controls and Telegram commands.
- Full alert set from section 9.

AC: scripted scenarios handled correctly (leg B never fills, restart
mid-entry, duplicate submit after timeout, reconcile mismatch, stale data
during entry); 7 days of simulated trading with unhedged exposure never
above one chunk.

### Phase 5: Live, minimal size

- Trade-enabled key with withdrawals disabled.
- Live size from D3 (live-small).
- Exchange-side dead man's switch if available.
- Daily summary, backup and restore test.

AC: 2 to 4 weeks live; zero unexplained reconcile mismatches; accounting
matches the venue's own statements within tolerance; every CRIT reviewed.

### Phase 6: Margin manager and scaling

- Internal transfers for margin top-up.
- Multiple symbols.
- Capital ramp rule: increase size only after N clean weeks, in steps no
  larger than 2x.

Later (separate spec): multi-venue hedging.

---

## 13. Coding and git conventions

Code:

- Python 3.12, PEP 8, ruff, type hints everywhere, mypy strict for `risk/`,
  `execution/`, `reconcile/`, `accounting/`.
- snake_case; no camelCase, no Hungarian notation.
- Lines up to ~80 columns.
- Short focused functions; early returns instead of nested conditionals;
  maximum nesting depth 3, refactor beyond that.
- Comments in English only, sparse, explaining why rather than what. No
  decorative or obvious comments.
- `Decimal` for all money and quantity values.
- No hidden global state; components get dependencies passed in.

Git:

- One logical change per commit, in logical order.
- Subject: imperative, capitalized, at most 50 characters, no trailing
  period, optional component prefix (`execution: Add hedge timeout`).
- Blank line, then a body wrapped at ~72 columns explaining what and why,
  not how.
- `Signed-off-by:` on every commit.
- Rebase local work onto upstream; do not merge upstream into branches;
  never rewrite published history.

---

## 14. Repository layout and testing

```
basis-bot/
  pyproject.toml
  config/
    config.example.yaml
  migrations/
  src/basis_bot/
    core/            controller, state machine, main loop
    venues/          adapter interface and implementations
    market_data/
    strategy/
    risk/
    execution/
    reconcile/
    margin/
    accounting/
    storage/
    control_api/
    notifier/
    dashboard/       app, templates, static
    backtest/
  watchdog/
  deploy/
    docker-compose.yml
  tests/
```

Tests are mandatory for the money path, because the costly failures of this
system are logic bugs, not strategy losses:

- unit conversion spot qty <-> perp contracts, rounding to step size
- risk engine: every limit, boundary values, deny/scale/halt outcomes
- execution: leg policy, partial fills, hedge timeout and unwind,
  idempotent resubmission
- reconciler: each mismatch type
- state machine: allowed transitions and allowed actions per state
- accounting: PnL attribution against a hand-computed example

Use a fake venue adapter for these; no network in unit tests.

---

## 15. Default configuration

Conservative placeholders for paper and live-small. Tune from backtest
results; changes to risk defaults require operator approval.

```yaml
venue:
  name: TBD                    # D1
  symbols: [BTC, ETH]          # D2

strategy:
  entry_apr: 0.15              # placeholder, set from backtest
  exit_apr: 0.05
  entry_confirm_periods: 3
  exit_confirm_periods: 2
  ema_periods: 6
  expected_hold_days: 14
  target_notional_usd:
    BTC: 0                     # set per D3
    ETH: 0

risk:
  max_total_notional_usd: 0    # set per D3
  max_symbol_notional_usd: 0
  max_perp_leverage: 2
  min_liq_distance_pct: 40
  warn_liq_distance_pct: 50
  max_net_delta_usd: 50
  max_daily_loss_pct_nav: 1.0
  max_single_order_usd: 0      # set per D3
  max_orders_per_minute: 30
  max_data_staleness_s: 5
  max_clock_drift_ms: 500
  max_consecutive_api_errors: 5

execution:
  max_chunk_notional_usd: 0    # set per D3
  max_slippage_bps: 5
  max_hedge_slippage_bps: 20
  hedge_timeout_s: 10

reconcile:
  interval_s: 30
  qty_tolerance_ratio: 0.001

control:
  kill_flattens: false

margin:                        # phase 6
  topup_trigger_pct: 45
  max_transfer_usd_per_hour: 0

alerts:
  dedup_window_s: 300
  warn_escalate_after_s: 900
  daily_summary_utc: "06:00"
  quiet_hours_utc: null

watchdog:
  timeout_s: 120
```
