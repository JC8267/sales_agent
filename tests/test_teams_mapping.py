from store_agent.channels.teams import activity_to_request, conversation_reference, response_to_activity
from store_agent.contracts import AgentResponse, Kpi, SuggestedAction

ACTIVITY = {
    "type": "message",
    "id": "act-1",
    "timestamp": "2026-09-26T18:00:00.000Z",
    "serviceUrl": "https://smba.trafficmanager.net/amer/",
    "from": {"id": "29:abc", "aadObjectId": "00000000-aaaa-bbbb-cccc-000000000001"},
    "recipient": {"id": "28:bot"},
    "conversation": {"id": "a:1xyz", "conversationType": "personal", "tenantId": "tenant-1"},
    "text": "<at>Store Agent</at> How were sales yesterday?",
    "entities": [{"type": "mention", "text": "<at>Store Agent</at>"}],
}


def test_inbound_text_activity():
    req = activity_to_request(ACTIVITY)
    assert req.user_id == "00000000-aaaa-bbbb-cccc-000000000001"
    assert req.conversation_id == "a:1xyz" and req.message == "How were sales yesterday?"
    assert req.context["source"] == "text"


def test_card_submit_becomes_normal_turn():
    req = activity_to_request({**ACTIVITY, "text": None, "value": {"msg": "Why was Storage down yesterday?"}})
    assert req.message == "Why was Storage down yesterday?" and req.context["source"] == "card_action"


def test_outbound_activity_and_reference():
    resp = AgentResponse(
        request_id="r", text="Sales were $184.3K.", title="Yesterday",
        kpis=[Kpi(label="Sales", value="$184.3K", change="▲ 7.6% vs LY", direction="up")],
        actions=[SuggestedAction(title="Show departments", message="Show sales by department")],
    )
    act = response_to_activity(resp)
    card = act["attachments"][0]["content"]
    assert act["attachments"][0]["contentType"] == "application/vnd.microsoft.card.adaptive"
    assert card["actions"] == [{"type": "Action.Submit", "title": "Show departments", "data": {"msg": "Show sales by department"}}]
    ref = conversation_reference(ACTIVITY)
    assert ref["conversation_id"] == "a:1xyz" and ref["service_url"].startswith("https://")
