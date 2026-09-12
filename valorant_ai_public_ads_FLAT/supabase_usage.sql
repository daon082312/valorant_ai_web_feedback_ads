-- VALORANT AI Coach
-- Account + network (IP hash) daily usage limits.
-- Run this entire file in Supabase Dashboard -> SQL Editor.
-- It uses daily_usage_v2 so it can coexist with the older daily_usage table.

create table if not exists public.daily_usage_v2 (
    subject_type text not null
        check (subject_type in ('account', 'ip')),
    subject_key text not null,
    usage_date date not null,
    successful_analyses integer not null default 0,
    updated_at timestamptz not null default now(),
    primary key (subject_type, subject_key, usage_date),
    constraint daily_usage_v2_count_nonnegative
        check (successful_analyses >= 0)
);

alter table public.daily_usage_v2 enable row level security;

revoke all on table public.daily_usage_v2
from anon, authenticated;

grant all on table public.daily_usage_v2
to service_role;

create index if not exists daily_usage_v2_date_idx
on public.daily_usage_v2 (usage_date);

create or replace function public.consume_dual_daily_usage(
    p_account_key text,
    p_ip_key text,
    p_usage_date date,
    p_account_limit integer,
    p_ip_limit integer
)
returns jsonb
language plpgsql
security invoker
set search_path = public
as $$
declare
    v_account_count integer;
    v_ip_count integer;
begin
    if p_account_limit < 1 or p_ip_limit < 1 then
        raise exception 'daily limits must be at least 1';
    end if;

    insert into public.daily_usage_v2 (
        subject_type,
        subject_key,
        usage_date,
        successful_analyses,
        updated_at
    )
    values ('account', p_account_key, p_usage_date, 0, now())
    on conflict (subject_type, subject_key, usage_date) do nothing;

    insert into public.daily_usage_v2 (
        subject_type,
        subject_key,
        usage_date,
        successful_analyses,
        updated_at
    )
    values ('ip', p_ip_key, p_usage_date, 0, now())
    on conflict (subject_type, subject_key, usage_date) do nothing;

    select successful_analyses
    into v_account_count
    from public.daily_usage_v2
    where subject_type = 'account'
      and subject_key = p_account_key
      and usage_date = p_usage_date
    for update;

    select successful_analyses
    into v_ip_count
    from public.daily_usage_v2
    where subject_type = 'ip'
      and subject_key = p_ip_key
      and usage_date = p_usage_date
    for update;

    if v_account_count >= p_account_limit then
        return jsonb_build_object(
            'allowed', false,
            'reason', 'account_limit',
            'account_used', v_account_count,
            'ip_used', v_ip_count
        );
    end if;

    if v_ip_count >= p_ip_limit then
        return jsonb_build_object(
            'allowed', false,
            'reason', 'ip_limit',
            'account_used', v_account_count,
            'ip_used', v_ip_count
        );
    end if;

    update public.daily_usage_v2
    set successful_analyses = successful_analyses + 1,
        updated_at = now()
    where subject_type = 'account'
      and subject_key = p_account_key
      and usage_date = p_usage_date
    returning successful_analyses
    into v_account_count;

    update public.daily_usage_v2
    set successful_analyses = successful_analyses + 1,
        updated_at = now()
    where subject_type = 'ip'
      and subject_key = p_ip_key
      and usage_date = p_usage_date
    returning successful_analyses
    into v_ip_count;

    return jsonb_build_object(
        'allowed', true,
        'reason', null,
        'account_used', v_account_count,
        'ip_used', v_ip_count
    );
end;
$$;

revoke all on function public.consume_dual_daily_usage(
    text,
    text,
    date,
    integer,
    integer
)
from public, anon, authenticated;

grant execute on function public.consume_dual_daily_usage(
    text,
    text,
    date,
    integer,
    integer
)
to service_role;
