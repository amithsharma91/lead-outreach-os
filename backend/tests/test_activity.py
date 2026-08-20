import sys
from datetime import datetime
sys.path.insert(0, r'.')

from app import models
from app.db.session import init_db, engine, SessionLocal
from sqlalchemy import select


def test_activity_log_creation():
    """Activity events can be created with required fields."""
    init_db()
    db = SessionLocal()
    try:
        lead = models.Lead(
            lead_id='GMAP-BLR-DENTAL-00005',
            business_name='Activity Test Lead',
            niche='DENTAL',
            phone='+919876543214',
        )
        db.add(lead)
        db.commit()

        # Create activity log entries directly
        activity_events = [
            ('IMPORT', {'source': 'test', 'count': 1}),
            ('REPLY_RECEIVED', {'reply_id': 1}),
            ('REPLY_CLASSIFIED', {'classification': 'positive'}),
            ('LEAD_QUALIFIED', {}),
            ('STOP_REQUEST', {}),
        ]

        for event_type, event_data in activity_events:
            db.add(models.ActivityLog(
                lead_id=lead.id,
                event_type=event_type,
                event_data=str(event_data),
                timestamp=datetime.fromisoformat('2026-08-18T18:37:00Z'.replace('Z', '+00:00')),
            ))
        db.commit()

        # Verify all 5 event types were created
        activity = db.execute(
            select(models.ActivityLog).where(models.ActivityLog.lead_id == lead.id)
        ).scalars().all()
        event_types = [e.event_type for e in activity]
        expected_types = ['IMPORT', 'REPLY_RECEIVED', 'REPLY_CLASSIFIED', 'LEAD_QUALIFIED', 'STOP_REQUEST']
        for et in expected_types:
            assert et in event_types, f"Expected event type {et} not found in {event_types}"

        # Check required fields
        for e in activity:
            assert e.lead_id is not None, "lead_id should not be None"
            assert e.event_type is not None, "event_type should not be None"
            assert e.event_data is not None, "event_data should not be None"
            assert e.timestamp is not None, "timestamp should not be None"
    finally:
        db.close()


def test_activity_logging_via_qualification():
    """Activity events should be logged when qualification occurs."""
    init_db()
    db = SessionLocal()
    try:
        lead = models.Lead(
            lead_id='GMAP-BLR-DENTAL-00006',
            business_name='Qualification Test',
            niche='DENTAL',
            phone='+919876543215',
        )
        db.add(lead)
        db.commit()

        from app.services.qualification import record_reply
        from app.core.constants import ReplyClassification

        # Record a positive reply which should log events
        reply = record_reply(
            db,
            lead_id='GMAP-BLR-DENTAL-00006',
            reply_text='Yes',
            channel='whatsapp',
            classification=ReplyClassification.POSITIVE.value,
            confidence=0.95,
        )
        db.commit()

        # Check activity logs exist
        activity = db.execute(
            select(models.ActivityLog).where(models.ActivityLog.lead_id == lead.id)
        ).scalars().all()
        assert len(activity) > 0, "Expected activity logs to be created"

        # Check event types include reply-related events
        event_types = [e.event_type for e in activity]
        assert 'REPLY_RECEIVED' in event_types
        assert 'REPLY_CLASSIFIED' in event_types
    finally:
        db.close()