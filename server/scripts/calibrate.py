"""真实模型校准驱动：脚本用户走完整场群面，落盘转录与报告。

用法：PYTHONPATH=. .venv/bin/python scripts/calibrate.py [question_id] [difficulty] [port]
"""

import asyncio
import json
import sys
import time
import urllib.request

import websockets

BASE = f"http://127.0.0.1:{sys.argv[3] if len(sys.argv) > 3 else '8125'}"
WS_BASE = BASE.replace("http", "ws")
QUESTION = sys.argv[1] if len(sys.argv) > 1 else "q_2026_008"
DIFFICULTY = sys.argv[2] if len(sys.argv) > 2 else "realistic"

# 中速节奏：真实 LLM 每次生成 3~8s，讨论 3 分钟足够 10+ 轮
DURATIONS = {
    "briefing": 5, "reading": 3, "opening_per": 25, "discussion": 180,
    "summary_solicit": 8, "summary_speech": 90, "closing": 1,
    "director_gap": [0.6, 1.4], "cold_wait": 8,
}

USER_OPENING = ("我的思路是先把问题拆成三层：渠道、产品、营销。二季度铺货率没变，"
                "那下滑大概率出在营销端的转化和渠道库存上，后面我建议围绕这两条线索验证。")
USER_DISCUSS = [
    "我补充一个细节：回款周期从45天涨到70天，说明渠道压货严重，经销商可能是在清库存，这会直接影响终端动销数据。",
    "刚才两位说的竞品和自播转化我都同意，但要注意相关不等于因果。我建议先分区域拉数据验证，再决定是跟投放还是先解决库存。",
    "时间不多了，我们收敛一下：主因先定为渠道库存积压+竞品分流，短期止血动作是先停压货、清库存，同意的扣个头？",
]
USER_SUMMARY = ("我代表小组总结：销量下滑18%的主因是渠道库存积压（回款周期45→70天佐证）"
                "叠加竞品电解质水分流；验证方法为分区域动销数据对比；短期动作是暂停向渠道压货、"
                "配合经销商清库存，一个月内复盘动销恢复情况。")


def post(path: str, payload: dict) -> dict:
    req = urllib.request.Request(
        BASE + path, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "X-Anon-Token": "calibration01"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def fetch_json(url: str) -> tuple[int, dict | None]:
    """同步 HTTP GET（供 asyncio.to_thread 调用）；4xx/5xx 返回状态码不抛异常。"""
    try:
        with urllib.request.urlopen(url, timeout=15) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, None


def save_result(out: str, payload: dict) -> None:
    with open(out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)


async def main() -> None:
    created = post("/api/sessions", {
        "question_id": QUESTION, "difficulty": DIFFICULTY, "durations": DURATIONS})
    sid = created["session_id"]
    print(f"session: {sid}  difficulty={DIFFICULTY}  question={QUESTION}", flush=True)

    discuss_sent = 0
    opening_sent = summary_sent = applied = review = False
    phase_now = "INIT"
    transcript: list[dict] = []
    t0 = time.monotonic()

    async with websockets.connect(f"{WS_BASE}/ws/session/{sid}", max_size=2**22) as ws:
        await ws.send(json.dumps({"type": "user_ready"}))
        while time.monotonic() - t0 < 420:
            try:
                m = json.loads(await asyncio.wait_for(ws.recv(), timeout=30))
            except asyncio.TimeoutError:
                continue
            t = m.get("type")
            if t in ("speak_end", "interviewer", "system"):
                if t == "speak_end":
                    transcript.append({"who": m.get("name") or m.get("agent"), "text": m.get("text", "")})
                    print(f"  [{m.get('name')}] {m.get('text', '')[:60]}", flush=True)
                elif t == "interviewer":
                    transcript.append({"who": "面试官", "text": m.get("text", "")})
                    print(f"  [面试官] {m.get('text', '')[:60]}", flush=True)
            elif t == "phase":
                phase_now = m.get("phase", phase_now)
                print(f"== {m.get('phase_label')} ==", flush=True)
            elif t == "review_ready":
                review = True
                break

            # ---- 脚本用户行为 ----
            if t == "phase" and m.get("phase") == "READING":
                await asyncio.sleep(1.0)
                await ws.send(json.dumps({"type": "user_skip"}))
            elif t == "interviewer" and "有请" in m.get("text", "") \
                    and "总结" not in m.get("text", "") and not opening_sent:
                await asyncio.sleep(0.5)  # 防竞态：等服务端创建好等待 future
                await ws.send(json.dumps({"type": "user_speak", "text": USER_OPENING}))
                opening_sent = True
            elif t == "speak_end" and phase_now == "DISCUSSION" \
                    and discuss_sent < len(USER_DISCUSS) \
                    and (m.get("agent") or m.get("agent_id")) not in (None, "user"):
                await asyncio.sleep(2.0)
                await ws.send(json.dumps({"type": "user_speak", "text": USER_DISCUSS[discuss_sent]}))
                discuss_sent += 1
            elif t == "phase" and m.get("phase") == "SUMMARY" and not applied:
                await ws.send(json.dumps({"type": "summary_apply"}))
                applied = True
            elif t == "interviewer" and "有请" in m.get("text", "") and "总结" in m.get("text", "") \
                    and applied and not summary_sent:
                await asyncio.sleep(0.5)
                await ws.send(json.dumps({"type": "user_speak", "text": USER_SUMMARY}))
                summary_sent = True

    print(f"\nreview_ready={review}  elapsed={time.monotonic()-t0:.0f}s  "
          f"user_turns={1+discuss_sent+(1 if summary_sent else 0)}", flush=True)

    # 落盘转录与报告（阻塞 IO 走线程池，避免阻塞事件循环）
    status, full = await asyncio.to_thread(fetch_json, f"{BASE}/api/sessions/{sid}/transcript")
    if status != 200 or full is None:
        print("transcript 获取失败", flush=True)
        return
    report = None
    for _ in range(40):
        status, body = await asyncio.to_thread(fetch_json, f"{BASE}/api/sessions/{sid}/report")
        if status == 200 and body is not None:
            report = body
            break
        await asyncio.sleep(3)

    out = f"/tmp/gi_calibrate_{sid}.json"
    await asyncio.to_thread(
        save_result, out, {"session_id": sid, "transcript": full, "report": report})
    print(f"saved: {out}", flush=True)
    if report:
        print(f"overall={report['overall']} rank={report['rank']}/{report['total_members']} "
              f"note={report['note']}", flush=True)
        for d in report["dimensions"]:
            ev = d["evidence"][0]["quote"][:24] if d.get("evidence") else "-"
            print(f"  {d['label']} {d['score']}  | {ev}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
