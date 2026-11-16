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
