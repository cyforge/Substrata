# Usable SDK milestone

Substrata's first usable release should let a developer run a paid request locally,
inspect its policy decision and ledger record, and replace the local payment adapter
with an x402 facilitator without changing the application flow.

## Delivery slices

1. Shared models and spend policy: exact amounts, explicit assets and networks,
   structured outcomes, and policy decisions that explain denied payments.
2. Durable accounting: reserve funds before authorization, retain uncertain payments,
   and inspect records after restarting the process.
3. x402 v2 protocol: bounded header parsing, typed requirements and receipts, and
   a facilitator client with explicit timeouts and no settlement retries.
4. Buyer SDK: check policy before signing, bind authorization to the offered payment,
   retry a paid request once, and preserve accounting when the outcome is uncertain.
5. Provider integration: protect configured routes, verify and settle accepted
   payments, reject replayed proofs, and expose machine-readable failures.
6. Developer workflow: runnable offline demo, CLI commands, installation instructions,
   package builds, and CI across supported Python versions.

## Acceptance checks

- A fresh virtual environment can install the wheel and run the documented demo.
- The demo completes a paid request without external accounts or network access.
- Denied requests do not sign or submit payment authorization.
- Concurrent reservations cannot spend beyond the configured ledger limit.
- An uncertain settlement remains counted until an operator reconciles it.
- Provider content is released only after successful payment validation.
- Automated tests cover malformed input, replay, failures, and restart behavior.
- Live Stellar support states its tested boundaries and required external setup.

Commit timestamps for this milestone use a retrospective July–November 2026
schedule. Implementation and verification take place on November 4, 2026.
GitHub issue and pull request timestamps reflect their actual creation times.
