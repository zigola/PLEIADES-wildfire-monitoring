from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("accounts.urls")),
    path("api/fire/", include("satellite.urls")),
    path("iot/", include("iot.urls")),
    path("satellite/", include("satellite.page_urls")),
    path("", include("dashboard.urls")),
]
