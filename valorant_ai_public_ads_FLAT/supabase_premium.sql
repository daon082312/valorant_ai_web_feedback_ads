-- VALORANT AI Coach Premium + Toss Payments recurring billing
-- Run this entire file in Supabase Dashboard -> SQL Editor.

create table if not exists public.user_entitlements (
    user_id uuid primary key references auth.users(id) on delete cascade,
    plan text not null default 'free'
        check (plan in ('free', 'premium')),
    premium_until timestamptz null,
    updated_at timestamptz not null default now()
);

alter table public.user_entitlements enable row level security;
revoke all on table public.user_entitlements from anon, authenticated;
grant all on table public.user_entitlements to service_role;

create table if not exists public.premium_subscriptions (
    user_id uuid primary key references auth.users(id) on delete cascade,
    email text not null default '',
    customer_key text not null unique,
    billing_key text null,
    status text not null default 'inactive'
        check (status in ('inactive', 'incomplete', 'active', 'past_due', 'canceled')),
    amount integer not null default 990 check (amount > 0),
    current_period_end timestamptz null,
    next_billing_at timestamptz null,
    cancel_at_period_end boolean not null default false,
    failure_count integer not null default 0 check (failure_count >= 0),
    consent_at timestamptz null,
    terms_version text null,
    last_payment_key text null,
    last_order_id text null,
    last_error text null,
    last_attempt_at timestamptz null,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create index if not exists premium_subscriptions_due_idx
on public.premium_subscriptions (next_billing_at)
where cancel_at_period_end = false;

alter table public.premium_subscriptions enable row level security;
revoke all on table public.premium_subscriptions from anon, authenticated;
grant all on table public.premium_subscriptions to service_role;

create table if not exists public.premium_payments (
    order_id text primary key,
    user_id uuid not null references auth.users(id) on delete cascade,
    amount integer not null check (amount > 0),
    status text not null check (status in ('paid', 'failed')),
    payment_key text null,
    failure_code text null,
    failure_message text null,
    created_at timestamptz not null default now()
);

create index if not exists premium_payments_user_created_idx
on public.premium_payments (user_id, created_at desc);

alter table public.premium_payments enable row level security;
revoke all on table public.premium_payments from anon, authenticated;
grant all on table public.premium_payments to service_role;

-- Manual emergency override example (optional):
-- insert into public.user_entitlements (user_id, plan, premium_until, updated_at)
-- select id, 'premium', now() + interval '30 days', now()
-- from auth.users
-- where email = 'user@example.com'
-- on conflict (user_id) do update
-- set plan = excluded.plan,
--     premium_until = excluded.premium_until,
--     updated_at = now();
