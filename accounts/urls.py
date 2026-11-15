from django.contrib.auth import views as auth_views
from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("register/", views.register_view, name="register"),
    path(
        "login/",
        auth_views.LoginView.as_view(
            template_name="accounts/login.html",
            redirect_authenticated_user=True,
        ),
        name="login",
    ),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("guest/", views.guest_view, name="guest"),
    path("profile/", views.profile_view, name="profile"),
    path("profile/edit/", views.profile_edit, name="profile_edit"),
    path("profile/areas/", views.profile_areas, name="profile_areas"),
    path("profile/security/", views.SecurityView.as_view(), name="security"),
    path("areas/save/", views.area_save, name="area_save"),
    path("areas/<int:pk>/delete/", views.area_delete, name="area_delete"),
]
