import math
from urllib.parse import urlencode

from django.conf import settings
from django.db import models


class Profile(models.Model):
    """Extra data for every user account (role + who the person is)."""

    class Role(models.TextChoices):
        MEMBER = "member", "Member"
        OPERATOR = "operator", "Operator"

    class UserType(models.TextChoices):
        RESPONDER = "responder", "Emergency responder"
        SCIENTIST = "scientist", "Scientist / researcher"
        LAND_MANAGER = "land_manager", "Land or forest manager"
        OTHER = "other", "Other"

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="profile",
    )
    role = models.CharField(
        max_length=20,
        choices=Role.choices,
        default=Role.MEMBER,
    )
    user_type = models.CharField(
        max_length=20,
        choices=UserType.choices,
        default=UserType.OTHER,
    )
    organization = models.CharField(max_length=120, blank=True)
    country = models.CharField(max_length=60, blank=True, default="Morocco")
    bio = models.TextField(max_length=300, blank=True)

    def __str__(self):
        return f"{self.user.username} ({self.get_role_display()})"


class SavedArea(models.Model):
    """A named area of interest (bounding box) a member wants to come back to."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="saved_areas",
    )
    name = models.CharField(max_length=80)
    south = models.FloatField()
    west = models.FloatField()
    north = models.FloatField()
    east = models.FloatField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.name} ({self.user})"

    @property
    def area_km2(self):
        mid = math.radians((self.south + self.north) / 2)
        height = (self.north - self.south) * 111.32
        width = (self.east - self.west) * 111.32 * math.cos(mid)
        return round(height * width)

    @property
    def calendar_query(self):
        return urlencode({
            "south": round(self.south, 4), "west": round(self.west, 4),
            "north": round(self.north, 4), "east": round(self.east, 4),
            "name": self.name,
        })
