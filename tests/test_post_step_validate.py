"""Smoke tests for post_step_validate."""


def test_import_post_step_validate():
    from app.services import post_step_validate as psv

    assert hasattr(psv, "validate_after_music")
    assert hasattr(psv, "finalize_or_retry")
