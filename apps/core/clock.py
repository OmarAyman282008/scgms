"""Indirection over django.utils.timezone so model defaults follow a patched clock
(used by the demo-data loader to create a realistic history)."""
from django.utils import timezone


def now():
    return timezone.now()


def today():
    return timezone.localdate()
