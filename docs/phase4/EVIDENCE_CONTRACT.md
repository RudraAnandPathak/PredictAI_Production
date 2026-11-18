# Evidence contract `predictai.evidence/1`

Every analysis result carries an evidence block. Status is **VERIFIED** only when all required parts are present and the
computation completed; otherwise **INCOMPLETE**, with the missing parts listed and the reason. The UI never labels an
INCOMPLETE result as verified.

Required parts: `source` (file, version number and kind, format, SHA-256 checksum, rows in dataset and in scope, row snapshot with the exact filters),
`calculation` (calc version, plan hash, operations with method, run time, library versions, isolation mode and limits),
`inputs` (selected columns, null policy, seed, transformation lineage), `interpretation` (method, sample size, assumptions,
uncertainty, limitations, causal-language guard), `access` (who/which dataset version was authorised, warnings).

- **Reproducibility:** `rerun` executes the stored plan again on the same dataset version and returns a `reproduction` block
  comparing `result_hash`; differing hashes are reported with an explanation (e.g. different library version).
- **Assistant:** each numeric fact is additionally recomputed by an independent standard-library implementation
  (`assistant/verify.py`). PASSED / FAILED / NOT_CHECKED is shown separately from evidence status. A FAILED check withholds the answer and marks the evidence INCOMPLETE.
- **Insights** embed the same fields plus the 10 required insight fields; they are inspectable at any time from the Insights tab.
