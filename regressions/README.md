# Committed regression tests

Each file pins one CONFIRMED demo vulnerability in the `bbj-regression/1` format: the scenario body, the contract version and the base-seed hash. Its expectation is that every check that failed now passes, with verdict NOT_REPRODUCED.

- **Generated from a real sandboxed run:** `PYTHONPATH=src uv run python scripts/export_demo_regressions.py`. That needs Docker and `make images`.
- **Checked without Docker:** `tests/unit/test_committed_regressions.py` confirms the hashes still match the current seed, scenarios and contract, which catches drift.
- **Executed in Docker:** `tests/integration/test_regressions_docker.py`, run with `pytest -m docker`. Each regression must pass against the hardened target **and** still fail against the vulnerable one, so it really detects the bug.
- **Retest through the API:** `bbj retest --file regressions/<key>.json`. This needs the regression test to exist in the database, so export it first with `bbj export <finding_id>`.

The `created_from` ids refer to the throwaway database of the generating run. They are provenance only. All data is synthetic.
