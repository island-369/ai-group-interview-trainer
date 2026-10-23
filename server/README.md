# server —— AI 群面陪练后端

M1 文本 MVP：六阶段完整群面对局（导演意愿调度）+ SQLite 持久化 + 8 维评估报告 + 浏览器对局室。

## 快速开始

```bash
cd server
uv venv --python 3.12 .venv && source .venv/bin/activate   # 或 python3 -m venv .venv
uv pip install -e ".[dev]"                                  # 或 pip install -e ".[dev]"

cp .env.example .env    # 不填 LLM_API_KEY 时自动走 mock，无需密钥即可跑通

pytest -q               # 测试（34 项，含整场 e2e）
uvicorn app.main:app --reload --port 8000
```

- <http://127.0.0.1:8000/room.html> —— **群面对局室**（完整流程，可选"快速体验"档约 3 分钟一场）
- <http://127.0.0.1:8000/> —— 单 AI 人格闲聊演示（M0 遗留）
- 对局结束后对局室内会出现报告链接（`/report.html?session_id=…`，雷达图 + 逐维度证据）

## 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | /healthz | 健康检查（含 provider 与用量） |
| GET | /api/personas ｜ /api/difficulty ｜ /api/questions | 人格卡 / 难度档位 / 题库 |
| POST | /api/sessions | 创建对局（难度缩放人格快照，返回 ws_url 与匿名 token） |
| GET | /api/sessions/{id} ｜ /transcript ｜ /report | 会话详情 / 转录 / 报告（未结束 409，生成中 202） |
| WS | /ws/session/{id} | 对局会话：下行快照+事件流，上行用户输入 |
| WS | /ws/demo/{persona_id} | M0 单 AI 演示 |

对局 WS 协议要点（完整见 docs/04§4）：
上行 `user_ready / user_typing / user_speak / user_skip / summary_apply / ping`；
下行 `snapshot / phase / interviewer / speak_start / speak_chunk / speak_end / system / error / review_ready`。

## 切换真实模型

在 `.env` 填入任意 OpenAI 兼容服务的 `LLM_API_BASE + LLM_API_KEY`，
并用 `LLM_MODEL_STRONG / LLM_MODEL_LIGHT` 指定强/轻模型（默认智谱 GLM）。
真实模式下：候选人发言与逐维度评估走 LLM（证据缺失自动回退启发式并标记）；
面试官话术为模板；mock 模式全链路确定性，用于测试与无密钥体验。

## 目录

```
app/
  core/                 # config / phases(状态机规格) / director(意愿调度) / session(运行时)
  schemas/ws.py         # WS 消息协议
  services/             # llm_gateway / question_bank / personas / rubrics / evaluation / store
  agents/               # base / candidate(四层prompt) / interviewer(模板话术)
  static/               # index(M0演示) / room(对局室) / report(报告页)
configs/                # 人格卡 personas/*.yaml + difficulty.yaml + rubrics.yaml
seeds/questions/        # 种子题库 ×10
data/app.db             # SQLite（gitignored）
tests/                  # pytest（config/题库/人格/gateway/director/评估/e2e）
```
