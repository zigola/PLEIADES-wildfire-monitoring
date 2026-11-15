import json

from django.conf import settings
from django.contrib import messages
from django.core.mail import EmailMessage
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from accounts.decorators import member_required, operator_required

from .forms import ContactForm


def home(request):
    return render(request, "dashboard/home.html")


def about(request):
    return render(request, "dashboard/about.html")


def methodology(request):
    return render(request, "dashboard/methodology.html")


def public_dashboard(request):
    return render(request, "dashboard/public_dashboard.html", {"sat_active": "calendar"})


def contact(request):
    if request.method == "POST":
        form = ContactForm(request.POST)
        if form.is_valid():
            contact_message = form.save()
            if settings.CONTACT_EMAIL and settings.EMAIL_HOST and settings.EMAIL_HOST_USER and settings.EMAIL_HOST_PASSWORD:
                try:
                    EmailMessage(
                        subject=f"[PLEIADES Contact] {contact_message.name}",
                        body=(
                            f"Name: {contact_message.name}\n"
                            f"Email: {contact_message.email}\n"
                            f"Received: {contact_message.created_at:%Y-%m-%d %H:%M}\n\n"
                            f"{contact_message.message}"
                        ),
                        from_email=settings.DEFAULT_FROM_EMAIL,
                        to=[settings.CONTACT_EMAIL],
                        reply_to=[contact_message.email],
                    ).send(fail_silently=False)
                    messages.success(request, "Thank you! Your message has been emailed to the PLEIADES team.")
                except Exception:
                    messages.warning(request, "Your message was saved, but email delivery failed. The team can read it in the admin panel.")
            else:
                messages.warning(request, "Your message was saved, but site email is not configured yet. The team can read it in the admin panel.")
            return redirect("contact")
    else:
        form = ContactForm()
    return render(request, "dashboard/contact.html", {"form": form})


@member_required
def assistant(request):
    """Evidence-grounded PLEIADES AI assistant."""
    from django.conf import settings
    return render(request, "dashboard/assistant.html", {
        "assistant_configured": bool(settings.GEMINI_API_KEY),
    })


@member_required
@require_POST
def assistant_chat(request):
    """Answer questions from facts retrieved live from the PLEIADES database (Gemini explains them, or a database-only answer is returned)."""
    if len(request.body) > 24000:
        return JsonResponse({"error": "Request is too large."}, status=413)
    try:
        payload = json.loads(request.body or b"{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"error": "Invalid JSON request."}, status=400)

    question = payload.get("message", "")
    history = payload.get("history", [])
    if not isinstance(question, str) or not question.strip():
        return JsonResponse({"error": "Please enter a question."}, status=400)
    if len(question) > 1200:
        return JsonResponse({"error": "Question is too long (maximum 1200 characters)."}, status=400)
    if not isinstance(history, list):
        return JsonResponse({"error": "Invalid conversation history."}, status=400)

    from satellite.ai_assistant import answer_question
    try:
        result = answer_question(question.strip(), history=history)
    except Exception:  # database problem: never leak internals
        return JsonResponse({"error": "PLEIADES AI could not read the database right now. Please try again."}, status=500)
    return JsonResponse(result, json_dumps_params={"default": str})


@operator_required
def console(request):
    """Operators only. Satellite data, IoT sites and messaging (later steps)."""
    return render(request, "dashboard/console.html")
