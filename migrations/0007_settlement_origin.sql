-- ============================================================
-- 0007_settlement_origin.sql —— 知识沉淀候选的生产端幂等键（D-020）
--
-- FR-K5 双进水管：收藏（channel=favorite）与工单完成（channel=ticket）都只产
-- 「待确认」候选，仍由管理员确认才入库（纪律 8）。
--
-- origin_ref 记录候选的来源引用：
--   * 收藏：msg-<消息 id>（一条回答最多一条收藏候选）
--   * 工单：ticket:<工单号>（一张工单最多一条待确认候选）
--   * 手工：NULL（不参与去重）
--
-- 唯一索引刻意排除 rejected：驳回后允许管理员重新提交候选（审核历史保留），
-- 但 pending / confirmed 不能被重复插入。
-- ============================================================

alter table settlements add column if not exists origin_ref text;

comment on column settlements.origin_ref is
  '候选来源引用：收藏=msg-<id>，工单=ticket:<工单号>，手工=NULL；用于生产端幂等';

create unique index if not exists settlements_origin_uq
  on settlements (channel, origin_ref)
  where origin_ref is not null and status <> 'rejected';
