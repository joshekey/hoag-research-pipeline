"""Synthetic study capability tests. Never uses hospital credentials."""
import unittest
from ohif_poc.study_capability import issue, verify

KEY = b"synthetic-test-key-not-for-production-012345"
UID = "1.2.826.0.1.3680043.10.543.100"

class StudyCapabilityTests(unittest.TestCase):
    def test_valid_study_bound_token(self):
        token = issue(KEY, UID, "synthetic-reviewer", now=1000, ttl=120)
        self.assertEqual(verify(KEY, token, UID, "synthetic-reviewer", now=1100), UID)

    def test_other_study_rejected(self):
        token = issue(KEY, UID, "synthetic-reviewer", now=1000)
        with self.assertRaises(PermissionError):
            verify(KEY, token, "1.2.999", "synthetic-reviewer", now=1010)

    def test_other_identity_rejected(self):
        token = issue(KEY, UID, "synthetic-reviewer", now=1000)
        with self.assertRaises(PermissionError):
            verify(KEY, token, UID, "another-reviewer", now=1010)

    def test_expired_and_future_rejected(self):
        token = issue(KEY, UID, "synthetic-reviewer", now=1000, ttl=40)
        for clock in (999, 1040, 1100):
            with self.assertRaises(PermissionError):
                verify(KEY, token, UID, "synthetic-reviewer", now=clock)

    def test_signature_tampering_rejected(self):
        token = issue(KEY, UID, "synthetic-reviewer", now=1000)
        body, sig = token.split(".")
        tampered = body[:-1] + ("A" if body[-1] != "A" else "B") + "." + sig
        with self.assertRaises(PermissionError):
            verify(KEY, tampered, UID, "synthetic-reviewer", now=1010)

    def test_invalid_ttl_and_uid_rejected(self):
        for ttl in (0, 301, True, -1):
            with self.assertRaises(ValueError):
                issue(KEY, UID, "synthetic-reviewer", now=1000, ttl=ttl)
        with self.assertRaises(ValueError):
            issue(KEY, "../data", "synthetic-reviewer", now=1000)

    def test_no_secret_in_payload(self):
        token = issue(KEY, UID, "synthetic-reviewer", now=1000)
        self.assertNotIn("synthetic-reviewer", token)
        self.assertNotIn(KEY.decode(), token)

if __name__ == "__main__":
    unittest.main()
