# Verified testnet settlement

On November 4, 2026, Substrata completed a live x402 `exact` payment on
`stellar:testnet`. The transaction transferred **0.001 test XLM** through the
native XLM Soroban asset contract. Test accounts received their starting balances
from Friendbot.

## On-chain evidence

| Field | Observed value |
| --- | --- |
| Transaction | `2c139bcf56531af0c03e4aef9fe3d6e63514371aa8c27d66ee82b5e479144453` |
| Status | `SUCCESS` in Stellar RPC; `successful: true` in Horizon |
| Ledger | `5553661` |
| Closed at | `2026-11-04T11:32:25Z` (12:32:25 p.m. Africa/Lagos) |
| Transfer amount | `10000` atomic units, or `0.0010000` XLM |
| Fee charged | `10394` stroops, or `0.0010394` XLM |
| Operations | `1` |
| Asset contract | `CDLZFC3SYJYDZT7K67VZ75HPJVIEUVNIXF47ZG2FB2RMQQVU2HHGCYSC` |

[Read the confirmed transaction in Horizon](https://horizon-testnet.stellar.org/transactions/2c139bcf56531af0c03e4aef9fe3d6e63514371aa8c27d66ee82b5e479144453).

Balances were queried independently through Horizon before and after settlement:

| Account role | Before (XLM) | After (XLM) | Change (XLM) |
| --- | ---: | ---: | ---: |
| Payer | 10000.0000000 | 9999.9990000 | -0.0010000 |
| Recipient | 10000.0000000 | 10000.0010000 | +0.0010000 |
| Facilitator | 10000.0000000 | 9999.9989606 | -0.0010394 |

The payer was `GCHTZUOASH2GA7ZDCZG2CMIR6Y6RSF5RC34XXI762CLQK32QISCMNHUX`.
The recipient was `GCBIQ6VTBPDHAOAANDSGX6AY4P2GCRYGFMDVW74KAAFIEC2KXKKW5IJQ`.
The transaction source and fee payer were the separate facilitator account,
`GCHV2H7XBI7XIBBZU3DY7S3P6JF5OKBGGXGMDAMG6VHQENK3SOJOBM6F`.

## Exercised flow

The tested SDK revision was `a7782c5e56592200e4161d5aa5589c6e7d7614ab`.
Python used `stellar-sdk==17.0.0`. A local test harness hosted the official
`@x402/stellar==2.29.0` facilitator mechanism, configured against
`https://soroban-testnet.stellar.org`.

1. `BuyerClient` requested a protected HTTPS endpoint on loopback and received a
   402 challenge from `WSGIMiddleware`.
2. The buyer checked an explicit spend policy and reserved 0.001 XLM in a
   file-backed ledger. The per-payment, daily, and total limits were each 0.001 XLM.
3. `StellarAuthSigner` read the live ledger, checked token decimals, simulated the
   transfer, and signed its authorization entry. It did not sign the transaction
   envelope or submit the payment.
4. `PaymentGate` used `HTTPFacilitator` to verify the payment, derived its
   authorization identity with `StellarProofIdentityExtractor`, and persisted a
   replay claim before settlement.
5. The official facilitator independently verified the payload, sponsored the
   transaction fee, submitted it to testnet, and returned the confirmed hash.
6. The provider released its protected content with HTTP 200 and a
   `PAYMENT-RESPONSE`. The buyer recorded the payment as `settled`.
7. Independent RPC and Horizon reads confirmed the transaction and exact balance
   changes. The local audit chain verified successfully, and the replay store
   contained one claim.

The provider used a temporary TLS certificate. The harness HTTP transport trusted
that certificate explicitly and disabled redirects. Signing, facilitator
verification, submission, and on-chain confirmation used real network calls.
The local harness was stopped after the test; private keys were excluded from
the repository and report.

This result covers native XLM on testnet with a locally hosted official
facilitator. USDC, managed facilitator deployments, and public-network payments
were not exercised by this run.
