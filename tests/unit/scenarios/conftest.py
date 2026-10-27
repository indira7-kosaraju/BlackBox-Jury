from __future__ import annotations

import pytest

from blackbox_jury.contracts.schema import TaskContract
from tests.unit.verifier.conftest import demo_contract


@pytest.fixture
def contract() -> TaskContract:
    return demo_contract()
