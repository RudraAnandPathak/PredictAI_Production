# Requirements coverage report

Rows: **490**. Source: `requirements_combined.json` (regenerate with `build_register.py`).

## By phase

- Phase 1: 1
- Phase 10: 12
- Phase 10A: 13
- Phase 10B: 11
- Phase 10C: 13
- Phase 10D: 11
- Phase 10E: 10
- Phase 11T: 16
- Phase 2: 8
- Phase 3: 32
- Phase 4: 14
- Phase 5: 39
- Phase 6: 184
- Phase 6c: 55
- Phase 7: 39
- Phase 8: 12
- Phase 9: 20

## Findings

- Rows with **no implementation location**: 153 ['P3-01', 'P3-02', 'P3-03', 'P3-04', 'P3-05', 'P3-06', 'P3-07', 'P3-08', 'P3-09', 'P3-10', 'P3-11', 'P3-12', 'P3-13', 'P3-14', 'P3-15', 'P3-16', 'P3-17', 'P3-18', 'P3-19', 'P3-20', 'P3-21', 'P3-22', 'P3-23', 'P3-24', 'P3-25']
- `Complete and verified` rows that **name no test file** (evidence is prose or a browser run from an earlier sandbox): 51
- Rows citing a **test file that does not exist**: 0 {}
- Rows whose status mapping is a judgement and should be reviewed by a person: 45 ['P3-30', 'P3-31', 'P3-32', 'P4-10', 'P4-14', 'DEL-04', 'CR-37', 'CR-45', 'P7-38', 'P8-01', 'P8-02', 'P8-03', 'P8-05', 'P8-06', 'P8-07', 'P8-08', 'P8-09', 'P8-10', 'P8-11', 'P8-12', 'P9-05', 'P9-11', 'P9-16', 'P9-18', 'P9-20']

## Rule applied

A row is never promoted to `Complete and verified` because code or UI exists: it needs an unqualified status in the source register. `Complete and verified` rows with no named test file are listed above so they can be audited; they are kept, not downgraded automatically, because many cite browser runs or prose evidence from earlier phases.
