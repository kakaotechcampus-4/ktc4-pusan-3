"""Test shared"""
from app.rules.shared import helper
def test_helper():
    assert helper("test") == "TEST"
