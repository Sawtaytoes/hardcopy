# A warning or report from the printer does not fail a print

- **Status:** Accepted
- **Date:** 2026-11-13
- **Type:** Behavior
- **Supersedes:** None
- **Superseded by:** None

## Decision

`wait_until_idle` fails a print only for a `printer-state-reasons` value that stops a job.
A value that ends in `-report` or `-warning` does not fail it. A value that ends in
`-error` fails it, and so does a value with no suffix, such as `media-jam`.

## Context

`wait_until_idle` is how `hardcopy print` confirms that pages reached paper. It used to
fail on every reason other than `none`. A printer with low toner reports
`toner-low-warning` for weeks while it prints normally, so every print in that time
ended with an error even though the pages came out.

## Why

IPP gives each reason a severity suffix: `-report`, `-warning`, or `-error`. Only an error
means the printer cannot go on. RFC 8011 says a reason with no suffix is an error, and
the reasons that matter most here, such as `media-empty` and `door-open`, can arrive
with no suffix. Blocking only on the literal `-error` suffix would let an empty tray pass
as a finished print, which is the failure this check exists to catch.

## Evidence

A code audit found that `wait_until_idle` treated `-report` and `-warning` reasons as
errors. Owner direction for the fix: *"Fix all those issues"*. Regression tests:
`test_wait_until_idle_passes_a_report_or_a_warning` and
`test_wait_until_idle_blocks_on_an_error_or_an_unsuffixed_reason` in `tests/test_ipp.py`.
