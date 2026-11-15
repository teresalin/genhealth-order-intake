from datetime import date

from app.validation import dob_error, is_valid_npi, name_error


def test_npi_check_digit():
    assert is_valid_npi("1234567893")
    assert is_valid_npi("1245319599")
    assert not is_valid_npi("1234567890")
    assert not is_valid_npi("123456789")
    assert not is_valid_npi("１２３４５６７８９３")  # full-width digits


def test_dob_bounds():
    today = date(2026, 1, 1)
    assert dob_error(date(1980, 5, 5), today) is None
    assert dob_error(date(2026, 1, 2), today)
    assert dob_error(date(1899, 12, 31), today)


def test_name_rules():
    assert name_error("Mary-Jane O'Neil") is None
    assert name_error("José Núñez") is None
    assert name_error("Dr. A. Smith, MD") is None
    assert name_error("")
    assert name_error("12345")
    assert name_error("Robert'); DROP TABLE")
