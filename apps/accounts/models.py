from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils.translation import get_language
from django.utils.translation import gettext_lazy as _


class User(AbstractUser):
    """A faculty or staff member. School-wide role lives here; committee roles
    (chair / secretary / member) live in committees.Membership, so one person
    can hold different roles in different committees."""

    class Role(models.TextChoices):
        DEAN = "dean", _("Dean")
        VICE_DEAN = "vice_dean", _("Vice-Dean")
        ADMIN = "admin", _("System Administrator")
        STAFF = "staff", _("Faculty / Staff")

    global_role = models.CharField(_("school role"), max_length=20, choices=Role.choices, default=Role.STAFF)
    name_ar = models.CharField(_("name (Arabic)"), max_length=150, blank=True)
    name_en = models.CharField(_("name (English)"), max_length=150, blank=True)
    position = models.CharField(_("position"), max_length=150, blank=True)
    phone = models.CharField(_("phone"), max_length=40, blank=True)
    must_change_password = models.BooleanField(default=True)
    email_notifications = models.BooleanField(_("receive e-mail notifications"), default=True)
    daily_digest_only = models.BooleanField(_("reminders as one daily digest"), default=False)

    class Meta:
        ordering = ["name_ar", "username"]
        verbose_name = _("user")
        verbose_name_plural = _("users")

    @property
    def display_name(self):
        if (get_language() or "ar").startswith("en"):
            return self.name_en or self.name_ar or self.get_full_name() or self.username
        return self.name_ar or self.name_en or self.get_full_name() or self.username

    @property
    def is_executive(self):
        return self.is_superuser or self.global_role in (self.Role.DEAN, self.Role.VICE_DEAN)

    @property
    def is_sysadmin(self):
        return self.is_superuser or self.global_role == self.Role.ADMIN

    def __str__(self):
        return self.display_name
