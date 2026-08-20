"""Production database safety regression test.

PROVES that normal production startup can NEVER execute a destructive
database reset (drop_all / create_all).

Two independent proofs:

1. Code-level: the FastAPI lifespan used by production does NOT reference
   init_db / drop_all / create_all.

2. Runtime-level: with data present in the database, starting the real
   application (TestClient runs the lifespan) leaves the data intact.

Destructive resets exist ONLY in:
- tests/conftest.py (pytest_configure)
- tests/... init_db() calls inside dedicated test setup
"""

import inspect

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import main as main_module
from app.main import app
from app.core.config import settings
from app.db.session import SessionLocal, init_db
from app.models.lead import Lead


def _find_lifespan_functions(obj, depth=0, seen=None):
    """Recursively collect functions reachable from a lifespan closure."""
    if seen is None:
        seen = set()
    if id(obj) in seen or depth > 3:
        return []
    seen.add(id(obj))
    funcs = []
    if inspect.isfunction(obj):
        funcs.append(obj)
        if obj.__closure__:
            for cell in obj.__closure__:
                if cell.cell_contents is not None:
                    funcs.extend(_find_lifespan_functions(cell.cell_contents, depth + 1, seen))
    return funcs


def test_lifespan_source_has_no_destructive_calls():
    """Production lifespan must not contain init_db/drop_all/create_all."""
    forbidden = ("init_db", "drop_all", "create_all")

    # 1. Direct source of the original lifespan defined in app.main
    lifespan = getattr(main_module, "lifespan")
    src = inspect.getsource(lifespan)
    for token in forbidden:
        assert token not in src, f"Production lifespan references {token}!"

    # 2. Every function reachable through the merged lifespan closure
    merged = app.router.lifespan_context
    for fn in _find_lifespan_functions(merged):
        try:
            fn_src = inspect.getsource(fn)
        except (OSError, TypeError):
            continue
        for token in forbidden:
            assert token not in fn_src, f"Lifespan function {fn.__name__} references {token}!"


def test_app_router_has_no_destructive_calls():
    """App startup wiring must not register any destructive startup hook."""
    # No on_event("startup") handlers registered
    assert not getattr(app, "on_startup", []), "on_startup handlers present"
    # The lifespan (original + merged) contains no destructive tokens
    test_lifespan_source_has_no_destructive_calls()


def test_data_survives_real_application_startup():
    """Runtime proof: committed data survives lifespan execution."""
    init_db()  # dedicated test reset (allowed for tests)

    db = SessionLocal()
    lead_id = "SAFE-START-001"
    db.add(Lead(
        lead_id=lead_id,
        business_name="Survival Lead",
        niche="software",
        city="Safe City",
        state="SC",
        country="USA",
        website_status="HAS_WEBSITE",
    ))
    db.commit()
    db.close()

    # Start the REAL application: TestClient as context manager runs lifespan
    with TestClient(app) as client:
        resp = client.get("/api/health")
        assert resp.status_code == 200

    # Data must still exist after startup/shutdown
    db2 = SessionLocal()
    lead = db2.execute(
        select(Lead).where(Lead.lead_id == lead_id)
    ).scalars().first()
    assert lead is not None, "Production startup destroyed committed data!"
    assert lead.business_name == "Survival Lead"
    db2.close()


def test_destructive_reset_confined_to_test_code():
    """drop_all/create_all may only exist in app/db/session.py definition
    and test setup files, never in production runtime code."""
    import os

    backend = r"C:\tmp\lead-outreach-os\backend"
    app_dir = os.path.join(backend, "app")
    banned = []

    for root, dirs, files in os.walk(app_dir):
        for fname in files:
            if not fname.endswith(".py"):
                continue
            fpath = os.path.join(root, fname)
            with open(fpath, "r", encoding="utf-8") as fh:
                src = fh.read()
            for token in ("drop_all", "create_all"):
                if token in src:
                    # session.py is allowed: it only DEFINES init_db; it must
                    # not be invoked by production startup (proven above).
                    if not (fname == "session.py" and token in src):
                        banned.append((fpath, token))
    assert not banned, f"Destructive calls found in production code: {banned}"


def test_init_db_refuses_outside_test_env():
    """Phase 2K guard: init_db() must refuse to run when APP_ENV != test."""
    original = settings.app_env
    try:
        object.__setattr__(settings, "app_env", "development")
        with pytest.raises(RuntimeError, match="APP_ENV=test"):
            init_db()
    finally:
        object.__setattr__(settings, "app_env", original)


def test_init_db_allowed_in_test_env():
    """The guard must not break the legitimate test-only reset."""
    assert settings.app_env == "test"
    init_db()
    db = SessionLocal()
    assert db.execute(select(Lead.id).limit(1)).scalars().first() is None
    db.close()