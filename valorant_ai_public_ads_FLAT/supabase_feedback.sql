-- VALORANT AI Coach feedback persistence + calibration
-- Run this entire file in Supabase Dashboard -> SQL Editor.
-- The app uses the service-role/secret key only; browsers cannot read this table.

create table if not exists public.analysis_feedback (
    feedback_id text primary key,
    created_at timestamptz not null default now(),
    user_id uuid null references auth.users(id) on delete set null,
    analysis_id text not null,
    target_type text not null
        check (target_type in ('overall', 'event')),
    event_index integer null check (event_index is null or event_index >= 0),
    event_timestamp text null,
    event_category text null,
    rating text not null
        check (rating in ('helpful', 'partial', 'not_helpful', 'up', 'down')),
    categories jsonb not null default '[]'::jsonb,
    comment text not null default ''
);

create index if not exists analysis_feedback_created_idx
on public.analysis_feedback (created_at desc);

create index if not exists analysis_feedback_category_idx
on public.analysis_feedback (event_category, created_at desc)
where target_type = 'event';

alter table public.analysis_feedback enable row level security;
revoke all on table public.analysis_feedback from anon, authenticated;
grant all on table public.analysis_feedback to service_role;

-- Optional verification after running:
-- select target_type, event_category, rating, count(*)
-- from public.analysis_feedback
-- group by target_type, event_category, rating
-- order by target_type, event_category, rating;
