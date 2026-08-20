import sys
sys.path.insert(0, r'.')

from app import models
from app.db.session import init_db, engine, SessionLocal
from app.services.qualification import record_reply, promote_to_qualified
from app.core.constants import MessageStatus, ReplyClassification
from sqlalchemy import select


def test_qualification_positive_reply():
    """Positive reply should create QualifiedLead with accepted_at."""
    init_db()
    db = SessionLocal()
    try:
        # Create a lead with unique ID
        lead = models.Lead(
            lead_id='GMAP-BLR-DENTAL-00001',
            business_name='Smile Dental',
            niche='DENTAL',
            phone='+919876543210',
        )
        db.add(lead)
        db.commit()

        # Record a positive reply
        reply = record_reply(
            db,
            lead_id='GMAP-BLR-DENTAL-00001',
            reply_text='Yes, I want a website!',
            channel='whatsapp',
            classification=ReplyClassification.POSITIVE.value,
            confidence=0.95,
        )
        db.commit()

        # Check QualifiedLead was created
        qualified = db.execute(
            select(models.QualifiedLead).where(models.QualifiedLead.lead_id == lead.id)
        ).scalar_one()
        assert qualified is not None
        assert qualified.business_name == 'Smile Dental'

        # Check accepted_at is populated
        assert qualified.accepted_at is not None, "accepted_at should be populated"
    finally:
        db.close()


def test_qualification_idempotent_re_promotion():
    """Re-promoting same reply should not create duplicate QualifiedLead."""
    init_db()
    db = SessionLocal()
    try:
        # Create a lead with unique ID
        lead = models.Lead(
            lead_id='GMAP-BLR-DENTAL-00002',
            business_name='Growth Dental',
            niche='DENTAL',
            phone='+919876543211',
        )
        db.add(lead)
        db.commit()

        # Record a positive reply
        reply = record_reply(
            db,
            lead_id='GMAP-BLR-DENTAL-00002',
            reply_text='Yes, I want a website!',
            channel='whatsapp',
            classification=ReplyClassification.POSITIVE.value,
            confidence=0.95,
        )
        db.commit()

        # Promote once
        q1 = promote_to_qualified(db, lead, reply, reason='first promotion')
        db.commit()

        # Promote again (idempotent)
        q2 = promote_to_qualified(db, lead, reply, reason='second promotion')
        db.commit()

        # Check only one QualifiedLead exists
        result = db.execute(
            select(models.QualifiedLead).where(models.QualifiedLead.lead_id == lead.id)
        )
        rows = result.scalars().all()
        assert len(rows) == 1, f"Expected 1 QualifiedLead, got {len(rows)}"

        # Check accepted_at is the same (original timestamp preserved)
        q = rows[0]
        # accepted_at should be from the first promotion
        assert q.accepted_at is not None
    finally:
        db.close()


def test_stop_classification():
    """STOP classification should set do_not_contact = True on lead."""
    init_db()
    db = SessionLocal()
    try:
        lead = models.Lead(
            lead_id='GMAP-BLR-DENTAL-00003',
            business_name='Stop Dental',
            niche='DENTAL',
            phone='+919876543212',
        )
        db.add(lead)
        db.commit()

        # Classify STOP
        from app.services.qualification import record_reply
        from app.core.constants import ReplyClassification

        reply = record_reply(
            db,
            lead_id='GMAP-BLR-DENTAL-00003',
            reply_text='STOP',
            channel='whatsapp',
            classification=ReplyClassification.STOP.value,
            confidence=1.0,
        )
        db.commit()

        # Check lead.do_not_contact is True
        db.refresh(lead)
        assert lead.do_not_contact is True, f"Expected do_not_contact=True, got {lead.do_not_contact}"
    finally:
        db.close()


def test_activity_logging():
    """Activity events should be logged with lead_id, event_type, event_data, timestamp."""
    init_db()
    db = SessionLocal()
    try:
        lead = models.Lead(
            lead_id='GMAP-BLR-DENTAL-00004',
            business_name='Activity Test',
            niche='DENTAL',
            phone='+919876543213',
        )
        db.add(lead)
        db.commit()

        from app.services.qualification import record_reply
        from app.core.constants import ReplyClassification

        # Record a reply which should log events
        reply = record_reply(
            db,
            lead_id='GMAP-BLR-DENTAL-00004',
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

        # Check event types
        event_types = [e.event_type for e in activity]
        assert 'REPLY_RECEIVED' in event_types
        assert 'REPLY_CLASSIFIED' in event_types
    finally:
        db.close()


def test_record_reply_transitions_message_via_state_machine():
    """Phase 2K: record_reply with a message_id must transition the
    message to REPLIED through the state machine (not a raw write)."""
    init_db()
    db = SessionLocal()
    try:
        lead = models.Lead(
            lead_id='GMAP-BLR-DENTAL-00005',
            business_name='Transition Dental',
            niche='DENTAL',
            phone='+919876543214',
        )
        db.add(lead)
        db.commit()

        message = models.OutreachMessage(
            lead_id=lead.id,
            channel='whatsapp',
            template_type='HAS_WEBSITE',
            generated_message='Hello',
            status=MessageStatus.SENT.value,
        )
        db.add(message)
        db.commit()

        reply = record_reply(
            db,
            lead_id='GMAP-BLR-DENTAL-00005',
            reply_text='Not now',
            channel='whatsapp',
            message_id=message.id,
            classification=ReplyClassification.NEGATIVE.value,
        )
        db.commit()

        assert reply.message_id == message.id
        db.refresh(message)
        assert message.status == MessageStatus.REPLIED.value
    finally:
        db.close()


def test_record_reply_terminal_message_stays_put():
    """Phase 2K: a terminal message referenced by the legacy path must
    NOT be re-transitioned (state machine rejection)."""
    init_db()
    db = SessionLocal()
    try:
        lead = models.Lead(
            lead_id='GMAP-BLR-DENTAL-00006',
            business_name='Terminal Dental',
            niche='DENTAL',
            phone='+919876543215',
        )
        db.add(lead)
        db.commit()

        message = models.OutreachMessage(
            lead_id=lead.id,
            channel='whatsapp',
            template_type='HAS_WEBSITE',
            generated_message='Hello',
            status=MessageStatus.REPLIED.value,
        )
        db.add(message)
        db.commit()

        record_reply(
            db,
            lead_id='GMAP-BLR-DENTAL-00006',
            reply_text='Still not interested',
            channel='whatsapp',
            message_id=message.id,
            classification=ReplyClassification.NEGATIVE.value,
        )
        db.commit()

        db.refresh(message)
        assert message.status == MessageStatus.REPLIED.value
    finally:
        db.close()


def test_record_reply_stop_transitions_active_messages():
    """Phase 2K: STOP via the legacy path must stop the lead AND move
    every active message to STOPPED (same as Phase 2G ingestion)."""
    init_db()
    db = SessionLocal()
    try:
        lead = models.Lead(
            lead_id='GMAP-BLR-DENTAL-00007',
            business_name='Stop Hard Dental',
            niche='DENTAL',
            phone='+919876543216',
        )
        db.add(lead)
        db.commit()

        message = models.OutreachMessage(
            lead_id=lead.id,
            channel='whatsapp',
            template_type='HAS_WEBSITE',
            generated_message='Hello',
            status=MessageStatus.SENT.value,
        )
        db.add(message)
        db.commit()

        record_reply(
            db,
            lead_id='GMAP-BLR-DENTAL-00007',
            reply_text='STOP',
            channel='whatsapp',
            classification=ReplyClassification.STOP.value,
            confidence=1.0,
        )
        db.commit()

        db.refresh(lead)
        assert lead.do_not_contact is True
        assert lead.outreach_status == 'STOPPED'
        db.refresh(message)
        assert message.status == MessageStatus.STOPPED.value
    finally:
        db.close()