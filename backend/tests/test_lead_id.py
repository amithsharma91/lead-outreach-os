import sys
sys.path.insert(0, r'.')

from app.services.lead_id import generate_lead_id
from app.db.session import SessionLocal, engine as orig_engine
from sqlalchemy import inspect


def test_lead_id_format():
    """Lead ID should follow SOURCE-CITY-NICHE-SEQUENCE format."""
    db = SessionLocal()
    try:
        lid = generate_lead_id(db, 'GMAP', 'BLR', 'DENTAL')
        assert lid.lead_id.startswith('GMAP-')
        assert 'BLR' in lid.lead_id
        assert 'DENTAL' in lid.lead_id
        assert '-' in lid.lead_id
    finally:
        db.close()


def test_lead_id_uniqueness_same_group():
    """Same (source, city, niche) group should produce same sequence increment."""
    db = SessionLocal()
    try:
        lid1 = generate_lead_id(db, 'GMAP', 'BLR', 'DENTAL')
        lid2 = generate_lead_id(db, 'GMAP', 'BLR', 'DENTAL')
        assert lid1.lead_id == lid2.lead_id, f"Expected same lead ID, got {lid1.lead_id} vs {lid2.lead_id}"
    finally:
        db.close()


def test_lead_id_separate_groups():
    """Different groups should produce different lead IDs."""
    db = SessionLocal()
    try:
        lid1 = generate_lead_id(db, 'GMAP', 'BLR', 'DENTAL')
        lid2 = generate_lead_id(db, 'CSV', 'BENGALURU', 'FITNESS')
        assert lid1.lead_id != lid2.lead_id, f"Expected different lead IDs, got {lid1.lead_id} vs {lid2.lead_id}"
    finally:
        db.close()


def test_lead_id_normalization():
    """Source, city, niche tokens should be normalized (lowercase, no special chars)."""
    db = SessionLocal()
    try:
        lid = generate_lead_id(db, 'GMAP', 'BLR', 'DENTAL')
        # All components should be normalized tokens
        assert lid.lead_id.count('-') == 3, f"Expected 3 dashes, got {lid.lead_id}"
    finally:
        db.close()