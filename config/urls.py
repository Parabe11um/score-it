from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

from .branding import APP_NAME
from .health import health


admin.site.site_header = APP_NAME
admin.site.site_title = APP_NAME

urlpatterns = [
    path("healthz/", health, name="health"),
    path("admin/", admin.site.urls),
    path(
        "login/",
        auth_views.LoginView.as_view(template_name="registration/login.html"),
        name="login",
    ),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("", include("poker.urls")),
]

