from django.urls import path

from . import views

app_name = "sales_dashboard"

urlpatterns = [
    path("", views.index, name="index"),
    path("run/", views.run_collector, name="run"),
]
