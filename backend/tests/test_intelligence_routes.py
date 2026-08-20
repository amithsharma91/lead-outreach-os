"""Route regression test for Phase 1 intelligence endpoints.

Verifies that all intelligence routes are registered in the FastAPI application.
"""

import pytest


def test_intelligence_routes_exist():
    """Verify all Phase 1 intelligence routes are present in the OpenAPI spec.

    This is the primary route regression test - it must always pass as long as
    the intelligence router is included in the FastAPI application.
    """
    from app.main import app

    openapi = app.openapi()
    paths = openapi.get("paths", {})

    expected_paths = [
        "/api/intelligence/analyze/{lead_id}",
        "/api/intelligence/{lead_id}",
        "/api/intelligence/priority",
        "/api/intelligence/analyze-batch",
        "/api/intelligence/recalculate",
    ]

    for path in expected_paths:
        assert path in paths, f"Missing intelligence path: {path}"

    # Verify HTTP methods for each path (OpenAPI uses lowercase)
    method_checks = {
        "/api/intelligence/analyze/{lead_id}": {"post"},
        "/api/intelligence/{lead_id}": {"get", "patch"},
        "/api/intelligence/priority": {"get"},
        "/api/intelligence/analyze-batch": {"post"},
        "/api/intelligence/recalculate": {"post"},
    }

    for path, expected_methods in method_checks.items():
        actual_methods = set(paths[path].keys())
        assert actual_methods == expected_methods, (
            f"Methods mismatch for {path}: "
            f"expected {expected_methods}, got {actual_methods}"
        )