-- VALORANT AI Coach one-time donations via Toss Payments
-- Run this entire file in Supabase Dashboard -> SQL Editor once before enabling donations.

create table if not exists public.donation_orders (
    order_id text primary key,
    customer_key text not null,
    amount integer not null check (amount >= 1000 and amount <= 500000),
    status text not null default 'pending'
        check (status in ('pending', 'paid', 'failed')),
    payment_key text null unique,
    method text null,
    failure_code text null,
    failure_message text null,
    paid_at timestamptz null,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create index if not exists donation_orders_created_idx
on public.donation_orders (created_at desc);

create index if not exists donation_orders_status_created_idx
on public.donation_orders (status, created_at desc);

alter table public.donation_orders enable row level security;
revoke all on table public.donation_orders from anon, authenticated;
grant all on table public.donation_orders to service_role;
