-- AdvFlow persistent account store.
-- Run this SQL in Supabase Dashboard -> SQL Editor -> New query.
-- Authentication stays server-side in the Streamlit app; do not expose SUPABASE_SECRET_KEY in browser code.
create table if not exists public.advflow_users (
    username text primary key
        check (username ~ '^[A-Za-z0-9_.-]{3,32}$'),
    password_salt text not null,
    password_hash text not null,
    role text not null default 'user'
        check (role in ('admin', 'user')),
    active boolean not null default true,
    must_change_password boolean not null default false,
    failed_attempts integer not null default 0
        check (failed_attempts >= 0),
    locked_until timestamptz,
    created_at timestamptz not null default now()
);

alter table public.advflow_users enable row level security;

-- The app uses only a server-side Supabase secret key. Regular visitors cannot access this table.
revoke all on table public.advflow_users from anon, authenticated;
grant all privileges on table public.advflow_users to service_role;
