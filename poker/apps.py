from django.apps import AppConfig

from config.branding import APP_NAME


class PokerConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "poker"
    verbose_name = APP_NAME

    def ready(self):
        from django.contrib.auth.signals import user_logged_in, user_login_failed
        from config.security import record_login

        user_logged_in.connect(record_login, dispatch_uid="score_it.login_success")
        user_login_failed.connect(record_login, dispatch_uid="score_it.login_failure")

