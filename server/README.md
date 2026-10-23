# server —— AI 群面陪练后端

M0 骨架：FastAPI + LLM Gateway（mock/真实双模式）+ 题库 + 人格卡/难度档位 + WS 流式演示。

## 快速开始

```bash
cd server
uv venv --python 3.12 .venv && source .venv/bin/activate   # 或 python3 -m venv .venv
uv pip install -e ".[dev]"                                  # 或 pip install -e ".[dev]"

cp .env.example .env    # 不填 LLM_API_KEY 时自动走 mock，无需密钥即可跑通

pytest -q               # 测试
uvicorn app.main:app --reload --port 8000
```

打开 <http://127.0.0.1:8000/> 是浏览器演示页（选一个人格卡对话，流式输出）。

## 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | /healthz | 健康检查（含 provider 与用量） |
| GET | /api/personas | 人格卡列表 |
| GET | /api/difficulty | 难度档位 |
| GET | /api/questions?type=&industry=&difficulty=&random= | 题库查询/随机抽题 |
| WS | /ws/demo/{persona_id} | M0 演示：单 AI 人格流式对话 |

WS 协议（M0 子集，完整协议见 `docs/04§4`）：
上行 `{"type":"user_speak","text":"..."}` / `{"type":"ping"}`；
下行 `hello / speak_start / speak_chunk / speak_end / pong / error`。

## 切换真实模型

在 `.env` 填入任意 OpenAI 兼容服务的 `LLM_API_BASE + LLM_API_KEY`，
并用 `LLM_MODEL_STRONG / LLM_MODEL_LIGHT` 指定强/轻模型（默认智谱 GLM）。

## 目录

```
app/
  core/config.py        # 设置（env/.env）
  schemas/ws.py         # WS 消息协议
  services/             # llm_gateway / question_bank / personas
  agents/               # base + candidate（M0 最小实现）
  static/index.html     # 浏览器演示页
configs/                # 人格卡 personas/*.yaml + difficulty.yaml
seeds/questions/        # 种子题库
tests/                  # pytest
```
