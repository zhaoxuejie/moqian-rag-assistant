-- 登录验证码挑战：一次性、带过期时间；只存答案哈希，不存明文。
create table if not exists captcha_challenges (
    token text primary key,
    answer_hash text not null,
    expires_at timestamptz not null,
    used boolean not null default false,
    created_at timestamptz not null default now()
);

create index if not exists captcha_challenges_expires_idx
    on captcha_challenges(expires_at);
