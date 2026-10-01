"""Маршруты приложения КР (подключены в django_project/urls.py по адресу /kr/)."""
from django.urls import path

from . import views

app_name = "kr"

urlpatterns = [
    path("", views.index, name="index"),                      # страница дашборда (index.html)
    path("api/metrics/", views.metrics_api, name="metrics"),  # расчёты для графиков (JSON)
]
