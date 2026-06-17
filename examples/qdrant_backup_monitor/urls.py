from django.urls import path

from . import views

app_name = "qdrant_backup_monitor"

urlpatterns = [
    path("", views.index, name="index"),
    path("run/", views.run_backup, name="run"),
]
