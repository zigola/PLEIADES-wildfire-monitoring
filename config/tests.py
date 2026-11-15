"""Production-settings guard: DEBUG off must never run with the public development key or without hosts."""

import os
import subprocess
import sys
from pathlib import Path

from django.test import SimpleTestCase

BASE = Path(__file__).resolve().parent.parent


def run_check(**env):
    e = {k: v for k, v in os.environ.items() if not k.startswith("DJANGO_")}
    e.update(env)
    return subprocess.run([sys.executable, "manage.py", "check"], cwd=BASE, env=e, capture_output=True, text=True, timeout=120)


class ProductionSettingsGuardTests(SimpleTestCase):
    def test_debug_off_requires_private_secret_key(self):
        r = run_check(DJANGO_DEBUG="0", DJANGO_ALLOWED_HOSTS="example.org")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("DJANGO_SECRET_KEY", r.stderr)

    def test_debug_off_requires_allowed_hosts(self):
        r = run_check(DJANGO_DEBUG="0", DJANGO_SECRET_KEY="x" * 50)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("DJANGO_ALLOWED_HOSTS", r.stderr)

    def test_debug_off_with_key_and_hosts_starts(self):
        r = run_check(DJANGO_DEBUG="0", DJANGO_SECRET_KEY="x" * 50, DJANGO_ALLOWED_HOSTS="example.org")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_local_development_default_still_starts_without_any_variable(self):
        self.assertEqual(run_check().returncode, 0)
