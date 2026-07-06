"""
Inbound dispatch — the worker side of the inbound pipeline.

Runs in a django-q worker (fast-ack already happened in the webhook view). It
parses the payload, logs the inbound message, runs the channel's handler, and
delivers any synchronous reply. The ``script`` handler returns None and replies
asynchronously from its own run via ``pyrunner_notify.reply()``.
"""

import logging

logger = logging.getLogger(__name__)


def dispatch_inbound(channel_id: str, payload: dict) -> None:
    from core.models import Channel, ChannelMessage
    from core.services.channels import get_handler, get_provider

    channel = Channel.objects.filter(id=channel_id).first()
    if channel is None:
        return
    provider = get_provider(channel.provider)
    msg = provider.parse_inbound(channel, payload)
    if msg is None:
        return

    ChannelMessage.objects.create(
        channel=channel,
        direction=ChannelMessage.Direction.IN,
        text=msg.text,
        sender_json=msg.sender,
        reply_ref_json=msg.reply_ref,
        handler=channel.inbound_handler,
    )

    handler = get_handler(channel.inbound_handler)
    if handler is None:
        return  # notify-only / unknown handler

    # Per-channel daily reply cap (the cost fuse). DB-counted, so it is robust
    # across processes (unlike the per-process LocMem rate-limit caches).
    if channel.daily_cap_reached():
        _reply(channel, provider, msg, "Daily limit reached — please try again tomorrow.", status="rejected")
        return

    try:
        out = handler(channel, msg)
    except Exception as e:  # noqa: BLE001 — any handler failure must still reply
        logger.exception("Inbound handler failed for channel %s", channel.id)
        _reply(
            channel, provider, msg,
            "Sorry — something went wrong handling that.",
            status="error", error=str(e),
        )
        return

    if out is not None:
        _reply(channel, provider, msg, out.text, reply_ref=out.reply_ref)


def _reply(channel, provider, msg, text, *, reply_ref=None, status="ok", error=""):
    """Deliver a synchronous reply and log it (best-effort; never raises)."""
    from core.models import ChannelMessage
    from core.services.channels import ChannelError, OutboundMessage

    ref = reply_ref if reply_ref is not None else msg.reply_ref
    try:
        provider.send(channel, OutboundMessage(text=text, reply_ref=ref))
    except ChannelError as e:
        status, error = "error", str(e)
        logger.warning("Failed to send inbound reply on channel %s: %s", channel.id, e)
    ChannelMessage.objects.create(
        channel=channel,
        direction=ChannelMessage.Direction.OUT,
        text=text,
        reply_ref_json=ref or {},
        handler=channel.inbound_handler,
        status=status,
        error=error,
    )
