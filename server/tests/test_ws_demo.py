"""WS demo 全流程（M0 验收项：单 AI 对话流式跑通）与 REST 面。"""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_api_surface():
    r = client.get("/healthz")
    assert r.status_code == 200 and r.json()["status"] == "ok"
    assert len(client.get("/api/personas").json()) == 5
    assert len(client.get("/api/difficulty").json()) == 4
    qs = client.get("/api/questions", params={"type": "ranking"}).json()
    assert len(qs) >= 3
    one = client.get("/api/questions", params={"random": "true"}).json()
    assert len(one) == 1 and one[0]["id"].startswith("q_2026_")
    assert client.get("/").status_code == 200  # 演示页


def test_ws_demo_streaming_flow():
    with client.websocket_connect("/ws/demo/c_001") as ws:
        hello = ws.receive_json()
        assert hello["type"] == "hello" and hello["agent"] == "c_001"

        ws.send_json({"type": "user_speak", "text": "你觉得今天面试难吗"})
        start = ws.receive_json()
        assert start["type"] == "speak_start" and start["agent"] == "c_001"

        texts = []
        while (m := ws.receive_json())["type"] == "speak_chunk":
            texts.append(m["chunk"])
        assert m["type"] == "speak_end" and m["interrupted"] is False
        assert "".join(texts).strip(), "流式内容不应为空"

        # 第二轮：验证上下文累积后仍正常
        ws.send_json({"type": "user_speak", "text": "那你准备怎么破题"})
        assert ws.receive_json()["type"] == "speak_start"
        while (m := ws.receive_json())["type"] == "speak_chunk":
            pass
        assert m["type"] == "speak_end"

        ws.send_json({"type": "ping"})
        assert ws.receive_json()["type"] == "pong"


def test_ws_demo_unknown_persona():
    with client.websocket_connect("/ws/demo/nope") as ws:
        m = ws.receive_json()
        assert m["type"] == "error" and "nope" in m["message"]


def test_ws_demo_rejects_empty_text():
    with client.websocket_connect("/ws/demo/c_002") as ws:
        assert ws.receive_json()["type"] == "hello"
        ws.send_json({"type": "user_speak", "text": "   "})
        assert ws.receive_json()["type"] == "error"
