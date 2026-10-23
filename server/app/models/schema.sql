-- M1 表结构（对齐 docs/05§3；training_records 留待 M3 成长曲线）
CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY,
  anon_token TEXT UNIQUE NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS sessions (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  question_id TEXT NOT NULL,
  config TEXT NOT NULL,          -- 难度/时长/人格组合 快照 JSON
  status TEXT NOT NULL,          -- waiting / running / finished / aborted
  phase TEXT NOT NULL,
  started_at TEXT,
  ended_at TEXT
);

CREATE TABLE IF NOT EXISTS session_members (
  session_id TEXT NOT NULL,
  member_id TEXT NOT NULL,
  role TEXT NOT NULL,            -- user / candidate / interviewer
  display_name TEXT NOT NULL,
  persona TEXT,                  -- 人格卡快照 JSON（按难度缩放后）
  PRIMARY KEY (session_id, member_id)
);

CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL,
  seq INTEGER NOT NULL,
  speaker_id TEXT,
  kind TEXT NOT NULL,            -- speak / interviewer / phase / system / summary
  text TEXT,
  ts TEXT NOT NULL DEFAULT (datetime('now')),
  meta TEXT,
  UNIQUE (session_id, seq)
);

CREATE TABLE IF NOT EXISTS director_logs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL,
  turn INTEGER NOT NULL,
  decision TEXT NOT NULL,
  ts TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS reports (
  session_id TEXT PRIMARY KEY,
  overall REAL NOT NULL,
  rank INTEGER,
  radar TEXT NOT NULL,
  payload TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS score_details (
  session_id TEXT NOT NULL,
  dimension TEXT NOT NULL,
  score REAL NOT NULL,
  evidence TEXT,
  PRIMARY KEY (session_id, dimension)
);
