-- D-014：评测用例入库需要保留仓库用例的稳定标识与上下文（批次/型号），
-- 否则入库后跑批会丢掉上下文，结果和 YAML 版本不可比。
alter table eval_cases
    add column if not exists source_id text,
    add column if not exists context jsonb;

create unique index if not exists eval_cases_source_id_key
    on eval_cases(source_id)
    where source_id is not null;
