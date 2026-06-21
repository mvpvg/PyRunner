"""
``manage.py sandbox_check`` — probe this host's script-execution sandbox.

Reports the protection tier the host can actually deliver (full / rlimits_only /
none) and why. With ``--save`` it caches the result on GlobalSettings, the same
write the dashboard "Test sandbox on this host" button performs — so an operator
can verify capability from the shell on the real deployment, where it matters
(the Docker Desktop kernel may differ from the production VPS).
"""

from django.core.management.base import BaseCommand

from core.services.sandbox import CAP_FULL, CAP_NONE, probe_sandbox, run_and_store_probe


class Command(BaseCommand):
    help = "Probe whether this host can sandbox script execution (and optionally cache it)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--save",
            action="store_true",
            help="Cache the result on GlobalSettings (sandbox_capability + sandbox_checked_at).",
        )

    def handle(self, *args, **options):
        result = run_and_store_probe() if options["save"] else probe_sandbox()

        style = self.style.SUCCESS if result.is_full else (
            self.style.WARNING if result.capability != CAP_NONE else self.style.NOTICE
        )
        self.stdout.write(style(f"Sandbox capability: {result.capability}"))
        if result.tool:
            self.stdout.write(f"  Tool: {result.tool}")
        self.stdout.write(f"  Detail: {result.detail}")

        if result.capability == CAP_FULL:
            self.stdout.write(
                "  -> Full filesystem/network sandbox + resource limits available."
            )
        elif result.capability == CAP_NONE:
            self.stdout.write(
                "  -> No isolation on this host (scripts run unrestricted)."
            )
        else:
            self.stdout.write(
                "  -> Resource limits apply; the filesystem/network sandbox is unavailable."
            )

        if options["save"]:
            self.stdout.write(self.style.SUCCESS("Saved to GlobalSettings."))
