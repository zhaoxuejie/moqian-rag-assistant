-- 线上演示站：入口演示码 + 日用量计数（成本闸）。
-- 演示码只做分流与归因；真正的成本上限由 demo_usage 的日额度保证。
create table if not exists demo_access_codes (
    code       text primary key,
    label      text not null default '',
    channel    text not null default '',
    enabled    boolean not null default true,
    expires_at timestamptz,
    created_at timestamptz not null default now()
);

-- 按天 + 维度聚合的用量：scope = global | ip | code | session
-- 并发安全靠主键 + 单条 upsert（见 app/demo_gate.py）。
create table if not exists demo_usage (
    day        date not null,
    scope      text not null check (scope in ('global', 'ip', 'code', 'session')),
    subject    text not null,
    answered   integer not null default 0,
    blocked    integer not null default 0,
    tokens_in  bigint not null default 0,
    tokens_out bigint not null default 0,
    updated_at timestamptz not null default now(),
    primary key (day, scope, subject)
);

-- 会话记住入口码：按码统计与按码限额要用
alter table sessions add column if not exists demo_code text;

create index if not exists sessions_demo_code_idx on sessions (demo_code);
