from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import UserCreationForm

from .models import Profile, SavedArea

User = get_user_model()


class RegisterForm(UserCreationForm):
    email = forms.EmailField(required=True)
    user_type = forms.ChoiceField(
        label="I am a",
        choices=Profile.UserType.choices,
        initial=Profile.UserType.OTHER,
    )

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("username", "email")

    def clean_email(self):
        email = self.cleaned_data["email"].strip()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("An account with this email already exists.")
        return email

    def save(self, commit=True):
        user = super().save(commit=False)
        user.email = self.cleaned_data["email"]
        if commit:
            user.save()
            profile, _ = Profile.objects.get_or_create(user=user)
            profile.user_type = self.cleaned_data["user_type"]
            profile.save(update_fields=["user_type"])
        return user


class AccountForm(forms.ModelForm):
    """Name and e-mail on the User row."""

    class Meta:
        model = User
        fields = ("first_name", "last_name", "email")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["email"].required = True

    def clean_email(self):
        email = self.cleaned_data["email"].strip()
        clash = User.objects.filter(email__iexact=email).exclude(pk=self.instance.pk)
        if clash.exists():
            raise forms.ValidationError("Another account already uses this email.")
        return email


class ProfileForm(forms.ModelForm):
    """Role is deliberately NOT editable here: only an administrator can change it."""

    class Meta:
        model = Profile
        fields = ("user_type", "organization", "country", "bio")
        widgets = {"bio": forms.Textarea(attrs={"rows": 3})}


class SavedAreaForm(forms.ModelForm):
    class Meta:
        model = SavedArea
        fields = ("name", "south", "west", "north", "east")

    def clean(self):
        data = super().clean()
        s, w, n, e = (data.get(k) for k in ("south", "west", "north", "east"))
        if None in (s, w, n, e):
            return data
        if not (-90 <= s < n <= 90):
            raise forms.ValidationError("Latitudes must satisfy -90 <= south < north <= 90.")
        if not (-180 <= w < e <= 180):
            raise forms.ValidationError("Longitudes must satisfy -180 <= west < east <= 180.")
        return data
