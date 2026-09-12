create table if not exists public.analysis_history (
    analysis_id text not null,
    user_id uuid not null references auth.users(id) on delete cascade,
    created_at timestamptz not null default now(),
    file_name text not null default '영상',
    overall_score integer not null default 0,
    tier text not null default '',
    summary text not null default '',
    model_used text not null default '',
    result jsonb not null
);

-- This unique index also upgrades older installations where analysis_id alone
-- was the primary key. PostgREST can use it for ON CONFLICT(user_id, analysis_id).
create unique index if not exists analysis_history_user_analysis_uidx
    on public.analysis_history (user_id, analysis_id);

create index if not exists analysis_history_user_created_idx
    on public.analysis_history (user_id, created_at desc);

alter table public.analysis_history enable row level security;

grant usage on schema public to authenticated;
grant select, insert, update, delete on table public.analysis_history to authenticated;

-- Safe to run repeatedly when upgrading an existing project.
drop policy if exists "analysis_history_select_own" on public.analysis_history;
drop policy if exists "analysis_history_insert_own" on public.analysis_history;
drop policy if exists "analysis_history_update_own" on public.analysis_history;
drop policy if exists "analysis_history_delete_own" on public.analysis_history;

create policy "analysis_history_select_own"
on public.analysis_history
for select
to authenticated
using (auth.uid() = user_id);

create policy "analysis_history_insert_own"
on public.analysis_history
for insert
to authenticated
with check (auth.uid() = user_id);

create policy "analysis_history_update_own"
on public.analysis_history
for update
to authenticated
using (auth.uid() = user_id)
with check (auth.uid() = user_id);

create policy "analysis_history_delete_own"
on public.analysis_history
for delete
to authenticated
using (auth.uid() = user_id);

-- Keep service-role access for server-side maintenance/admin use.
grant all on table public.analysis_history to service_role;

comment on table public.analysis_history is
'VALORANT AI Coach saved analysis results. Original video files are not stored.';
