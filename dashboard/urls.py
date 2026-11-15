from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("about/", views.about, name="about"),
    path("methodology/", views.methodology, name="methodology"),
    path("public-dashboard/", views.public_dashboard, name="public_dashboard"),
    path("contact/", views.contact, name="contact"),
    path("assistant/", views.assistant, name="assistant"),
    path("assistant/chat/", views.assistant_chat, name="assistant_chat"),
    path("console/", views.console, name="console"),
]
