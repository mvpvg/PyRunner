"""
Service for datastore statistics and operations.
"""

from django.db.models import Sum
from django.db.models.functions import Coalesce, Length

from core.models import DataStore, DataStoreEntry
from core.services.environment_service import EnvironmentService


class DatastoreService:
    """Service for datastore statistics and operations."""

    @classmethod
    def get_datastores_with_stats(cls, workspace=None):
        """
        Get datastores annotated with size, optionally scoped to a workspace.

        Args:
            workspace: when given, only stores in this workspace are returned
                (tenancy). ``None`` keeps the legacy "all stores" behavior.

        Returns:
            QuerySet of DataStore objects with annotations:
            - size_bytes: Total size of value_json fields in bytes

        Note: entry_count is provided by the DataStore model property.
        """
        qs = DataStore.objects.annotate(
            size_bytes=Coalesce(Sum(Length("entries__value_json")), 0),
        )
        if workspace is not None:
            qs = qs.for_workspace(workspace)
        return qs.order_by("name")

    @classmethod
    def get_total_size(cls, workspace=None) -> int:
        """
        Get total size of datastore entries in bytes, optionally scoped.

        Args:
            workspace: when given, only entries of stores in this workspace count.

        Returns:
            Total size in bytes
        """
        qs = DataStoreEntry.objects.all()
        if workspace is not None:
            qs = qs.filter(datastore__workspace=workspace)
        result = qs.aggregate(total=Coalesce(Sum(Length("value_json")), 0))
        return result["total"]

    @classmethod
    def format_size(cls, size_bytes: int) -> str:
        """Format size in human-readable format."""
        return EnvironmentService.format_disk_usage(size_bytes)
