"""WS 消息协议（M1 子集，完整协议见 docs/04§4）。"""

import time
from typing import Literal

from pydantic import BaseModel

# 会话与 demo 共用的上行类型；对局语义由 SessionRuntime.handle_user 路由
IncomingType = Literal[
    "user_speak", "ping", "user_ready", "user_typing", "user_skip", "summary_apply"
]


class Incoming(BaseModel):
    type: IncomingType
    text: str | None = None


def out(**fields) -> dict:
    """构造下行消息：统一附带时间戳。"""
    return {"ts": round(time.time(), 3), **fields}

