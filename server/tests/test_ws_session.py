"""会话 WS 协议层：上行消息类型与错误字段（回归：控制类消息曾被校验拒绝）。

注意：TestClient 必须用作上下文管理器——对局运行时是后台任务，
需要 POST 与 WS 共享同一个事件循环（生产环境 uvicorn 天然单循环）。
"""

from fastapi.testclient import TestClient

from app.main import app

FAST = {
    "briefing": 0.5, "reading": 1.0, "opening_per": 1.0, "discussion": 3.0,
    "summary_solicit": 1.0, "summary_speech": 1.5, "closing": 0.2,
    "director_gap": [0.05, 0.15], "cold_wait": 0.8,
}


def test_ws_session_protocol():
    with TestClient(app) as client:
        r = client.post(
            "/api/sessions",
            json={"question_id": "q_2026_003", "difficulty": "newbie", "durations": FAST},
        )
        assert r.status_code == 200
        sid = r.json()["session_id"]

        with client.websocket_connect(f"/ws/session/{sid}") as ws:
            snap = ws.receive_json()
            assert snap["type"] == "snapshot" and snap["phase"] == "INIT"
            assert len(snap["members"]) == 7  # 用户 + 5 AI + 面试官

            # 控制类消息必须被接受且不产生错误（此前 bug：user_ready/user_typing 被拒）
            ws.send_json({"type": "user_typing"})
            ws.send_json({"type": "user_ready"})

            types, errors = [], []
            for _ in range(30):
                m = ws.receive_json()
                types.append(m["type"])
                if m["type"] == "error":
                    errors.append(m)
                if m["type"] == "phase" and m["phase"] == "READING":
                    break
            assert "phase" in types and not errors, f"协议错误: {errors}"

            # 非法类型 → 错误消息必须带 text 字段（前端读 m.text）
            ws.send_json({"type": "bogus"})
            got = [ws.receive_json() for _ in range(3)]
            err = next(m for m in got if m["type"] == "error")
            assert err.get("text"), f"错误消息缺 text 字段: {err}"


def test_ws_session_unknown_id():
    with TestClient(app) as client, client.websocket_connect("/ws/session/s_nope") as ws:
        m = ws.receive_json()
        assert m["type"] == "error"
