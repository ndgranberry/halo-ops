"""Tests for the load-bearing QueryCategory thresholds.

These thresholds are what classify queries as "too narrow / specific /
moderate / general / too broad" — business-critical because downstream
logic either ships or rejects the query based on this label.
"""

import pytest

from models_roboscout import QueryCategory


@pytest.mark.parametrize(
    "count,expected",
    [
        (0, QueryCategory.TOO_NARROW),
        (19, QueryCategory.TOO_NARROW),
        (20, QueryCategory.SPECIFIC),   # boundary
        (499, QueryCategory.SPECIFIC),
        (500, QueryCategory.MODERATE),  # boundary
        (1000, QueryCategory.MODERATE),
        (1001, QueryCategory.GENERAL),  # boundary
        (3000, QueryCategory.GENERAL),
        (3001, QueryCategory.TOO_BROAD),
        (999_999, QueryCategory.TOO_BROAD),
    ],
)
def test_classify_by_count(count, expected):
    assert QueryCategory.from_count(count) is expected
