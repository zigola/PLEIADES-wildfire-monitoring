from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .roles import MEMBER, OPERATOR, VISITOR, user_role

User = get_user_model()

PUBLIC_PAGES = ["home", "about", "methodology", "public_dashboard", "contact",
                "accounts:login", "accounts:register"]


class PublicPagesTests(TestCase):
    def test_public_pages_load_for_visitors(self):
        for name in PUBLIC_PAGES:
            with self.subTest(page=name):
                self.assertEqual(self.client.get(reverse(name)).status_code, 200)

    def test_navbar_links_resolve(self):
        html = self.client.get(reverse("about")).content.decode()
        for name in ["home", "about", "methodology", "public_dashboard", "contact"]:
            self.assertIn(reverse(name), html)
        self.assertNotIn(reverse("assistant"), html.split("nav-links")[1].split("</nav>")[0])
        self.assertNotIn(reverse("console"), html.split("nav-links")[1].split("</nav>")[0])


class RoleTests(TestCase):
    def setUp(self):
        self.member = User.objects.create_user("mona", "m@x.com", "pass-12345-xyz")
        self.operator = User.objects.create_user("omar", "o@x.com", "pass-12345-xyz")
        self.operator.profile.role = "operator"
        self.operator.profile.save()
        self.admin = User.objects.create_superuser("root", "r@x.com", "pass-12345-xyz")

    def test_role_resolution(self):
        self.assertEqual(user_role(self.member), MEMBER)
        self.assertEqual(user_role(self.operator), OPERATOR)
        self.assertEqual(user_role(self.admin), OPERATOR)
        from django.contrib.auth.models import AnonymousUser
        self.assertEqual(user_role(AnonymousUser()), VISITOR)

    def test_visitor_redirected_to_login(self):
        for name in ["assistant", "console"]:
            r = self.client.get(reverse(name))
            self.assertEqual(r.status_code, 302)
            self.assertIn(reverse("accounts:login"), r["Location"])
            self.assertIn("next=", r["Location"])

    def test_member_access(self):
        self.client.force_login(self.member)
        self.assertEqual(self.client.get(reverse("assistant")).status_code, 200)
        self.assertEqual(self.client.get(reverse("console")).status_code, 403)

    def test_operator_access(self):
        for u in (self.operator, self.admin):
            self.client.force_login(u)
            self.assertEqual(self.client.get(reverse("assistant")).status_code, 200)
            self.assertEqual(self.client.get(reverse("console")).status_code, 200)

    def test_nav_shows_links_by_role(self):
        self.client.force_login(self.member)
        nav = self.client.get(reverse("home")).content.decode().split("nav-links")[1].split("</nav>")[0]
        self.assertIn(reverse("assistant"), nav)
        self.assertNotIn(reverse("console"), nav)
        self.client.force_login(self.operator)
        nav = self.client.get(reverse("home")).content.decode().split("nav-links")[1].split("</nav>")[0]
        self.assertIn(reverse("console"), nav)


class AuthFlowTests(TestCase):
    def test_register_creates_member_and_logs_in(self):
        r = self.client.post(reverse("accounts:register"), {
            "username": "newuser", "email": "new@x.com", "user_type": "other",
            "password1": "Sup3r-secret-pw", "password2": "Sup3r-secret-pw"})
        self.assertRedirects(r, reverse("home"))
        u = User.objects.get(username="newuser")
        self.assertEqual(u.profile.role, "member")
        self.assertEqual(user_role(u), MEMBER)
        self.assertEqual(int(self.client.session["_auth_user_id"]), u.pk)

    def test_register_cannot_self_assign_operator(self):
        self.client.post(reverse("accounts:register"), {
            "username": "sneaky", "email": "s@x.com", "role": "operator", "user_type": "other",
            "password1": "Sup3r-secret-pw", "password2": "Sup3r-secret-pw"})
        self.assertEqual(User.objects.get(username="sneaky").profile.role, "member")

    def test_register_validation(self):
        User.objects.create_user("taken", "dup@x.com", "pw-12345-abc")
        r = self.client.post(reverse("accounts:register"), {
            "username": "other", "email": "DUP@x.com",
            "password1": "Sup3r-secret-pw", "password2": "Sup3r-secret-pw"})
        self.assertEqual(r.status_code, 200)
        self.assertFalse(User.objects.filter(username="other").exists())
        r = self.client.post(reverse("accounts:register"), {
            "username": "weak", "email": "w@x.com", "password1": "123", "password2": "123"})
        self.assertFalse(User.objects.filter(username="weak").exists())

    def test_login_logout_and_next(self):
        User.objects.create_user("lina", "l@x.com", "pass-12345-xyz")
        r = self.client.post(reverse("accounts:login") + "?next=" + reverse("assistant"),
                             {"username": "lina", "password": "pass-12345-xyz",
                              "next": reverse("assistant")})
        self.assertRedirects(r, reverse("assistant"))
        r = self.client.post(reverse("accounts:logout"))
        self.assertRedirects(r, reverse("home"))
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_bad_login_shows_error(self):
        r = self.client.post(reverse("accounts:login"), {"username": "no", "password": "no"})
        self.assertContains(r, "incorrect")

    def test_profile_requires_login(self):
        r = self.client.get(reverse("accounts:profile"))
        self.assertEqual(r.status_code, 302)
        User.objects.create_user("p", "p@x.com", "pass-12345-xyz")
        self.client.login(username="p", password="pass-12345-xyz")
        self.assertContains(self.client.get(reverse("accounts:profile")), "member")


class GuestModeTests(TestCase):
    def test_login_and_register_offer_guest_button(self):
        for name in ("accounts:login", "accounts:register"):
            with self.subTest(page=name):
                self.assertContains(self.client.get(reverse(name)), reverse("accounts:guest"))

    def test_guest_post_sets_session_and_opens_calendar(self):
        r = self.client.post(reverse("accounts:guest"))
        self.assertRedirects(r, reverse("public_dashboard"))
        self.assertTrue(self.client.session.get("guest"))
        page = self.client.get(reverse("public_dashboard"))
        self.assertContains(page, "guest-chip")
        self.assertContains(page, reverse("accounts:register"))  # CTA to create an account

    def test_guest_requires_post(self):
        self.assertEqual(self.client.get(reverse("accounts:guest")).status_code, 405)

    def test_guest_still_blocked_from_members_pages(self):
        self.client.post(reverse("accounts:guest"))
        self.assertEqual(self.client.get(reverse("assistant")).status_code, 302)
        self.assertEqual(self.client.get(reverse("accounts:profile")).status_code, 302)


class ProfilePagesTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("sara", "sara@x.com", "pass-12345-xyz")
        self.client.force_login(self.user)

    def test_all_profile_pages_load(self):
        for name in ("accounts:profile", "accounts:profile_edit", "accounts:profile_areas", "accounts:security"):
            with self.subTest(page=name):
                self.assertEqual(self.client.get(reverse(name)).status_code, 200)

    def test_profile_pages_require_login(self):
        self.client.logout()
        for name in ("accounts:profile", "accounts:profile_edit", "accounts:profile_areas", "accounts:security"):
            with self.subTest(page=name):
                self.assertEqual(self.client.get(reverse(name)).status_code, 302)

    def test_register_stores_user_type(self):
        self.client.logout()
        self.client.post(reverse("accounts:register"), {
            "username": "ranger", "email": "r@x.com", "user_type": "responder",
            "password1": "Sup3r-secret-pw", "password2": "Sup3r-secret-pw"})
        self.assertEqual(User.objects.get(username="ranger").profile.user_type, "responder")

    def test_edit_profile_saves_details(self):
        r = self.client.post(reverse("accounts:profile_edit"), {
            "first_name": "Sara", "last_name": "B", "email": "sara2@x.com",
            "user_type": "scientist", "organization": "INRA", "country": "Morocco", "bio": "Remote sensing."})
        self.assertRedirects(r, reverse("accounts:profile_edit"))
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "sara2@x.com")
        self.assertEqual(self.user.profile.organization, "INRA")
        self.assertEqual(self.user.profile.user_type, "scientist")

    def test_edit_profile_cannot_change_role(self):
        self.client.post(reverse("accounts:profile_edit"), {
            "first_name": "", "last_name": "", "email": "sara@x.com",
            "user_type": "other", "organization": "", "country": "Morocco", "bio": "", "role": "operator"})
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.role, "member")

    def test_edit_profile_rejects_email_used_by_someone_else(self):
        User.objects.create_user("other", "taken@x.com", "pass-12345-xyz")
        r = self.client.post(reverse("accounts:profile_edit"), {
            "first_name": "", "last_name": "", "email": "TAKEN@x.com",
            "user_type": "other", "organization": "", "country": "", "bio": ""})
        self.assertEqual(r.status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "sara@x.com")

    def test_password_change(self):
        r = self.client.post(reverse("accounts:security"), {
            "old_password": "pass-12345-xyz", "new_password1": "N3w-strong-pass-77", "new_password2": "N3w-strong-pass-77"})
        self.assertRedirects(r, reverse("accounts:security"))
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("N3w-strong-pass-77"))


class SavedAreaTests(TestCase):
    AREA = {"name": "Tadla", "south": "32.0", "west": "-7.2", "north": "33.0", "east": "-5.6"}

    def setUp(self):
        self.user = User.objects.create_user("sara", "sara@x.com", "pass-12345-xyz")
        self.client.force_login(self.user)

    def test_save_area(self):
        r = self.client.post(reverse("accounts:area_save"), {**self.AREA, "next": reverse("public_dashboard")})
        self.assertRedirects(r, reverse("public_dashboard"))
        area = self.user.saved_areas.get()
        self.assertEqual((area.name, area.south, area.east), ("Tadla", 32.0, -5.6))
        self.assertContains(self.client.get(reverse("accounts:profile_areas")), "Tadla")

    def test_save_area_rejects_inverted_box(self):
        self.client.post(reverse("accounts:area_save"), {**self.AREA, "south": "40", "north": "30"})
        self.assertEqual(self.user.saved_areas.count(), 0)

    def test_save_area_ignores_external_next(self):
        r = self.client.post(reverse("accounts:area_save"), {**self.AREA, "next": "https://evil.example/"})
        self.assertRedirects(r, reverse("accounts:profile_areas"))

    def test_guest_cannot_save_area(self):
        self.client.logout()
        r = self.client.post(reverse("accounts:area_save"), self.AREA)
        self.assertEqual(r.status_code, 302)
        self.assertIn(reverse("accounts:login"), r["Location"])

    def test_area_limit(self):
        from .models import SavedArea
        from .views import MAX_SAVED_AREAS
        for i in range(MAX_SAVED_AREAS):
            SavedArea.objects.create(user=self.user, name=f"a{i}", south=1, west=1, north=2, east=2)
        self.client.post(reverse("accounts:area_save"), self.AREA)
        self.assertEqual(self.user.saved_areas.count(), MAX_SAVED_AREAS)

    def test_delete_only_own_area(self):
        from .models import SavedArea
        other = User.objects.create_user("other", "o2@x.com", "pass-12345-xyz")
        theirs = SavedArea.objects.create(user=other, name="x", south=1, west=1, north=2, east=2)
        mine = SavedArea.objects.create(user=self.user, name="y", south=1, west=1, north=2, east=2)
        self.assertEqual(self.client.post(reverse("accounts:area_delete", args=[theirs.pk])).status_code, 404)
        self.client.post(reverse("accounts:area_delete", args=[mine.pk]))
        self.assertFalse(SavedArea.objects.filter(pk=mine.pk).exists())
        self.assertTrue(SavedArea.objects.filter(pk=theirs.pk).exists())
