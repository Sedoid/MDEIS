# Aurora methodology fixture

`methodology-aurora.pdf` is the evaluation methodology of Aurora, a fictional school. It is
synthetic, supplied by the owner, and has no real school or learner in it. `methodology-aurora.md`
is a faithful Markdown copy (every sentence verbatim, gaps and all). `methodology-aurora.cases.json`
holds Appendix B (F-01 to F-16) and the four worked examples of section 8 (W-1 to W-4), unchanged.

It is here because it is structurally different from v2.0, for the clarity gate and the build pipeline.
It is a document to be judged, so nothing in it was fixed.

## Faults a good gate should report on this document

- complementary suppression has no method (section 10, AUR-06, Appendix C, F-15 says a cell is suppressed but not which)
- reconstruction checks for repeated queries and related reports have no algorithm (section 10)
- the "Mixed" label (section 8) does not say what counts as different labels, and sits awkwardly with "never compared across subjects" (section 7)
- "term" is used for course-based upper secondary (sections 8 and 9: window, "spanning at least 2 terms", recency) but is not defined for courses
- vague wording with no definition: "important findings" (section 2 step 10), "where feasible" (section 4), "where lawful" (section 12)
- the version is marked draft and its own numbers are marked illustrative until approved (title block, Appendix C)

## Things a gate must NOT report as faults on this document

- its settings table is in section 4 (not 3.1)
- section 6.3 is a list of competences (not a measure)
- its numbered headings are valid although the PDF's font names do not contain "Bold"

## Amended copy

`methodology-aurora-amended.md` is a copy of `methodology-aurora.md` with only the edits that remove
the six faults the clarity gate reports on the original. `methodology-aurora-amended.cases.json` is
the original cases file plus A-1 to A-16, which test the added rules. What was changed:

1. Section 10: a complementary suppression rule as numbered steps, with two worked examples (one matches F-15).
2. Section 10: the reconstruction check is now a rule as numbered steps (compare with each earlier report of the same school year, suppress a single subgroup smaller than 10), with one example.
3. Section 2 steps 8 and 10: "important" is replaced by a defined trigger (Declining, Fluctuating or Insufficient evidence goes to the subject teacher; evidence is cited for each statement of a grade, a result or a label).
4. Section 4: "where feasible" is replaced by a condition (records under both versions exist for the same learner and subject).
5. Section 12: "where lawful" is replaced by a condition (reviewer, date and corrections are always recorded; a dissenting view is recorded when the reviewer enters one).

The added rules are proposals for testing and were never approved by any school. The clarity gate
should report 0 faults on the amended copy. The "Mixed" label and "term for courses" issues remain on
purpose.
