from tensorfork.fork import _dispatch_error, _is_signed, _jsonable, _outcomes, diff


def test_is_signed_reads_version_byte_after_compact_length():
    # compact len (1 byte, mode 0) then version: 0x84 = signed v4, 0x04 = unsigned/inherent v4
    assert _is_signed("0x" + "28" + "84" + "00" * 9)
    assert not _is_signed("0x" + "28" + "04" + "00" * 9)
    # 2-byte compact length (mode 1)
    assert _is_signed("0x" + "0901" + "84" + "00" * 60)
    assert not _is_signed("0x" + "0901" + "05" + "00" * 60)


def test_outcomes_maps_extrinsic_results():
    events = [
        {"extrinsic_idx": None, "event_id": "Issued"},
        {"extrinsic_idx": 0, "event_id": "ExtrinsicSuccess"},
        {"extrinsic_idx": 1, "event_id": "ExtrinsicFailed",
         "attributes": {"dispatch_error": {"Module": {"index": 7, "error": "0x1a000000"}}}},
    ]
    out = _outcomes(events)
    assert out[0] == "ok"
    assert out[1].startswith("failed: module 7")


def test_dispatch_error_prefers_names():
    assert _dispatch_error({"dispatch_error": {"Module": {"name": "SettingWeightsTooFast"}}}) == "SettingWeightsTooFast"


def test_jsonable_keeps_big_ints_exact():
    assert _jsonable({"flags": 2**127, "free": 5, "x": [2**60]}) == {"flags": str(2**127), "free": 5, "x": [str(2**60)]}


def test_diff_reports_changed_leaves_with_deltas():
    d = diff({"sn64": {"emission": 10, "n": 256}, "gone": 1}, {"sn64": {"emission": 12, "n": 256}, "new": True})
    assert d == {
        "gone": {"before": 1, "after": None},
        "new": {"before": None, "after": True},
        "sn64.emission": {"before": 10, "after": 12, "delta": 2},
    }
