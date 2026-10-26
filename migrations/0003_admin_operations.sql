-- ============================================================
-- 0003_admin_operations.sql —— 后台治理操作（D-011）
--
-- 语义（2026-10-19 拍板）：
--   * 知识源「删除」= 软删除：documents.lifecycle='disabled'，切片保留、检索剔除、可重新启用；
--   * 版本处置：旧版 → lifecycle='superseded'（仍参与检索，按批次/版本展示）；
--                 保留但禁用 → lifecycle='disabled'；
--   * 任务取消：新增 jobs.status='cancelled'；运行中任务置 cancel_requested，
--               worker 在阶段边界退出，不中断进行中的外部调用；
--   * 审计：后台写操作留痕（谁、何时、对哪个对象做了什么），供治理复盘。
--
-- 兼容：0001 的 documents.status 仍是"解析状态"（uploaded/parsing/parsed/failed），
--       生命周期改由 lifecycle 独立表达；历史 status='disabled' 的行回填为 disabled。
-- ============================================================

-- ---------- 知识源生命周期 ----------
alter table documents add column if not exists lifecycle text not null default 'current';

alter table documents drop constraint if exists documents_lifecycle_chk;
alter table documents
  add constraint documents_lifecycle_chk
  check (lifecycle in ('current', 'superseded', 'disabled'));

update documents set lifecycle = 'disabled'
 where status = 'disabled' and lifecycle <> 'disabled';

create index if not exists documents_lifecycle_idx on documents (lifecycle);

-- ---------- 任务取消 ----------
alter table jobs add column if not exists cancel_requested boolean not null default false;

alter table jobs drop constraint if exists jobs_status_chk;
alter table jobs
  add constraint jobs_status_chk
  check (status in ('queued', 'running', 'done', 'failed', 'cancelled'));

-- ---------- 后台写操作审计 ----------
create table if not exists admin_audit_log (
  id           bigserial primary key,
  actor_account text not null,
  action       text not null,              -- document.patch | document.enable | chunk.patch | job.cancel | document.reparse
  target_type  text not null,              -- document | chunk | job
  target_id    text not null,
  detail       jsonb not null default '{}'::jsonb,
  created_at   timestamptz not null default now()
);

create index if not exists admin_audit_target_idx
  on admin_audit_log (target_type, target_id, created_at desc);
create index if not exists admin_audit_created_idx on admin_audit_log (created_at desc);
