from epms import ocr_benchmark


def test_scoring_counts_missing_wrong_and_absent_fields_separately():
    expected = {1: [{"number": "12345-26-012345X", "line": "001", "usd": "5.00", "kg": "8", "serial": None}]}
    actual = {1: [{"number": "1234526012345x", "line": "001", "usd": "5", "kg": "18", "serial": "02"}]}
    counts, fields = ocr_benchmark.score(expected, actual)
    assert counts["correct_item_identity"] == 1 and counts["exact_documents"] == 0
    assert fields["usd"]["exact"] == 1 and fields["kg"]["wrong"] == 1
    assert fields["serial"]["false_extra"] == 1
    _, fields = ocr_benchmark.score(expected, {1: []})
    assert fields["kg"]["missing"] == 1


def test_duplicate_rows_cannot_increase_item_accuracy():
    first = {"number": "12345-26-012345X", "line": "001", "usd": "5"}
    second = dict(first, line="002", usd="7")
    counts, fields = ocr_benchmark.score({1: [first, second]}, {1: [first, first, second]})
    assert counts["correct_item_identity"] == 1
    assert counts["missing_items"] == 1 and counts["unexpected_items"] == 2
    assert fields["usd"]["exact"] == 1 and fields["usd"]["false_extra"] == 2
