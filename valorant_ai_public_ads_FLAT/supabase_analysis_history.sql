create table if not exists public.analysis_history (
    analysis_id text primary key,
    user_id uuid not null references auth.users(id) on delete cascade,
    created_at timestamptz not null default now(),
    file_name text not null default '영상',
    overall_score integer not null default 0,
    tier text not null default '',
    summary text not null default '',
    model_used text not null default '',
    result jsonb not null
);

create index if not exists analysis_history_user_created_idx
    on public.analysis_history (user_id, created_at desc);

alter table public.analysis_history enable row level security;

revoke all on table public.analysis_history from anon, authenticated;
grant all on table public.analysis_history to service_role;

comment on table public.analysis_history is
'VALORANT AI Coach saved analysis results. Original video files are not stored.';
