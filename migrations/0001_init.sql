-- ============================================================
-- 0001_init.sql —— M0 初始表结构（architecture §6 数据模型）
-- 说明：
--   * 账号即工号：`account` 全平台唯一、不可改、不可复用；保留账号 __unidentified__ 不可登录。
--   * 切片是知识库原子：chunks 携带 evidence_id 与出处元数据 meta_json。
--   * 会话记录身份解析结果；engineer_id 为空 = 未识别，聚合时归入 __unidentified__。
--   * admin_preview = 管理员自测会话，统计查询必须排除（channel 过滤）。
-- ============================================================

create extension if not exists vector;
create extension if not exists pg_trgm;

-- ---------- 账号 ----------
create table if not exists users (
  id             bigserial primary key,
  account        text        not null unique,           -- 工号（E1001…），不可改不可复用
  name           text        not null,
  role           text        not null default 'user',   -- admin | user（两级，不做 RBAC）
  status         text        not null default 'active', -- active | disabled
  credential     text        not null default 'local',  -- domain | local
  password_hash  text,                                  -- credential=domain 时为空
  group_name     text,                                  -- 小组（售后/维保）
  created_at     timestamptz not null default now(),
  updated_at     timestamptz not null default now(),
  constraint users_role_chk check (role in ('admin', 'user')),
  constraint users_status_chk check (status in ('active', 'disabled')),
  constraint users_credential_chk check (credential in ('domain', 'local')),
  -- 保留账号 __unidentified__ 是归属哨兵：不可登录，所以允许无密码
  constraint users_pwd_chk check (
    credential = 'domain' or password_hash is not null or account = '__unidentified__'
  )
);

-- 未识别哨兵账号：不可登录、不可删除，仅用于归属聚合
insert into users (account, name, role, status, credential, password_hash)
values ('__unidentified__', '未识别', 'user', 'disabled', 'local', null)
on conflict (account) do nothing;

-- ---------- 知识源 ----------
create table if not exists documents (
  id              bigserial primary key,
  source_type     text not null,           -- pdf | word | excel | ticket | drawing | video | audio
  title           text not null,
  doc_code        text,                    -- 文件编号（如 3CA0141-12）
  device_model    text,                    -- 型号/产品系列（如 BK / DZS800）
  batch           text,                    -- 批次（首批资料无，留空）
  manual_version  text,                    -- 版本（Ver.3）
  effective_date  date,
  file_path       text,                    -- data/original-docs 下的相对路径（不入库原件内容）
  file_sha256     text,
  status          text not null default 'parsed',   -- uploaded | parsing | parsed | failed | disabled
  meta_json       jsonb not null default '{}'::jsonb,
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now(),
  constraint documents_source_type_chk check (
    source_type in ('pdf', 'word', 'excel', 'ticket', 'drawing', 'video', 'audio')
  ),
  constraint documents_status_chk check (
    status in ('uploaded', 'parsing', 'parsed', 'failed', 'disabled')
  )
);

create index if not exists documents_model_idx on documents (device_model);
create index if not exists documents_status_idx on documents (status);

-- ---------- 切片（知识库原子 / 证据单元） ----------
create table if not exists chunks (
  id           bigserial primary key,
  document_id  bigint not null references documents (id) on delete cascade,
  evidence_id  text not null,             -- 对外引用 ID（如 M642 / T1187）
  content      text not null,
  section_no   text,                      -- 章节号（机制字段，行业词汇由 app 决定如何展示）
  page_from    int,
  page_to      int,
  batch        text,
  manual_version text,
  disabled     boolean not null default false,
  meta_json    jsonb not null default '{}'::jsonb,
  embedding    vector(1024),               -- D10：BGE-M3 = 1024 维
  bm25_tokens  text,                       -- jieba 预分词结果（空格分隔）
  created_at   timestamptz not null default now()
);

create unique index if not exists chunks_evidence_id_uidx on chunks (evidence_id);
create index if not exists chunks_document_idx on chunks (document_id);
create index if not exists chunks_active_idx on chunks (document_id) where disabled = false;
create index if not exists chunks_embedding_idx on chunks using hnsw (embedding vector_cosine_ops);

-- ---------- 任务（数据库任务表 + worker 心跳） ----------
create table if not exists jobs (
  id           bigserial primary key,
  type         text not null,              -- parse | reindex
  payload      jsonb not null default '{}'::jsonb,
  status       text not null default 'queued',  -- queued | running | done | failed
  error        text,
  attempts     int not null default 0,
  worker_id    text,
  heartbeat_at timestamptz,
  created_at   timestamptz not null default now(),
  started_at   timestamptz,
  finished_at  timestamptz,
  constraint jobs_status_chk check (status in ('queued', 'running', 'done', 'failed'))
);

create index if not exists jobs_status_idx on jobs (status, created_at);

-- ---------- 会话（身份解析结果） ----------
create table if not exists sessions (
  id                  bigserial primary key,
  auth_source         text not null,        -- gateway_sso | host_injected | password_login | anonymous
  claimed_engineer_id text,                 -- 宿主声明的工号（可能未开通）
  engineer_id         bigint references users (id),  -- 在册校验通过才写；空 = 未识别
  channel             text not null default 'widget', -- widget | admin_preview
  host_context        jsonb not null default '{}'::jsonb,  -- 工单号/型号/批次
  created_at          timestamptz not null default now(),
  constraint sessions_auth_source_chk check (
    auth_source in ('gateway_sso', 'host_injected', 'password_login', 'anonymous')
  ),
  constraint sessions_channel_chk check (channel in ('widget', 'admin_preview'))
);

create index if not exists sessions_created_idx on sessions (created_at);
create index if not exists sessions_engineer_idx on sessions (engineer_id);

-- ---------- 问答日志 ----------
create table if not exists messages (
  id                   bigserial primary key,
  session_id           bigint not null references sessions (id) on delete cascade,
  role                 text not null,       -- user | assistant
  content              text not null default '',
  structured_query     jsonb,               -- ① 结构化改写结果（未知字段为空）
  evidence_ids         text[] not null default '{}',
  citations            jsonb not null default '[]'::jsonb,
  refused              boolean not null default false,
  gate_reason          text,                -- gap | safe | citation_stripped | null
  question_at          timestamptz,         -- 首次提问时间（定位时长起点）
  resolved             boolean,
  resolved_at          timestamptz,         -- 已解决标记时间（定位时长终点）
  feedback             text,                -- resolved | unresolved | saved
  created_at           timestamptz not null default now(),
  constraint messages_role_chk check (role in ('user', 'assistant'))
);

create index if not exists messages_session_idx on messages (session_id, created_at);
create index if not exists messages_refused_idx on messages (refused);
create index if not exists messages_resolved_idx on messages (resolved) where resolved is not null;

-- ---------- 评测 ----------
create table if not exists eval_cases (
  id                bigserial primary key,
  question          text not null,
  expected_points   jsonb not null default '[]'::jsonb,
  fault_category    text,                   -- 报警码 / 异响 / 温升 / 精度偏差
  batch_pair_id     text,                   -- 跨批次（此处为跨型号/版本）用例对
  baseline_answer   text,                   -- 人工基线列（可为空）
  expected_refuse   boolean not null default false,
  expected_safety   boolean not null default false,
  created_at        timestamptz not null default now()
);

create table if not exists eval_runs (
  id              bigserial primary key,
  started_at      timestamptz not null default now(),
  finished_at     timestamptz,
  model_snapshot  jsonb not null default '{}'::jsonb,
  metrics         jsonb not null default '{}'::jsonb,
  status          text not null default 'running',
  constraint eval_runs_status_chk check (status in ('running', 'done', 'failed'))
);

-- ---------- 后台配置（系统配置页持久化；原型用 sessionStorage） ----------
create table if not exists app_config (
  key        text primary key,
  value      jsonb not null,
  updated_at timestamptz not null default now()
);
