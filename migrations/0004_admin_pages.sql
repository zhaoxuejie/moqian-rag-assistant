-- ============================================================
-- 0004_admin_pages.sql —— 剩余后台页面的持久化基础
--
-- 覆盖：
--   * settlements    知识沉淀待确认队列（人工确认才入库，纪律 8）
--   * gaps           知识缺口状态（缺口本身可由拒答日志派生，本表存状态/覆盖说明）
--   * identity_pending 宿主声明但未开通的工号（治理域「待开通工号」）
-- 说明：问答日志直接读 sessions/messages；配置用已有 app_config；评测用已有
--       eval_cases/eval_runs；不重复建表。
-- ============================================================

-- ---------- 知识沉淀 ----------
create table if not exists settlements (
  id                   bigserial primary key,
  channel              text not null default 'manual'
                         check (channel in ('ticket', 'favorite', 'manual')),
  status               text not null default 'pending'
                         check (status in ('pending', 'confirmed', 'rejected')),
  title                text not null,
  source               text,                    -- 来源描述：工单号 / 会话 / 文档
  model                text,
  batch                text,
  version              text,
  submitted_by         text,
  submitted_at         timestamptz not null default now(),
  loc                  text,                    -- 确认入库后的出处定位
  body                 text not null default '',-- 待确认正文（契约字段 text）
  context              jsonb not null default '[]'::jsonb,  -- [{k,v}] 上下文键值对
  review_log           jsonb not null default '[]'::jsonb,  -- [{at,by,action,note}]
  confirmed_document_id bigint references documents (id) on delete set null,
  confirmed_chunk_id    bigint references chunks (id) on delete set null,
  created_at           timestamptz not null default now(),
  updated_at           timestamptz not null default now()
);

create index if not exists settlements_status_idx on settlements (status, submitted_at desc);
create index if not exists settlements_channel_idx on settlements (channel, submitted_at desc);

-- ---------- 知识缺口状态 ----------
create table if not exists gaps (
  id               bigserial primary key,
  symptom_key      text not null unique,   -- 归一化后的症状文本（去重键）
  symptom          text not null,          -- 展示文本（原始提问）
  model            text,
  count            integer not null default 1,
  first_at         timestamptz not null default now(),
  last_at          timestamptz not null default now(),
  status           text not null default 'open'
                     check (status in ('open', 'covered', 'ignored')),
  covered_by       text,
  note             text,
  related_messages text[] not null default '{}'::text[],
  created_at       timestamptz not null default now(),
  updated_at       timestamptz not null default now()
);

create index if not exists gaps_status_idx on gaps (status, last_at desc);

-- ---------- 待开通工号（声明了但不在册） ----------
create table if not exists identity_pending (
  account       text primary key,
  name          text,
  first_seen_at timestamptz not null default now(),
  last_seen_at  timestamptz not null default now(),
  note          text
);
