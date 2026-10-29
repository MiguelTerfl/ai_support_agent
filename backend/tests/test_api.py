import json

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

from conftest import FakeDB, FakeLLM, RETURNS, say, tool_call


def parse_sse(text: str) -> list[dict]:
    events = []
    for block in text.split("\n\n"):
        data = [l[6:] for l in block.splitlines() if l.startswith("data: ")]
        if data:
            events.append(json.loads(data[0]))
    return events


def client(settings, llm, db=None):
    return TestClient(create_app(settings, llm=llm, db=db or FakeDB([RETURNS])))


def body(text: str) -> dict:
    return {"messages": [{"role": "user", "content": text}]}


def test_chat_streams_events_and_health(settings):
    llm = FakeLLM([tool_call("search_knowledge_base", query="returns"), say("You have 30 days.")])
    with client(settings, llm) as c:
        r = c.post("/api/chat", json=body("What is the return deadline?"))
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
        evs = parse_sse(r.text)
        assert [e["type"] for e in evs] == ["status", "tool_call", "tool_result", "status", "answer", "done"]
        assert c.get("/api/health").json() == {"ok": True, "llm": True, "db": True, "model": "fake-model"}


def test_confirmation_flow_with_replay_protection(settings):
    db = FakeDB()
    llm = FakeLLM([tool_call("create_ticket", subject="Coffee maker fault", description="It has not turned on since yesterday", email="luis@example.com")])
    with client(settings, llm, db) as c:
        conf = next(e for e in parse_sse(c.post("/api/chat", json=body("please open a ticket")).text) if e["type"] == "confirmation")
        assert db.tickets == []
        ok = c.post("/api/confirm", json={"token": conf["token"], "approve": True})
        assert ok.status_code == 200 and "TCK-000001" in ok.json()["message"] and len(db.tickets) == 1
        assert c.post("/api/confirm", json={"token": conf["token"], "approve": True}).status_code == 409
        assert len(db.tickets) == 1


def test_confirm_reject_tampered_and_failure_allows_retry(settings):
    db = FakeDB()
    llm = FakeLLM([tool_call("create_ticket", subject="Coffee maker fault", description="It has not turned on since yesterday", email="luis@example.com")])
    with client(settings, llm, db) as c:
        conf = next(e for e in parse_sse(c.post("/api/chat", json=body("please open a ticket")).text) if e["type"] == "confirmation")
        assert c.post("/api/confirm", json={"token": conf["token"] + "x", "approve": True}).status_code == 400
        assert "didn't create" in c.post("/api/confirm", json={"token": conf["token"], "approve": False}).json()["message"]
        db.fail_ticket = True
        assert c.post("/api/confirm", json={"token": conf["token"], "approve": True}).status_code == 500
        db.fail_ticket = False
        assert c.post("/api/confirm", json={"token": conf["token"], "approve": True}).status_code == 200


def test_rate_limit_returns_429():
    s = Settings(rate_limit_per_min=2, _env_file=None)
    with client(s, FakeLLM([say("hello")] * 5)) as c:
        codes = [c.post("/api/chat", json=body("hello")).status_code for _ in range(3)]
    assert codes == [200, 200, 429]


def test_internal_key_required_and_client_ip_trusted_only_with_key():
    s = Settings(internal_api_key="k123", rate_limit_per_min=1, _env_file=None)
    with client(s, FakeLLM([say("a")] * 5)) as c:
        assert c.post("/api/chat", json=body("hello")).status_code == 401
        h = {"x-internal-key": "k123"}
        assert c.post("/api/chat", json=body("hello"), headers={**h, "x-client-ip": "1.1.1.1"}).status_code == 200
        assert c.post("/api/chat", json=body("hello"), headers={**h, "x-client-ip": "1.1.1.1"}).status_code == 429
        assert c.post("/api/chat", json=body("hello"), headers={**h, "x-client-ip": "2.2.2.2"}).status_code == 200  # another IP


def test_request_validation(settings):
    with client(settings, FakeLLM()) as c:
        assert c.post("/api/chat", json={"messages": []}).status_code == 422
        assert c.post("/api/chat", json={"messages": [{"role": "assistant", "content": "x"}]}).status_code == 422
        assert c.post("/api/chat", json={"messages": [{"role": "system", "content": "x"}]}).status_code == 422
