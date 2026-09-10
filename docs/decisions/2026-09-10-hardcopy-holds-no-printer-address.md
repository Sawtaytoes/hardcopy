# hardcopy holds no printer address

- **Status:** Accepted
- **Date:** 2026-09-10
- **Type:** Privacy / repository scope
- **Supersedes:** None
- **Superseded by:** None

## Decision

`--host` is a required argument on `hardcopy print`. There is no default, no fallback environment variable holding a real address, and no printer name anywhere in the repository. A test asserts it: `test_no_printer_address_is_committed_in_this_repository` scans the source for anything shaped like an IP address and fails the build.

## Context

This code came out of a workspace script that printed to one specific household printer. That script carried the printer's address as an argument default, which was correct there: the workspace is private, and the address is the kind of detail it exists to record.

`hardcopy` is a separate, publishable repository.

## Why

A published repository must not tell a stranger anything about the owner's home. An address is small, easy to add back as a convenience, and easy to miss in review, so the rule is enforced by a test rather than by care. The test is cheap and it fails at the exact moment somebody re-adds a default.

Keeping the address out also makes the tool honest about what it is. It prints to a printer you name. It does not know about any particular one.

## Evidence

Owner decision on where the tool should live, 2026-09-10: *"add it to bin/ in a subfolder or a different repo because this one has no CI. Add it to one that does have CI"*, and on scope, *"Prepare and print"*. The workspace rule the test enforces: an app repository carries no personal detail.
