from django.contrib import admin

from core.models import Plugin


@admin.register(Plugin)
class PluginAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "status", "source", "version", "updated_at")
    list_filter = ("status", "source")
    search_fields = ("name", "slug")
    readonly_fields = ("installed_at", "activated_at", "updated_at")
