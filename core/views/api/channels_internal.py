"""
Internal Channels send API (loopback-only).

The only way an in-run script can deliver a chat message or email: a sandboxed
run has no DB / encryption key / provider access, so ``pyrunner_notify`` POSTs
here and PyRunner does the send server-side. Authenticated by the same signed
per-run token the internal datastore API uses (loopback-only), so the run's
workspace is derived server-side and a run cannot send through another
workspace's channel.

POST /internal/channels/send
  body: {
    "target": "<channel name>" | "email",
    "text": "...",
    "subject": "...",       # email only (optional)
    "to": "...",            # email only (optional; defaults to core default)
    "reply_ref": {...}      # chat target override (e.g. {"chat_id": ...})
  }
"""

import json
import logging

from django.core.cache import cache
from django.http import HttpRequest, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from core.views.api.decorators import internal_datastore_token_required

logger = logging.getLogger(__name__)

# Per-run send brake: a runaway script can't spam a provider. Generous enough
# for normal use; the cap is per run, not per channel.
_RUN_SEND_LIMIT = 30
_RUN_SEND_WINDOW = 60  # seconds


def _bad_request(message: str) -> JsonResponse:
    return JsonResponse(
        {"error": {"code": "BAD_REQUEST", "message": message}}, status=400
    )


@csrf_exempt
@require_http_methods(["POST"])
@internal_datastore_token_required
def send(request: HttpRequest) -> JsonResponse:
    run_id = (getattr(request, "datastore_run", None) or {}).get("run_id")

    # Per-run rate limit.
    rate_key = f"channel_send_rate_{run_id}"
    count = cache.get(rate_key, 0)
    if count >= _RUN_SEND_LIMIT:
        return JsonResponse(
            {"error": {"code": "RATE_LIMITED", "message": "Per-run send rate exceeded."}},
            status=429,
        )
    cache.set(rate_key, count + 1, _RUN_SEND_WINDOW)

    try:
        data = json.loads(request.body or b"{}")
    except (ValueError, TypeError):
        return _bad_request("Body must be valid JSON")
    if not isinstance(data, dict):
        return _bad_request("Body must be a JSON object")

    target = (data.get("target") or "").strip()
    text = data.get("text")
    if not target:
        return _bad_request("Missing 'target'")
    if not isinstance(text, str) or not text:
        return _bad_request("Missing 'text'")

    # --- email target: delegate to PyRunner core email ---
    if target == "email":
        from core.services import NotificationService

        try:
            NotificationService.send_email(
                subject=data.get("subject") or "[PyRunner] Notification",
                body=text,
                to=data.get("to") or None,
            )
        except Exception as e:
            logger.warning("channels send email failed for run %s: %s", run_id, e)
            return JsonResponse(
                {"error": {"code": "SEND_FAILED", "message": str(e)}}, status=502
            )
        return JsonResponse({"ok": True})

    # --- chat channel target: resolve within the run's workspace ---
    from core.models import Channel, Workspace
    from core.services import ChannelService
    from core.services.channels import ChannelError

    ws_id = getattr(request, "datastore_workspace", None)
    if ws_id is None:
        default_ws = Workspace.get_default()
        ws_id = default_ws.id if default_ws else None

    channel = Channel.objects.filter(workspace_id=ws_id, name=target).first()
    if channel is None:
        return JsonResponse(
            {"error": {"code": "CHANNEL_NOT_FOUND", "message": f"Channel '{target}' not found"}},
            status=404,
        )
    if not channel.enabled:
        return _bad_request(f"Channel '{target}' is disabled")

    reply_ref = data.get("reply_ref")
    if reply_ref is not None and not isinstance(reply_ref, dict):
        return _bad_request("'reply_ref' must be an object")

    try:
        result = ChannelService.send(channel, text, reply_ref=reply_ref)
    except ChannelError as e:
        return JsonResponse(
            {"error": {"code": "SEND_FAILED", "message": str(e)}}, status=502
        )
    return JsonResponse({"ok": True, "result": result})
