# Team roles and theme switch: design

Status: approved by the owner on 2026-11-22. This is a small change on top of the web interface (`2026-11-21-web-interface-design.md`).

## Intent

The owner now uses Jerome in production and wants to be its **super admin**. Today every signed-in member, and every API key, can add people, reset passwords and deactivate anyone. Only super admins should manage the team; members should just do the work (projects, proofreading, review, exports).

The owner also wants a **light mode** they can choose. Jerome already has light and dark themes, but it only follows the operating system, so someone whose OS is dark can't pick light.

## Goals

- Two roles: `super_admin` and `member`.
- Team management is for super admins only, and the server enforces it.
- The team can never lose its last active super admin.
- A Theme choice of System, Light or Dark, remembered per browser, that applies everywhere including the login page.

## Non-goals

- More roles, such as an admin between the two.
- Per-project permissions.
- Email invitations.
- An audit log.
- Restricting what members can do with projects. Every member keeps full access to every project.

## Approaches

There was one sensible way to build each part, so the owner was shown one design and approved it.

**Roles.** A `role` column on `users`, enforced by a FastAPI dependency on the team routes.
- A separate admin panel would duplicate the existing Team page.
- Per-project ACLs are out of scope for one team in one shared workspace.
- A boolean `is_super_admin` would also work for two roles, but a role string reads better in the API and the UI.

**Theme.** `next-themes`, which is already installed as a dependency of the sonner toasts, with a class-based Tailwind dark variant.
- A hand-written hook would have to re-solve the flash of the wrong theme before React loads, which next-themes already handles with its inline script.

## Design

### Roles (backend)

- **Data:** migration `0005` adds `users.role` (text, not null, check `role in ('super_admin', 'member')`, server default `'member'`). The same migration sets every existing user to `super_admin`, because before roles every member could manage the team, so no one loses a power they had.
- **Core:**
  - `create_user(..., role=None)`. Without a role, the new user becomes `super_admin` when the team has no active super admin, and `member` otherwise. A fresh install's first login is therefore its super admin.
  - `set_role(session, user_id, role)`.
  - **Last-super-admin guard:** demoting or deactivating the last active super admin raises `Conflict("cannot remove the last active super admin")`, which becomes 409. The existing last-active-member guard stays.
- **API:**
  - `GET/POST /v1/users` and `PATCH /v1/users/{id}` require a cookie session whose user is an active `super_admin`.
  - Members get 403 `"super admin only"`. API keys get the same 403: they keep working for books, jobs and suggestions, but they can't manage the team.
  - `UserOut` and `MeOut` gain `role`. `MeOut.role` is `null` for API-key callers.
  - `POST /v1/users` accepts an optional `role`. `PATCH` accepts `role`.
- **CLI:**
  - `jerome users create EMAIL --name NAME [--role super_admin|member]`. The default follows `create_user`.
  - New: `jerome users set-role EMAIL ROLE`, for recovery when nobody can reach the Team page.

### Roles (web)

- `useMe` exposes `role`.
- The "Team" nav link is shown only to super admins. A member who opens `/team` sees "Only a super admin can manage the team" and no data.
- The Team page:
  - adds a Role column with "Super admin" and "Member" badges;
  - adds a Role select to Add member, defaulting to Member;
  - adds a row action "Make super admin" or "Make member", behind a confirmation dialog;
  - shows a 409 from the last-super-admin guard as the API's message.

### Theme (web)

- `ThemeProvider` from next-themes wraps the app, outside the router, so Login and Change password follow it too. Its settings are `attribute="class"`, `defaultTheme="system"`, `enableSystem` and `storageKey="jerome-theme"`.
- `index.css` switches the dark variant from the `prefers-color-scheme` media query to the `.dark` class, and the dark tokens move under `.dark`.
- The account menu gets a "Theme" group with System, Light and Dark radio items. The sonner toasts already read `useTheme`.
- The choice lives in localStorage and is per browser.

### Docs

The README's first-login section says the first account becomes super admin, mentions `--role` and `set-role`, and states that team management is super-admin only.

## Left to the builder

- Exact component structure, copy wording beyond the strings above, and test layout.
- Follow the existing patterns: `SessionDep`, the `Conflict` errors, `test-utils.tsx`, and the shadcn components already in `web/src/components/ui`.
