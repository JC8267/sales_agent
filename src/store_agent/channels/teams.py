"""Teams <-> agent mapping on the Activity Protocol, independent of the hosting SDK.

The host (Microsoft 365 Agents SDK or Teams SDK, see docs/ARCHITECTURE.md section 3) validates
the inbound JWT, then calls activity_to_request(); it sends response_to_activity() back,
and stores conversation_reference() for proactive delivery. Nothing here trusts identity
claims made in message text: the user id is the Entra object id on the validated activity.
"""

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from store_agent.channels.adaptive_cards import render_card
from store_agent.contracts import AgentRequest, AgentResponse

ADAPTIVE_CARD = "application/vnd.microsoft.card.adaptive"


class UnsupportedActivity(Exception):
    pass


def activity_to_request(activity: dict[str, Any]) -> AgentRequest:
    if activity.get("type") != "message":
        raise UnsupportedActivity(activity.get("type"))
    sender = activity.get("from") or {}
    aad_object_id = sender.get("aadObjectId")
    if not aad_object_id:
        raise UnsupportedActivity("message without an Entra object id")
    value = activity.get("value") or {}
    text = value.get("msg") or activity.get("text") or ""
    ts = activity.get("timestamp")
    return AgentRequest(
        request_id=activity.get("id") or uuid4().hex,
        user_id=aad_object_id,
        conversation_id=(activity.get("conversation") or {}).get("id", ""),
        message=_strip_mentions(text, activity),
        channel="teams",
        timestamp=datetime.fromisoformat(ts.replace("Z", "+00:00")) if ts else datetime.now(timezone.utc),
        context={"source": "card_action" if value.get("msg") else "text", "tenant_id": (activity.get("conversation") or {}).get("tenantId")},
    )


def response_to_activity(response: AgentResponse) -> dict[str, Any]:
    return {
        "type": "message",
        "text": response.text,
        "attachments": [{"contentType": ADAPTIVE_CARD, "content": render_card(response)}],
    }


def conversation_reference(activity: dict[str, Any]) -> dict[str, Any]:
    conv = activity.get("conversation") or {}
    return {
        "service_url": activity.get("serviceUrl"),
        "conversation_id": conv.get("id"),
        "conversation_type": conv.get("conversationType"),
        "tenant_id": conv.get("tenantId"),
        "bot_id": (activity.get("recipient") or {}).get("id"),
        "user_aad_object_id": (activity.get("from") or {}).get("aadObjectId"),
    }


def _strip_mentions(text: str, activity: dict[str, Any]) -> str:
    for e in activity.get("entities") or []:
        if e.get("type") == "mention" and e.get("text"):
            text = text.replace(e["text"], "")
    return " ".join(text.split())
