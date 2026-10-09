"""Synthetic-only browser-session authorization harness for OHIF.

NO login/identity provider, server launcher, hospital paths, secrets or clinical
endpoints. Test fixtures inject a previously authenticated session. Production
must integrate with approved institutional authentication/session management.
"""
from flask import abort, request, session
from ohif_poc.study_capability import verify
from ohif_poc.synthetic_dicomweb import ROOT, STUDY, create_test_app


def create_session_test_app(signing_key, flask_session_key):
    """Require both a trusted session identity and short-lived study capability.

    In production, the session identity must come from a verified identity
    provider; the browser must never be allowed to set its own principal.
    """
    app = create_test_app()
    app.secret_key = flask_session_key
    app.config.update(SESSION_COOKIE_HTTPONLY=True,
                      SESSION_COOKIE_SAMESITE="Strict",
                      SESSION_COOKIE_SECURE=True)

    @app.before_request
    def authorize_dicomweb():
        if not request.path.startswith(ROOT + "/"):
            abort(404)
        identity = session.get("_synthetic_verified_identity")
        capability = session.get("_synthetic_study_capability")
        if not identity or not capability:
            abort(401)
        try:
            verify(signing_key, capability, STUDY, identity)
        except (ValueError, PermissionError):
            abort(403)

    @app.after_request
    def no_cache(response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers.pop("Access-Control-Allow-Origin", None)
        return response

    return app
