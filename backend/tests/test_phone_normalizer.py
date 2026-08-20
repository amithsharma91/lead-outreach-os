import sys
sys.path.insert(0, r'.')

from app.services.phone_normalizer import normalize_phone, NormalizedPhone


def test_normalize_indian_with_country_code():
    """+91XXXXXXXXXX should normalize with review_required=False."""
    result = normalize_phone('+919876543210')
    assert result.phone == '+919876543210'
    assert result.review_required is False


def test_normalize_indian_without_country_code():
    """9876543210 (10 digits, no leading 0) should not be flagged as Indian."""
    result = normalize_phone('9876543210')
    assert result.phone == '+919876543210'
    assert result.review_required is False


def test_normalize_with_leading_zero():
    """09876543210 (11 digits with leading 0) should not be flagged as Indian."""
    result = normalize_phone('09876543210')
    assert result.phone == '+919876543210'
    assert result.review_required is False


def test_normalize_with_spaces():
    """+91 98765 43210 should normalize correctly."""
    result = normalize_phone('+91 98765 43210')
    assert result.phone == '+919876543210'
    assert result.review_required is False


def test_normalize_with_hyphens():
    """+91-98765-43210 should normalize correctly."""
    result = normalize_phone('+91-98765-43210')
    assert result.phone == '+919876543210'
    assert result.review_required is False


def test_normalize_empty():
    """Empty string should return normalized phone with review_required=True."""
    result = normalize_phone('')
    assert result.review_required is True
    # phone may be None for empty input


def test_normalize_none():
    """None should return normalized phone with review_required=True."""
    result = normalize_phone(None)
    assert result.review_required is True
    # phone may be None for None input


def test_normalize_generic_number():
    """1234567890 should normalize to +911234567890."""
    result = normalize_phone('1234567890')
    assert result.phone == '+911234567890'
    assert result.review_required is False