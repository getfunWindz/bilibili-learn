import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import pytest
from pagespec import parse_pages

def test_single_page():
    assert parse_pages("3") == [3]

def test_range():
    assert parse_pages("1-5") == [1, 2, 3, 4, 5]

def test_mixed():
    assert parse_pages("1,3,5-7") == [1, 3, 5, 6, 7]

def test_dedup_keeps_order():
    assert parse_pages("1-3,2") == [1, 2, 3]

def test_whitespace_tolerated():
    assert parse_pages(" 2 , 4 ") == [2, 4]

def test_invalid_raises():
    for bad in ("abc", "0-3", "5-2", "-1", "", "1,,2"):
        with pytest.raises(ValueError):
            parse_pages(bad)
