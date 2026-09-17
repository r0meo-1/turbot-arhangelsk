"""Regression tests for live MDT add-lead response compatibility."""

from shared import mdt as mdt_shared
from shared.mdt_live_compat import _live_result_is_success, _source_accepts_result_only


def test_result_only_success_shapes_are_accepted():
    assert _live_result_is_success({"result": True}) is True
    assert _live_result_is_success({"result": "ok"}) is True
    assert _live_result_is_success({"result": "success"}) is True
    assert _live_result_is_success({"result": 1822}) is True
    assert _live_result_is_success({"result": {"id": 1822}}) is True
    assert _live_result_is_success({"result": {"success": True}}) is True


def test_result_only_failure_shapes_are_rejected():
    assert _live_result_is_success(None) is False
    assert _live_result_is_success({"result": False}) is False
    assert _live_result_is_success({"result": 0}) is False
    assert _live_result_is_success({"result": "validation_failed"}) is False
    assert _live_result_is_success({"result": True, "error": "bad"}) is False
    assert _live_result_is_success({"error": "bad"}) is False


def test_result_only_compatibility_is_limited_to_observed_sources():
    assert _source_accepts_result_only(mdt_shared.MDTSettings(name_prefix="Website")) is True
    assert _source_accepts_result_only(mdt_shared.MDTSettings(name_prefix="Telegram")) is True
    assert _source_accepts_result_only(mdt_shared.MDTSettings(name_prefix=" telegram ")) is True
    assert _source_accepts_result_only(mdt_shared.MDTSettings(name_prefix="VK")) is False
    assert _source_accepts_result_only(mdt_shared.MDTSettings(name_prefix="Client")) is False
