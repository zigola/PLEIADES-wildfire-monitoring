from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import UserAdmin

from .models import Profile, SavedArea

User = get_user_model()


class ProfileInline(admin.StackedInline):
    model = Profile
    can_delete = False
    verbose_name_plural = "PLEIADES profile"


admin.site.unregister(User)


@admin.register(User)
class PolarisUserAdmin(UserAdmin):
    inlines = [ProfileInline]
    list_display = ("username", "email", "polaris_role", "is_staff", "is_active")
    list_select_related = ("profile",)

    @admin.display(description="Role")
    def polaris_role(self, obj):
        from .roles import user_role
        return user_role(obj)


@admin.register(SavedArea)
class SavedAreaAdmin(admin.ModelAdmin):
    list_display = ("name", "user", "south", "west", "north", "east", "created_at")
    search_fields = ("name", "user__username")
