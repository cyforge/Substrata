# Label Taxonomy and Triage Guidance

## Label Families

### Area Labels

- `area/oss`
- `area/sdk`
- `area/models`
- `area/signer`
- `area/wallet`
- `area/policy`
- `area/x402`
- `area/provider`
- `area/telemetry`

### Kind Labels

- `kind/docs`
- `kind/infra`
- `kind/feature`
- `kind/test`

### Contributor Fit Labels

- `fit/good-first-issue`
- `fit/intermediate`
- `fit/core-maintainer`

### Workflow Labels

- `status/blocked`
- `status/needs-context`
- `status/ready`

## Triage Workflow

1. Confirm the issue has enough context to be actionable.
2. Apply one `area/*` label and one `kind/*` label.
3. Add a contributor-fit label when the intended audience is clear.
4. Link blocking dependencies or mark the issue `status/blocked`.
5. If the issue is too large, split it before assigning or advertising it.

## Good First Issue Standard

Use `fit/good-first-issue` only when:

- the change is scoped to one clear area
- the first likely files are easy to find
- the acceptance criteria are concrete
- the issue does not depend on private context or deep protocol knowledge
