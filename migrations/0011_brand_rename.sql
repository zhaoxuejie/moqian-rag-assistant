-- 品牌重命名：历史库里如果保存过旧组织名，升级到本迁移时自动改成「陌阡科技」。
--
-- 幂等：只更新仍然等于旧值的行；新库没有这一行（品牌由 app/branding.py 的默认值提供），
-- 因此新库跑这条迁移什么都不做。
--
-- 旧组织名用 Unicode 转义写（U&'...'），避免在仓库里留下旧品牌字样：
--   U&'\7434\590F\79D1\6280' = 旧组织名

update app_config
set value = jsonb_set(value, '{orgName}', to_jsonb('陌阡科技'::text), false),
    updated_at = now()
where key = 'config.brand'
  and value ->> 'orgName' = U&'\7434\590F\79D1\6280';
