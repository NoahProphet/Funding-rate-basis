# LBank venue assessment (D1)

Checked 2026-10-09 against LBank's public API docs and the ccxt `lbank`
implementation (master). Operator choice: LBank, symbols BTC and ETH.

## Verdict

**Blocked.** LBank's public documentation has no authenticated perpetual
(contract) API, so the short perp leg cannot be traded, monitored or
reconciled from code. Phase 1 cannot meet its scope (recording balances
and positions) and phases 4+ cannot place the hedge.

## Requirements checklist

| Requirement | Status | Evidence |
|-------------|--------|----------|
| Spot trading via API | yes | `POST /v2/supplement/create_order.do`, `api.lbank.info` |
| Spot client order id | yes | `custom_id`; max length not documented |
| Spot cancel-all | yes | `POST /v2/supplement/cancel_order_by_symbol.do` |
| Key permission check | yes | `POST /v2/supplement/api_Restrictions.do` returns `enableWithdrawals`, `ipRestrict` |
| Server time | yes | spot `/v2/timestamp.do`, contract `/cfd/openApi/v1/pub/getTime` |
| USDT-margined perps | yes | `lbkperp.lbank.com`, product group `SwapU` |
| Perp market data, funding rate | yes | `/cfd/openApi/v1/pub/{instrument,marketData,marketOrder}` |
| Perp order placement / cancel / query | **not documented** | contract docs list only the 4 public endpoints |
| Perp positions, liq price, margin ratio | **not documented** | same |
| Funding payment (income) history | **not documented** | same |
| Funding rate history | **not documented** | ccxt `fetchFundingRateHistory: False` |
| Spot <-> contract transfer via API | **not documented** | not in spot or contract docs; ccxt has no `transfer` |
| Dead man's switch | **not documented** | |
| Funding interval per symbol | unverified | may be in `instrument` response; check live |

ccxt supports LBank swap markets for market data only (funding rate,
tickers, order book); order, position and balance methods are spot-only.

## Operational notes

- API keys not bound to an IP expire after 30 days. Bind keys to the VPS IP.
- Contract rate limit errors use code 183; documented weights are not
  published for the contract API.

## Sources

- https://www.lbank.com/docs/contract.html
- https://www.lbank.com/en-US/docs/index.html
- https://github.com/ccxt/ccxt/blob/master/python/ccxt/lbank.py
- https://docs.ccxt.com/docs/exchanges/lbank/implicit-api
