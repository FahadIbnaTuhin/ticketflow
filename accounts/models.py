from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    class Role(models.TextChoices):
        CUSTOMER = "customer", "Customer"
        ORGANIZER = "organizer", "Organizer"

    role = models.CharField(
        max_length=10,
        choices=Role.choices,
        default=Role.CUSTOMER,
    )

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(role__in=["customer", "organizer"]),
                name="accounts_user_valid_role",
            ),
        ]

    def __str__(self):
        return self.username
