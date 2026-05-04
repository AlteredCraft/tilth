"""trace_id and span_id must follow the OTel shape so events.jsonl is
trivially exportable to Phoenix / Langfuse / Braintrust later."""

from __future__ import annotations

import re

from tilth.loop import _span_id, _trace_id

_HEX32 = re.compile(r"^[0-9a-f]{32}$")
_HEX16 = re.compile(r"^[0-9a-f]{16}$")


def test_trace_id_is_32_lowercase_hex():
    assert _HEX32.match(_trace_id())


def test_span_id_is_16_lowercase_hex():
    assert _HEX16.match(_span_id())


def test_ids_are_unique_per_call():
    assert _trace_id() != _trace_id()
    assert _span_id() != _span_id()
