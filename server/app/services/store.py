"""SQLite DAO：同步写 + 进程内锁（M1 单机够用；M3 切 PostgreSQL 时换 SQLAlchemy）。"""

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

_SCHEMA = (Path(__file__).resolve().parent.parent / "models" / "schema.sql").read_text(encoding="utf-8")


class Store:
    def __init__(self, db_path: str | Path):
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.execute("PRAGMA journal_mode=WAL")

    def _exec(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur

    def _query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    # ---------------------------------------------------------------- users
    def get_or_create_user(self, anon_token: str) -> str:
        rows = self._query("SELECT id FROM users WHERE anon_token=?", (anon_token,))
        if rows:
            return rows[0]["id"]
        user_id = f"u_{anon_token[:12]}"
        self._exec("INSERT INTO users(id, anon_token) VALUES(?,?)", (user_id, anon_token))
        return user_id

    # ------------------------------------------------------------- sessions
    def create_session(self, session_id: str, user_id: str, question_id: str, config: dict) -> None:
        self._exec(
            "INSERT INTO sessions(id, user_id, question_id, config, status, phase)"
            " VALUES(?,?,?,?, 'waiting', 'INIT')",
            (session_id, user_id, question_id, json.dumps(config, ensure_ascii=False)),
        )

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        rows = self._query("SELECT * FROM sessions WHERE id=?", (session_id,))
        if not rows:
            return None
        row = dict(rows[0])
        row["config"] = json.loads(row["config"])
        return row

    def update_session(self, session_id: str, *, status: str | None = None, phase: str | None = None) -> None:
        sets, params = [], []
        if status:
            sets.append("status=?"); params.append(status)
        if phase:
            sets.append("phase=?"); params.append(phase)
        if status == "finished":
            sets.append("ended_at=datetime('now')")
        params.append(session_id)
        self._exec(f"UPDATE sessions SET {', '.join(sets)} WHERE id=?", tuple(params))

    def add_member(
        self, session_id: str, member_id: str, role: str, display_name: str, persona: dict | None
    ) -> None:
        self._exec(
            "INSERT INTO session_members(session_id, member_id, role, display_name, persona)"
            " VALUES(?,?,?,?,?)",
            (session_id, member_id, role, display_name, json.dumps(persona, ensure_ascii=False) if persona else None),
        )

    def get_members(self, session_id: str) -> list[dict[str, Any]]:
        rows = self._query("SELECT * FROM session_members WHERE session_id=?", (session_id,))
        members = []
        for r in rows:
            m = dict(r)
            m["persona"] = json.loads(m["persona"]) if m["persona"] else None
            members.append(m)
        return members

    # ------------------------------------------------------------- messages
    def append_message(
        self, session_id: str, kind: str, text: str, speaker_id: str | None = None, meta: dict | None = None
    ) -> int:
        """seq 全序：同一会话内自增（锁内读改写）。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT COALESCE(MAX(seq),0)+1 AS nx FROM messages WHERE session_id=?", (session_id,)
            ).fetchone()
            seq = row["nx"]
            self._conn.execute(
                "INSERT INTO messages(session_id, seq, speaker_id, kind, text, meta) VALUES(?,?,?,?,?,?)",
                (session_id, seq, speaker_id, kind, text,
                 json.dumps(meta, ensure_ascii=False) if meta else None),
            )
            self._conn.commit()
        return seq

    def list_messages(self, session_id: str) -> list[dict[str, Any]]:
        rows = self._query(
            "SELECT * FROM messages WHERE session_id=? ORDER BY seq", (session_id,)
        )
        out = []
        for r in rows:
            m = dict(r)
            m["meta"] = json.loads(m["meta"]) if m["meta"] else None
            out.append(m)
        return out

    # -------------------------------------------------------- director logs
    def save_director_log(self, session_id: str, turn: int, decision: dict) -> None:
        self._exec(
            "INSERT INTO director_logs(session_id, turn, decision) VALUES(?,?,?)",
            (session_id, turn, json.dumps(decision, ensure_ascii=False)),
        )

    # -------------------------------------------------------------- reports
    def save_report(self, session_id: str, overall: float, rank: int, radar: dict, payload: dict) -> None:
        self._exec("DELETE FROM reports WHERE session_id=?", (session_id,))
        self._exec("DELETE FROM score_details WHERE session_id=?", (session_id,))
        self._exec(
            "INSERT INTO reports(session_id, overall, rank, radar, payload) VALUES(?,?,?,?,?)",
            (session_id, overall, rank,
             json.dumps(radar, ensure_ascii=False), json.dumps(payload, ensure_ascii=False)),
        )
        for d in payload["dimensions"]:
            self._exec(
                "INSERT INTO score_details(session_id, dimension, score, evidence) VALUES(?,?,?,?)",
                (session_id, d["key"], d["score"],
                 json.dumps(d.get("evidence", []), ensure_ascii=False)),
            )

    def get_report(self, session_id: str) -> dict[str, Any] | None:
        rows = self._query("SELECT * FROM reports WHERE session_id=?", (session_id,))
        if not rows:
            return None
        r = dict(rows[0])
        r["radar"] = json.loads(r["radar"])
        r["payload"] = json.loads(r["payload"])
        return r

    def close(self) -> None:
        self._conn.close()
