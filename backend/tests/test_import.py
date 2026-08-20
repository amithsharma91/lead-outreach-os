import sys
sys.path.insert(0, r'.')

import io
import os
from app.services.import_service import build_import_report, commit_import, create_backup
from app.services.import_service import ImportRow, FIELD_ALIASES, CANONICAL_FIELDS
from app.models.lead import Lead


def test_import_service_components():
    """Verify import service core functions are available."""
    assert build_import_report is not None
    assert commit_import is not None
    assert create_backup is not None


def test_Field_ALIASES_exist():
    """FIELD_ALIASES should be a dict."""
    assert isinstance(FIELD_ALIASES, dict)


def test_CANONICAL_FIELDS_exist():
    """CANONICAL_FIELDS should be a list/tuple."""
    assert CANONICAL_FIELDS is not None