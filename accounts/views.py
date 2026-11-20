from django.contrib.auth import views as auth_views
from django.contrib.auth.mixins import LoginRequiredMixin
from django.urls import reverse_lazy
from django.views.generic import CreateView, TemplateView

from .forms import RegistrationForm


class RegistrationView(CreateView):
    form_class = RegistrationForm
    template_name = "accounts/register.html"
    success_url = reverse_lazy("accounts:login")


class AccountHomeView(LoginRequiredMixin, TemplateView):
    template_name = "accounts/home.html"


LoginView = auth_views.LoginView
LogoutView = auth_views.LogoutView
