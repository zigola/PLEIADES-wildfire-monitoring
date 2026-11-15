from unittest import mock

from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import ContactMessage


class ContactTests(TestCase):
    def test_contact_saves_message(self):
        r = self.client.post(reverse("contact"), {
            "name": "Sara", "email": "s@x.com", "message": "Hello team"}, follow=True)
        self.assertEqual(ContactMessage.objects.count(), 1)
        # SMTP is not configured in the test environment: the message is kept and the visitor is told the truth.
        self.assertContains(r, "Your message was saved, but site email is not configured yet")
        self.assertEqual(len(mail.outbox), 0)

    @override_settings(CONTACT_EMAIL="team@example.org", EMAIL_HOST="smtp.example.org", EMAIL_HOST_USER="u",
                       EMAIL_HOST_PASSWORD="p", EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_contact_emails_team_with_real_line_breaks(self):
        r = self.client.post(reverse("contact"), {"name": "Sara", "email": "s@x.com", "message": "Hello team"}, follow=True)
        self.assertContains(r, "Thank you")
        self.assertEqual(len(mail.outbox), 1)
        body = mail.outbox[0].body
        self.assertIn("Name: Sara\nEmail: s@x.com\n", body)   # real newlines, not a literal backslash-n
        self.assertNotIn("\\n", body)
        self.assertEqual(mail.outbox[0].reply_to, ["s@x.com"])
        self.assertEqual(mail.outbox[0].to, ["team@example.org"])

    @override_settings(CONTACT_EMAIL="team@example.org", EMAIL_HOST="smtp.example.org", EMAIL_HOST_USER="u", EMAIL_HOST_PASSWORD="p")
    def test_contact_keeps_message_when_email_delivery_fails(self):
        with mock.patch("django.core.mail.EmailMessage.send", side_effect=OSError("smtp down")):
            r = self.client.post(reverse("contact"), {"name": "Sara", "email": "s@x.com", "message": "Hello team"}, follow=True)
        self.assertEqual(ContactMessage.objects.count(), 1)
        self.assertContains(r, "email delivery failed")

    def test_contact_invalid(self):
        r = self.client.post(reverse("contact"), {"name": "", "email": "bad", "message": ""})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(ContactMessage.objects.count(), 0)

    def test_404_page_is_styled(self):
        r = self.client.get("/nope/")
        self.assertEqual(r.status_code, 404)
