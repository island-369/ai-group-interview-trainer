"""WS 消息协议（M0 子集，完整协议见 docs/04§4）。"""

import time
from typing import Literal

from pydantic import BaseModel

IncomingType = Literal["user_speak", "ping"]


class Incoming(BaseModel):
    type: IncomingType
    text: str | None = None


def out(**fields) -> dict:
    """构造下行消息：统一附带时间戳。"""
    return {"ts": round(time.time(), 3), **fields}
