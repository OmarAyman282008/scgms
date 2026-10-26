from django.shortcuts import redirect
from django.urls import reverse


class ForcePasswordChangeMiddleware:
    """Users created with an initial password must set their own before anything else."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        u = getattr(request, "user", None)
        if u is not None and u.is_authenticated and u.must_change_password:
            allowed = (reverse("accounts:password_change"), reverse("accounts:logout"))
            if request.path not in allowed and not request.path.startswith(("/static/", "/i18n/")):
                return redirect("accounts:password_change")
        return self.get_response(request)
