import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import HttpResponse
from django.test import RequestFactory
from django.urls import reverse

from .permissions import role_required, user_has_role

User = get_user_model()


@pytest.mark.django_db
def test_registration_creates_customer(client):
    response = client.post(
        reverse("accounts:register"),
        {
            "username": "new-customer",
            "email": "customer@example.com",
            "password1": "StrongPassword123!",
            "password2": "StrongPassword123!",
        },
    )

    assert response.status_code == 302
    assert response.url == reverse("accounts:login")
    user = User.objects.get(username="new-customer")
    assert user.email == "customer@example.com"
    assert user.role == User.Role.CUSTOMER
    assert user.check_password("StrongPassword123!")


@pytest.mark.django_db
def test_login_authenticates_user(client, django_user_model):
    user = django_user_model.objects.create_user(
        username="customer",
        password="StrongPassword123!",
    )

    response = client.post(
        reverse("accounts:login"),
        {"username": user.username, "password": "StrongPassword123!"},
    )

    assert response.status_code == 302
    assert response.url == reverse("accounts:home")
    assert "_auth_user_id" in client.session


@pytest.mark.django_db
def test_logout_ends_session(client, django_user_model):
    user = django_user_model.objects.create_user(
        username="customer",
        password="StrongPassword123!",
    )
    client.force_login(user)

    response = client.post(reverse("accounts:logout"))

    assert response.status_code == 302
    assert response.url == reverse("accounts:login")
    assert "_auth_user_id" not in client.session


@pytest.mark.django_db
def test_customer_is_default_role(django_user_model):
    user = django_user_model.objects.create_user(
        username="default-role",
        password="StrongPassword123!",
    )

    assert user.role == django_user_model.Role.CUSTOMER


def test_invalid_role_fails_model_validation(django_user_model):
    user = django_user_model(username="invalid-role", role="admin")

    with pytest.raises(ValidationError):
        user.full_clean()


@pytest.mark.django_db
def test_role_permissions_allow_only_matching_authenticated_users(django_user_model):
    organizer = django_user_model.objects.create_user(
        username="organizer",
        password="StrongPassword123!",
        role=django_user_model.Role.ORGANIZER,
    )
    customer = django_user_model.objects.create_user(
        username="customer",
        password="StrongPassword123!",
    )

    @role_required(django_user_model.Role.ORGANIZER)
    def organizer_view(request):
        return HttpResponse("allowed")

    request = RequestFactory().get("/organizer-only/")
    request.user = organizer
    assert organizer_view(request).content == b"allowed"
    assert user_has_role(organizer, django_user_model.Role.ORGANIZER)

    request.user = customer
    with pytest.raises(PermissionDenied):
        organizer_view(request)
    assert not user_has_role(customer, django_user_model.Role.ORGANIZER)


def test_role_permission_redirects_anonymous_user():
    @role_required("organizer")
    def organizer_view(request):
        return HttpResponse("allowed")

    request = RequestFactory().get("/organizer-only/")
    request.user = AnonymousUser()

    response = organizer_view(request)

    assert response.status_code == 302
    assert response.url.startswith(reverse("accounts:login"))
