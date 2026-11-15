from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import PasswordChangeView
from django.contrib.messages.views import SuccessMessageMixin
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils.decorators import method_decorator
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from .forms import AccountForm, ProfileForm, RegisterForm, SavedAreaForm
from .models import Profile, SavedArea

MAX_SAVED_AREAS = 20


def register_view(request):
    if request.user.is_authenticated:
        return redirect("home")

    if request.method == "POST":
        form = RegisterForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            messages.success(request, "Welcome to PLEIADES! Your account has been created.")
            return redirect("home")
    else:
        form = RegisterForm()

    return render(request, "accounts/register.html", {"form": form})


@require_POST
def guest_view(request):
    """Browse without an account. Guests get the public calendar, not saved areas."""
    if request.user.is_authenticated:
        return redirect("home")
    request.session["guest"] = True
    messages.success(request, "You are browsing as a guest. Create a free account any time to save areas.")
    return redirect("public_dashboard")


def _safe_next(request, fallback):
    target = request.POST.get("next") or request.GET.get("next") or ""
    if target and url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()},
                                                  require_https=request.is_secure()):
        return target
    return fallback


# ------------------------------------------------------------------ profile

def _profile_context(request, section):
    return {
        "section": section,
        "profile": request.user.profile,
        "area_count": request.user.saved_areas.count(),
    }


@login_required
def profile_view(request):
    ctx = _profile_context(request, "overview")
    ctx["recent_areas"] = request.user.saved_areas.all()[:3]
    return render(request, "accounts/profile.html", ctx)


@login_required
def profile_edit(request):
    profile, _ = Profile.objects.get_or_create(user=request.user)
    if request.method == "POST":
        account_form = AccountForm(request.POST, instance=request.user)
        profile_form = ProfileForm(request.POST, instance=profile)
        if account_form.is_valid() and profile_form.is_valid():
            account_form.save()
            profile_form.save()
            messages.success(request, "Your profile has been updated.")
            return redirect("accounts:profile_edit")
    else:
        account_form = AccountForm(instance=request.user)
        profile_form = ProfileForm(instance=profile)
    ctx = _profile_context(request, "edit")
    ctx["forms"] = [account_form, profile_form]
    return render(request, "accounts/profile_edit.html", ctx)


@login_required
def profile_areas(request):
    ctx = _profile_context(request, "areas")
    ctx["areas"] = request.user.saved_areas.all()
    ctx["max_areas"] = MAX_SAVED_AREAS
    return render(request, "accounts/profile_areas.html", ctx)


@login_required
@require_POST
def area_save(request):
    fallback = reverse("accounts:profile_areas")
    target = _safe_next(request, fallback)
    if request.user.saved_areas.count() >= MAX_SAVED_AREAS:
        messages.error(request, f"You can save up to {MAX_SAVED_AREAS} areas. Delete one first.")
        return redirect(target)
    form = SavedAreaForm(request.POST)
    if form.is_valid():
        area = form.save(commit=False)
        area.user = request.user
        area.save()
        messages.success(request, f"Area “{area.name}” saved to your profile.")
    else:
        messages.error(request, "That area could not be saved. Check the name and coordinates.")
    return redirect(target)


@login_required
@require_POST
def area_delete(request, pk):
    area = get_object_or_404(SavedArea, pk=pk, user=request.user)
    area.delete()
    messages.success(request, "Area removed.")
    return redirect("accounts:profile_areas")


@method_decorator(login_required, name="dispatch")
class SecurityView(SuccessMessageMixin, PasswordChangeView):
    template_name = "accounts/profile_security.html"
    success_url = reverse_lazy("accounts:security")
    success_message = "Your password has been changed."

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update(_profile_context(self.request, "security"))
        return ctx
