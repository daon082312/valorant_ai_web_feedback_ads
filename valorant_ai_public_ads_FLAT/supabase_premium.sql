-- VALORANT AI Coach premium membership entitlements
-- Run this entire file in Supabase Dashboard -> SQL Editor.

create table if not exists public.user_entitlements (
    user_id uuid primary key references auth.users(id) on delete cascade,
    plan text not null default 'free'
        check (plan in ('free', 'premium')),
    premium_until timestamptz null,
    updated_at timestamptz not null default now()
);

alter table public.user_entitlements enable row level security;

revoke all on table public.user_entitlements
from anon, authenticated;

grant all on table public.user_entitlements
to service_role;

-- Example: make one user premium indefinitely by email.
-- Replace the email address before running this example manually.
--
-- insert into public.user_entitlements (user_id, plan, premium_until, updated_at)
-- select id, 'premium', null, now()
-- from auth.users
-- where email = 'user@example.com'
-- on conflict (user_id) do update
-- set plan = excluded.plan,
--     premium_until = excluded.premium_until,
--     updated_at = now();
--
-- To return a user to the free plan:
-- update public.user_entitlements
-- set plan = 'free', premium_until = null, updated_at = now()
-- where user_id = (select id from auth.users where email = 'user@example.com');
