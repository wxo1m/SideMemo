-- SideMemo 동기화용 Supabase 스키마
-- Supabase 대시보드 > SQL Editor 에 붙여넣고 [Run] 하세요.

create table if not exists public.sidememo_notes (
    user_id    uuid        not null default auth.uid() references auth.users (id) on delete cascade,
    note_id    text        not null,
    position   integer     not null default 0,
    payload    jsonb       not null default '{}'::jsonb,
    deleted    boolean     not null default false,
    updated_at timestamptz not null default now(),
    primary key (user_id, note_id)
);

create table if not exists public.sidememo_settings (
    user_id    uuid        primary key default auth.uid() references auth.users (id) on delete cascade,
    payload    jsonb       not null default '{}'::jsonb,
    updated_at timestamptz not null default now()
);

create index if not exists sidememo_notes_updated_idx
    on public.sidememo_notes (user_id, updated_at desc);

-- updated_at 자동 갱신
create or replace function public.sidememo_touch_updated_at()
returns trigger
language plpgsql
as $$
begin
    new.updated_at = now();
    return new;
end;
$$;

drop trigger if exists sidememo_notes_touch on public.sidememo_notes;
create trigger sidememo_notes_touch
    before insert or update on public.sidememo_notes
    for each row execute function public.sidememo_touch_updated_at();

drop trigger if exists sidememo_settings_touch on public.sidememo_settings;
create trigger sidememo_settings_touch
    before insert or update on public.sidememo_settings
    for each row execute function public.sidememo_touch_updated_at();

-- 행 수준 보안: 본인 데이터만 읽고 쓸 수 있습니다.
alter table public.sidememo_notes    enable row level security;
alter table public.sidememo_settings enable row level security;

drop policy if exists sidememo_notes_owner on public.sidememo_notes;
create policy sidememo_notes_owner on public.sidememo_notes
    for all
    using (auth.uid() = user_id)
    with check (auth.uid() = user_id);

drop policy if exists sidememo_settings_owner on public.sidememo_settings;
create policy sidememo_settings_owner on public.sidememo_settings
    for all
    using (auth.uid() = user_id)
    with check (auth.uid() = user_id);

-- 웹 앱이 실시간으로 변경을 받아볼 수 있게 합니다.
alter publication supabase_realtime add table public.sidememo_notes;
alter publication supabase_realtime add table public.sidememo_settings;
