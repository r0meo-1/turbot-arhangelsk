"""Deliberate failure on a disposable validation PR; must never be merged."""


def test_required_regression_gate_blocks_a_failing_change():
    assert False, "Intentional negative control: verify failed CI blocks merging"
