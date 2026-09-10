# A page photo is flattened by division, never by a threshold

- **Status:** Accepted
- **Date:** 2026-09-10
- **Type:** Image pipeline
- **Supersedes:** None
- **Superseded by:** None

## Decision

`flatten` divides the image by a heavily blurred copy of itself, then applies one contrast stretch. It does not threshold, and it does not binarise. The output stays 8-bit grey.

## Context

A photograph of a printed page carries an illumination gradient. The lit corner and the shadowed corner of the same sheet of paper can differ by more than ink differs from paper on either one of them. The page also sits on a surface, so the frame holds a dark border, and often a thumb.

The obvious approach is a threshold, global or adaptive, producing black ink on white paper.

## Why

Two failures, both fatal for the pages this tool is for.

1. **One brightness does not fit the frame.** A global threshold set for the lit side turns the shadowed side solid black. Dividing by a local background removes the gradient first, after which a single stretch works over the whole page.
2. **A threshold destroys thin marks.** A staff line, a table rule and a stem are about three pixels wide at 300 dpi, and they are grey at their edges. A hard cut erases them or breaks them into dashes. Division preserves the anti-aliasing that makes them readable, which is why the result stays grey rather than becoming bilevel.

The blur kernel is a twelfth of the short side. It has to be much wider than any glyph, or the division subtracts the notation along with the shading, and much narrower than the shadow, or it does not remove it.

The stretch runs to 235 rather than 255. Paper after division sits near white but not at it, and stretching to the true maximum leaves the page visibly grey on paper.

## Evidence

Measured on two photographs of a page of piano music taken on a phone, 2026-09-10. The raw frame carried a left-to-right shadow, a thumb over the left margin, and the dark surface at three corners. After division both sides of the paper read above 200 while the printed rules stayed below 110, and every rule survived to the printed sheet.

Owner request that produced the tool: *"I just added two piano sheets in `Downloads/` for Phantom of the Opera. Can we print that?"*
