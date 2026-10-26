-- 知识源「删除」改成「归档」：lifecycle 增加 archived（D-032）
--
-- 语义区分（此前两者完全等价，都是 lifecycle='disabled'）：
--   * disabled（停用）：仍在列表里，但不进检索池，可随时启用；
--   * archived（归档）：默认不出现在列表（需显式 lifecycle=archived），也不进检索池，
--     切片与原件全部保留，可恢复为 current。
--
-- 不物理删除的理由：证据单元是引用目标，删了会让已发出的答案引用失效，
-- 破坏"事实性结论可核验"这条产品纪律（PRD §5.1 / architecture §8 ADR4）。

alter table documents drop constraint if exists documents_lifecycle_chk;
alter table documents
  add constraint documents_lifecycle_chk
  check (lifecycle in ('current', 'superseded', 'disabled', 'archived'));
