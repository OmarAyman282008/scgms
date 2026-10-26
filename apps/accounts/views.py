from django import forms
from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from apps.core import permissions as P
from apps.core.services import audit

from .models import User

INITIAL_PASSWORD = "ChangeMe@2026"


class LoginView(auth_views.LoginView):
    template_name = "accounts/login.html"
    redirect_authenticated_user = True

    def form_valid(self, form):
        resp = super().form_valid(form)
        audit(self.request.user, "login", f"Login: {self.request.user.username}",
              f"تسجيل دخول: {self.request.user.username}", request=self.request)
        return resp


class PasswordChangeView(auth_views.PasswordChangeView):
    template_name = "accounts/password_change.html"

    def form_valid(self, form):
        form.save()
        u = form.user
        u.must_change_password = False
        u.save(update_fields=["must_change_password"])
        update_session_auth_hash(self.request, u)
        messages.success(self.request, _("Password changed."))
        return redirect("core:home")


class ProfileForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ["name_ar", "name_en", "email", "phone", "email_notifications", "daily_digest_only"]


@login_required
def profile(request):
    form = ProfileForm(request.POST or None, instance=request.user)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, _("Saved."))
        return redirect("accounts:profile")
    roles = request.user.memberships.filter(end_date__isnull=True).select_related("committee")
    return render(request, "accounts/profile.html", {"form": form, "roles": roles})


class UserForm(forms.ModelForm):
    new_password = forms.CharField(label=gettext_lazy("Set password"), required=False,
                                   help_text=gettext_lazy("Leave empty to keep the current password. The user must change it at first login."))

    class Meta:
        model = User
        fields = ["username", "name_ar", "name_en", "email", "position", "global_role", "is_active"]


@login_required
def user_list(request):
    P.require(P.is_admin(request.user) or P.is_exec(request.user))
    q = request.GET.get("q", "")
    users = User.objects.all().order_by("global_role", "name_ar")
    if q:
        users = users.filter(name_ar__icontains=q) | users.filter(name_en__icontains=q) | users.filter(username__icontains=q)
    return render(request, "accounts/user_list.html", {"users": users.prefetch_related("memberships__committee"), "q": q})


@login_required
def user_edit(request, pk=None):
    P.require(P.is_admin(request.user))
    obj = get_object_or_404(User, pk=pk) if pk else None
    form = UserForm(request.POST or None, instance=obj)
    if request.method == "POST" and form.is_valid():
        u = form.save(commit=False)
        pw = form.cleaned_data.get("new_password")
        if not obj:
            u.set_password(pw or INITIAL_PASSWORD)
            u.must_change_password = True
        elif pw:
            u.set_password(pw)
            u.must_change_password = True
        u.save()
        audit(request.user, "user.save", f"Saved user {u.username} ({u.global_role})",
              f"حفظ المستخدم {u.username}", u, request=request)
        messages.success(request, _("Saved."))
        return redirect("accounts:users")
    return render(request, "accounts/user_form.html", {"form": form, "obj": obj, "initial_password": INITIAL_PASSWORD})
