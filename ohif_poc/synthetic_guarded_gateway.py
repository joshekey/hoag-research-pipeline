"""Synthetic-only authorization test harness for OHIF DICOMweb.

NEVER deploy this module for hospital DICOM. An injected trusted test principal
stands in for future hospital authentication. The token issuer is deliberately
offline; there is no token issuance endpoint or clinical source access.
"""
from flask import abort, request

from ohif_poc.study_capability import verify
from ohif_poc.synthetic_dicomweb import ROOT, STUDY, create_test_app


def create_guarded_synthetic_app(key, trusted_principal):
    """Guard fixture QIDO/WADO requests with a verified single-study capability.

    trusted_principal must be a server-side callable; never implement it by
    trusting a header, query parameter or body. Test use only.
    """
    if not callable(trusted_principal):
        raise TypeError("Server-side principal callback required")
    app = create_test_app()

    @app.before_request
    def authorize():
        if not request.path.startswith(ROOT + "/"):
            abort(404)
        # Restrict to the fixture's one explicitly known synthetic study.
        # Never derive the authorized study from a caller-controlled URL.
        principal = trusted_principal()
        if not principal:
            abort(401)
        token = request.headers.get("X-HOAG-OHIF-Capability", "")
        try:
            verify(key, token, STUDY, principal)
        except (ValueError, PermissionError):
            abort(403)

    @app.after_request
    def headers(response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers.pop("Access-Control-Allow-Origin", None)
        return response

    return app
