# Schedule Memory

Schedules and routines are durable structured memories, stored in:

`data/profiles/default/schedules.json`

This prevents routines from being buried only in raw chat history.

## Tools

- `remember_schedule(title, schedule_date, items, schedule_id?, notes?, file_path?)`
  - Store or replace a schedule, routine, checklist, or plan.
  - Use whenever Travis builds or changes a daily schedule, medication routine,
    pet-care routine, project checklist, or recurring plan.
- `get_schedule(identifier?)`
  - Retrieve by schedule ID or ISO date (`2026-05-08`).
  - Empty identifier means today's schedule.
- `list_schedules(include_archived?)`
  - Show all active durable schedules.

## Context Injection

The most recent active schedules are injected into Andrew's normal memory
context under:

`## Schedules / Plans (durable memory)`

That means Andrew should check this context, or call `get_schedule`, before
saying he does not know Travis's schedule.

## Reconstructed Schedule

The missing May 8 schedule was reconstructed from chat history and stored as:

- schedule ID: `travis-morning-schedule-2026-05-08`
- date: `2026-05-08`
- file: `C:\Users\aztre\Desktop\agent\andrew's projects\schedules\Travis_Morning_Schedule_May_8_2026.txt`

It includes morning medication, Chance care, breakfast, routine data tracking,
prediction engine work, finance tracking, and medical check-ins.
