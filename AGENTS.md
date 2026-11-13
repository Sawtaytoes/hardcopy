# AGENTS.md

`hardcopy` cleans a photograph of a printed page and sends it to an IPP printer. Read
`README.md` and `docs/decisions/` before changing behavior.

- This repository is publishable. It holds no printer address and no personal detail
  ([decision](docs/decisions/2026-09-10-hardcopy-holds-no-printer-address.md)).
- Run `ruff check .` and `pytest -q` before you push. CI runs both.
- Tests never send a job to a real printer. They build the bytes a printer would send.

## Independent adversarial review

Before integrating any change in this repository, including an authorized direct push
to the default branch, obtain review from at least one other agent that did not author
the changes. This includes code, tests, dependencies,
configuration, CI, and documentation; scale review depth to the risk. For risky designs,
seek an early challenge as well as review of the final diff. Give the reviewer the
requirements, applicable instructions, base/head SHAs, full diff, and test evidence.
Ask it to find correctness defects, regressions, edge cases, security problems, and
missing verification; a no-findings result is valid. Reviewers inspect and report;
they do not edit, merge, or deploy.

Resolve findings with evidence, fix confirmed defects, and re-run affected checks.
Unresolved material concerns block integration. Re-review substantive changes after fixes
or rebases so the final head is covered. Record reviewer identity, reviewed SHAs,
findings (or none), resolutions, and verification on the PR; for a direct-push workflow,
use a tracked review note instead. Self-review and passing
CI do not replace independent review. If the reviewer is unavailable, retry or use
another authorized agent; report the gate instead of silently waiving it. Only an
explicit owner instruction can waive the review, with its scope recorded in the same
review evidence.
Existing required checks, merge rules, and deployment verification still apply.
