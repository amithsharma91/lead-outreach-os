import sys
sys.path.insert(0, r'.')

from app import models
from app.db.session import init_db, engine, SessionLocal
from sqlalchemy import inspect, select, delete


def test_database_tables_exist():
    """All six required tables should exist after init_db()."""
    init_db()
    insp = inspect(engine)
    tables = insp.get_table_names()
    expected = {'activity_logs', 'campaigns', 'leads', 'outreach_messages', 'qualified_leads', 'replies'}
    assert set(tables) == expected, f"Expected {expected}, got {set(tables)}"


def test_six_tables_have_data_after_init():
    """After init_db, all six tables should exist (may be empty)."""
    init_db()
    insp = inspect(engine)
    tables = insp.get_table_names()
    assert len(tables) == 6


def test_lead_model_columns():
    """Lead model should have expected columns."""
    init_db()
    cols = [c.name for c in models.Lead.__table__.columns]
    expected = {'id', 'lead_id', 'business_name', 'niche', 'phone', 'city', 'state', 'country',
                'source', 'qualification_score', 'qualification_status', 'outreach_status',
                'do_not_contact', 'updated_at', 'created_at'}
    assert expected.issubset(set(cols)), f"Missing columns: {expected - set(cols)}"


def test_qualified_lead_model():
    """QualifiedLead model should have expected columns."""
    init_db()
    cols = [c.name for c in models.QualifiedLead.__table__.columns]
    expected = {'id', 'lead_id', 'business_name', 'accepted_at', 'niche', 'phone',
                'reply_text', 'qualification_reason', 'notification_status', 'created_at'}
    assert expected.issubset(set(cols)), f"Missing columns: {expected - set(cols)}"


def test_activity_log_model():
    """ActivityLog model should have expected columns."""
    init_db()
    cols = [c.name for c in models.ActivityLog.__table__.columns]
    expected = {'id', 'lead_id', 'event_type', 'event_data', 'timestamp'}
    assert expected.issubset(set(cols)), f"Missing columns: {expected - set(cols)}"


def test_reply_model():
    """Reply model should have expected columns."""
    init_db()
    cols = [c.name for c in models.Reply.__table__.columns]
    expected = {'id', 'lead_id', 'message_id', 'channel', 'reply_text', 'classification',
                'confidence', 'received_at'}
    assert expected.issubset(set(cols)), f"Missing columns: {expected - set(cols)}"


def test_outreach_message_model():
    """OutreachMessage model should have expected columns."""
    init_db()
    cols = [c.name for c in models.OutreachMessage.__table__.columns]
    expected = {'id', 'lead_id', 'campaign_id', 'channel', 'template_type', 'status',
                'scheduled_at', 'sent_at', 'created_at'}
    assert expected.issubset(set(cols)), f"Missing columns: {expected - set(cols)}"


def test_campaign_model():
    """Campaign model should have expected columns."""
    init_db()
    cols = [c.name for c in models.Campaign.__table__.columns]
    expected = {'id', 'name', 'description', 'template_type', 'active', 'start_time', 'end_time', 'created_at'}
    assert expected.issubset(set(cols)), f"Missing columns: {expected - set(cols)}"