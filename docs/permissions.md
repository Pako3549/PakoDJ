# Permission model (target design)

Statuses: **[implemented]** = lives in code today; **[planned]** = decided
here, to be wired when the webapp lands. Prefer not to invent more statuses.

## Why elementary permissions

A single role integer can answer "is this an admin?" but not "this person
must be able to add songs without touching the queue order, and only in the
servers where they are". Granular work needs elementary permissions:

- composable: service users, mods, viewers all become combinations
- requestable: a user can knock asking for exactly the capabilities they need
- auditable: each grant is one field change, no "guess the role"

The current three roles remain as **presets** of this model, not as the model.

## Elementary permissions

| Permission | Meaning (server-scoped unless noted) |
| --- | --- |
| `status.view` | See live status and now-playing of a guild |
| `queue.view` | Read the queue and history of a guild |
| `queue.enqueue` | Add tracks to a guild queue (from search or URL) |
| `queue.manage` | Reorder, remove single items, clear — i.e. mutate an existing queue |
| `playback.control` | Skip / pause / resume / stop live playback |
| `users.manage` | List users, change roles and permissions [admin-level] |
| `requests.resolve` | Grant/deny access requests [admin-level] |
| `guilds.all` | Scope override: act on ALL bot guilds. Without it, every permission above is confined to the guilds the user shares (the `guilds` snapshot from login) |

Notes:
- `guilds.all` is the "all servers" vs "only my servers" switch requested for
  viewing status — it gates every other permission's reach, read and write.
- Admin-level permissions are invisible to normal users (the users panel is
  not rendered for them).
- The seeded user (`seed = true`) holds **every** permission plus `guilds.all`,
  and only `DISCORD_ADMIN_IDS` can produce that combination.

## Presets (current roles mapped)

| Role | Permissions |
| --- | --- |
| `nobody` | none |
| `viewer` | `status.view`, `queue.view` |
| `admin` | all elementary permissions + `guilds.all` |

Seeded accounts are `admin` by construction and flag-protected; granted users
may hold any subset (a moderator with `queue.enqueue` + `queue.manage` +
`playback.control`, shared-guilds only, is a legal state).

## Storage [planned]

- `users.permissions TEXT NOT NULL DEFAULT ''` — JSON array of permission
  names; empty array means "derive from role", non-empty means explicit set
  (explicit grants never silently reset at login).
- Effective check order: `seed` => all; explicit list => list; else role
  preset. Storage stays in `users`, so no extra table or session changes are
  needed; `_require_viewer`/`_require_admin` dissolve into a
  `_require_permission(perm)` dependency factory.

## Access requests (notification flow) [implemented backend / planned UX]

Already live: `GET /api/access/guilds` (shared-guilds snapshot),
`POST /api/access-requests` (one pending per user+guild, 200-char message),
admin `GET` list + `PATCH {granted|denied}`.

- [planned] The request body may carry `wanted: ["queue.enqueue", ...]` so the
  panel can prefill the grant form — validation: only permission names the
  requester does not already effectively hold, and never admin-level ones.
- [planned] Panel notifications: the management page subscribes to an SSE
  channel (EventSource, per `docs/style.md` tech stack) or — as the minimal
  first version — polls `GET /api/access-requests` while the tab is open and
  shows a badge for pending items. A `GET /api/access-requests/count` cheap
  endpoint can back the badge without pulling the full list.
- Granting = choose permissions (or a preset) and then
  `PATCH /api/users/{id}`; both admin actions, same audit story.

## Panel UX contract [planned, for docs/style.md]

- Users page: one card per user with effective permissions shown as grouped
  chips (view / queue / playback / admin-level), role preset as a quick
  "apply preset" button, custom toggles for single permissions.
- Seeded users render a lock badge ("env-owned") and no editable controls.
- Request notifications: pending badge in the header; clicking opens the
  request list with grant/deny and the prefilled permission set from the
  request.
