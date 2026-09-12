-- VALORANT AI Coach: persistent daily usage counter
-- Run this once in Supabase Dashboard -> SQL Editor.

create table if not exists public.daily_usage (
    user_key text not null,
    usage_date date not null,
    successful_analyses integer not null default 0,
    updated_at timestamptz not null default now(),
    primary key (user_key, usage_date),
    constraint daily_usage_count_nonnegative
        check (successful_analyses >= 0)
);

alter table public.daily_usage enable row level security;

revoke all on table public.daily_usage from anon, authenticated;
grant all on table public.daily_usage to service_role;

create or replace function public.consume_daily_usage(
    p_user_key text,
    p_usage_date date,
    p_daily_limit integer
)
returns integer
language plpgsql
security invoker
set search_path = public
as $$
declare
    v_count integer;
begin
    if p_daily_limit < 1 then
        raise exception 'p_daily_limit must be at least 1';
    end if;

    insert into public.daily_usage (
        user_key,
        usage_date,
        successful_analyses,
        updated_at
    )
    values (
        p_user_key,
        p_usage_date,
        0,
        now()
    )
    on conflict (user_key, usage_date) do nothing;

    select successful_analyses
    into v_count
    from public.daily_usage
    where user_key = p_user_key
      and usage_date = p_usage_date
    for update;

    if v_count >= p_daily_limit then
        return -1;
    end if;

    update public.daily_usage
    set successful_analyses = successful_analyses + 1,
        updated_at = now()
    where user_key = p_user_key
      and usage_date = p_usage_date
    returning successful_analyses
    into v_count;

    return v_count;
end;
$$;

revoke all on function public.consume_daily_usage(text, date, integer)
from public, anon, authenticated;

grant execute on function public.consume_daily_usage(text, date, integer)
to service_role;

create index if not exists daily_usage_date_idx
on public.daily_usage (usage_date);
