-- S4：转人工记录（P0 只做热线提示，但要留下"谁在什么上下文下转过人工"的痕迹）
-- 依赖 0001_init.sql 的 sessions / messages。

create table if not exists handoffs (
  id          bigserial primary key,
  session_id  bigint not null references sessions (id) on delete cascade,
  message_id  bigint references messages (id) on delete set null,
  context     jsonb not null default '{}'::jsonb,  -- 触发时的会话上下文快照（不编造条数）
  created_at  timestamptz not null default now()
);

create index if not exists handoffs_session_idx on handoffs (session_id, created_at);

-- 反馈目录：messages 已有 resolved / resolved_at / feedback 列，S4 只补两件事：
-- 1) 契约里的可选补充说明 note；2) 防止写入契约之外的 feedback 值
alter table messages add column if not exists feedback_note text;

alter table messages drop constraint if exists messages_feedback_chk;
alter table messages
  add constraint messages_feedback_chk
  check (feedback is null or feedback in ('resolved', 'unresolved', 'saved'));
