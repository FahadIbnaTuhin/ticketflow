from django.contrib import admin
from django.urls import include, path

from events import views as event_views


urlpatterns = [
    path("", event_views.home, name="home"),
    path("admin/", admin.site.urls),
    path("accounts/", include("accounts.urls")),
    path("events/", include("events.urls")),
    path("health/", include("health.urls")),
]
