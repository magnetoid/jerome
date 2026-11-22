# Team Roles and Theme Switch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Two team roles (`super_admin`, `member`), with team management limited to super admins and enforced by the server, plus a System/Light/Dark theme choice in the account menu.

**Architecture:**
- **Data:** a `role` column on `users`, added by migration `0005`.
- **Core:** the rules live in `jerome/core/users.py`: the default role, `set_role`, and the last-super-admin guard.
- **API:** a `require_super_admin` FastAPI dependency gates the `/v1/users` router.
- **Web:** reads `role` from `/v1/auth/me` to hide the Team page from members.
- **Theme:** next-themes, with a class-based Tailwind dark variant. A tiny inline script in `index.html` sets the class before the bundle loads.

**Tech Stack:**
- Backend: Python 3.12, FastAPI, SQLAlchemy 2, Alembic, Postgres 16, Typer, pytest, ruff.
- Web: React 19, TypeScript, Vite 7, Tailwind 4, shadcn/ui, react-query 5, next-themes 0.4, vitest with Testing Library.

**Spec:** `docs/superpowers/specs/2026-11-22-roles-and-theme-design.md`

## Global Constraints

- **Role values** are exactly `super_admin` and `member`.
- **Error messages:**
  - The last-super-admin guard raises `Conflict("cannot remove the last active super admin")`, which becomes 409.
  - Team routes return 403 with detail `"super admin only"` to members and to API keys.
  - The existing guard `Conflict("cannot deactivate the last active member")` stays.
- **Web copy:**
  - Members who open `/team` see "Only a super admin can manage the team".
  - Role badges read "Super admin" and "Member".
  - Row actions read "Make super admin" and "Make member".
  - The account menu's theme group is labelled "Theme", with items "System", "Light" and "Dark".
- **next-themes settings:** `attribute="class"`, `defaultTheme="system"`, `enableSystem`, `storageKey="jerome-theme"`.
- **Public repo:**
  - Never commit book files. Use only `tests/fixtures/public/` EPUBs.
  - No server names, IPs, domains or deployment IDs in committed files.
- **Backend checks:**
  - Tests: `JEROME_TEST_DATABASE_URL=postgresql+psycopg://jerome:jerome@localhost:5432/jerome_test uv run pytest -q`.
  - Lint: `uv run ruff check . && uv run ruff format --check .`.
  - `test_large_paragraph_with_eight_suggestions_classifies_quickly` is a known timing flake under load. Re-run it alone before treating it as a failure.
- **Web checks:** `cd web && npm test && npm run build`.
- **Commits** end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **A super admin demotes themself while another super admin exists.** The server allows it. The web must refetch `/v1/auth/me`, so the Team link disappears and the page shows the member message, not a table that now 403s. Pinned in Task 4.
2. **Deactivating the only active super admin while other active members exist.** This returns 409 with the super-admin message. It must not slip through just because the last-active-member guard passes. An inactive super admin does not count as the one who remains. Pinned in Task 1.
3. **Upgrading a database that already has users.** Every existing user becomes `super_admin`, and users created afterwards default to `member`. Pinned in Task 1.
4. **A dark-OS user loads any page, `/login` included.** They see no light flash before the bundle runs. A stored "light" choice wins over a dark OS. Pinned in Task 3.
5. **An integration's API key calls `/v1/users`.** It gets 403 `"super admin only"`. The same key still works for books, jobs and suggestions. Pinned in Task 2.

---

### Task 1: Role column and core rules

**Files:**
- Create: `migrations/versions/0005_user_roles.py`
- Modify: `jerome/types.py`, `jerome/orm.py` (class `User`), `jerome/core/users.py`
- Modify: `tests/conftest.py`, so the engine fixture's Alembic config comes from a helper the migration test reuses.
- Test: `tests/core/test_users.py`, `tests/test_schema.py`

**Interfaces:**
- **Produces:**
  - `Role = Literal["super_admin", "member"]` in `jerome/types.py`.
  - `User.role: Mapped[str]`.
  - `create_user(session, email, name, password=None, role: Role | None = None) -> tuple[User, str | None]`.
  - `set_role(session, user_id: int, role: str) -> User`.
  - `get_user_by_email(session, email: str) -> User`, which raises `NotFound`.
  - `update_user(...)` is unchanged in signature but gains the super-admin guard.

- [ ] **Step 1: Write the failing core tests** in `tests/core/test_users.py`:

```python
def test_first_user_is_super_admin_and_later_ones_are_members(session):
    a, _ = create_user(session, "a@x.org", "A")
    b, _ = create_user(session, "b@x.org", "B")
    c, _ = create_user(session, "c@x.org", "C", role="super_admin")
    assert (a.role, b.role, c.role) == ("super_admin", "member", "super_admin")

def test_set_role_promotes_and_demotes(session):
    a, _ = create_user(session, "a@x.org", "A")
    b, _ = create_user(session, "b@x.org", "B")
    assert set_role(session, b.id, "super_admin").role == "super_admin"
    assert set_role(session, a.id, "member").role == "member"

def test_cannot_demote_or_deactivate_the_last_active_super_admin(session):
    a, _ = create_user(session, "a@x.org", "A")
    create_user(session, "b@x.org", "B")  # an active member keeps the member guard quiet
    create_user(session, "z@x.org", "Z", role="super_admin")
    update_user(session, get_user_by_email(session, "z@x.org").id, active=False)
    # an inactive super admin does not count
    with pytest.raises(Conflict, match="cannot remove the last active super admin"):
        set_role(session, a.id, "member")
    with pytest.raises(Conflict, match="cannot remove the last active super admin"):
        update_user(session, a.id, active=False)

def test_unknown_role_and_unknown_email_are_refused(session):
    a, _ = create_user(session, "a@x.org", "A")
    with pytest.raises(InvalidChangeError):
        set_role(session, a.id, "owner")
    with pytest.raises(InvalidChangeError):
        create_user(session, "b@x.org", "B", role="owner")
    with pytest.raises(NotFound):
        get_user_by_email(session, "nobody@x.org")
    assert get_user_by_email(session, " A@X.org ").id == a.id
```

Also update `test_cannot_deactivate_last_active_member`: create both users with `role="super_admin"`, so that test still exercises the member guard.

- [ ] **Step 2: Write the failing migration test** in `tests/test_schema.py`. The `engine` fixture is at head.
  - `test_migration_0005_makes_existing_users_super_admins(engine)` downgrades to `"0004"`.
  - It inserts a user with raw SQL (`email`, `name`, `password_hash`), then upgrades to `"head"`.
  - It asserts that user's `role == "super_admin"`.
  - It asserts a raw insert without a role gets `"member"`.
  - It asserts inserting `role = 'owner'` raises `IntegrityError`.
  - It deletes its rows in a `finally`.
  - Build the Alembic `Config` through the conftest helper, not a copy.

- [ ] **Step 3: Run the new tests to verify they fail.**
  - Run: `JEROME_TEST_DATABASE_URL=... uv run pytest tests/core/test_users.py tests/test_schema.py -q`
  - Expected: FAIL with `ImportError` (`set_role`, `get_user_by_email`) or with `TypeError` from the unexpected `role`.

- [ ] **Step 4: Implement the role column and the migration.**
  - Add `Role` to `jerome/types.py` beside `Variant`.
  - Add `User.role` with `server_default=sql_text("'member'")` and `__table_args__ = (CheckConstraint(_in("role", Role), name="ck_users_role"),)`.
  - Migration `0005` (`down_revision = "0004"`):
    - `add_column` with `nullable=False`, `server_default="member"`;
    - `create_check_constraint` named `ck_users_role`;
    - then `UPDATE users SET role = 'super_admin'`.
  - The downgrade drops the constraint, then the column.

- [ ] **Step 5: Implement the core rules** in `jerome/core/users.py`.
  - **Validation:** unknown roles raise `InvalidChangeError(f"unknown role {role!r}")`.
  - **Default role:** `create_user` without a role picks `super_admin` when no active super admin exists, and `member` otherwise.
  - **One guard helper** is shared by `set_role` (super_admin → member) and `update_user(active=False)`.
    - It selects the *other* active super admins' ids `with_for_update()`. Postgres refuses `FOR UPDATE` with `count()`. The lock stops two concurrent demotions from both passing.
    - It raises the Conflict when there are none.
  - **Order in `update_user`:** the existing last-active-member check runs first, then the super-admin check.
  - **`get_user_by_email`** normalizes the email the way `authenticate` does.

- [ ] **Step 6: Run the tests to verify they pass.**
  - Run: the same command as Step 3, then the whole suite. `test_migration_matches_models` must stay green.
  - Expected: PASS.

- [ ] **Step 7: Commit.** `git commit -m "feat: user roles with a last-super-admin guard"`

---

### Task 2: Super-admin-only team API and CLI

**Files:**
- Modify: `jerome/api/deps.py` (`Principal`, `current_principal`, new `require_super_admin`)
- Modify: `jerome/api/routes_users.py`, `jerome/api/routes_auth.py` (`MeOut`, `_me`, `me`)
- Modify: `jerome/api/schemas.py` (`UserOut`, `UserCreateIn`, `UserPatchIn`)
- Modify: `jerome/cli.py`, `README.md` (the **First login** paragraph)
- Test: `tests/api/test_users_api.py`, `tests/api/test_auth_api.py`, `tests/test_cli.py`

**Interfaces:**
- **Consumes:** everything Task 1 produces.
- **Produces:**
  - `Principal.role: str | None = None`, the last field. It is the user's role for a web session and `None` for an API key.
  - `require_super_admin(principal) -> Principal`.
  - `MeOut.role: Role | None`.
  - `UserOut.role: Role`.
  - `UserCreateIn.role: Role | None = None`.
  - `UserPatchIn.role: Role | None = None`.
  - CLI `jerome users create EMAIL --name NAME [--role ROLE]` and `jerome users set-role EMAIL ROLE`.

- [ ] **Step 1: Rewrite the team API tests** in `tests/api/test_users_api.py`.
  - **Callers:** every existing test calls `/v1/users` as a super admin's cookie session over `web_client` with the `WEB` header. `login(web_client, session)` makes Ana the first user, so she is a super admin.
  - **Logins:** logging in as someone else in the same client replaces the cookie. Check temporary passwords with `users.authenticate` instead.
  - `USER_KEYS` gains `"role"`.
  - Add these tests:

```python
def test_team_routes_are_for_super_admins_only(web_client, session, client, auth):
    login(web_client, session)                                      # Ana: super admin
    login(web_client, session, "bo@x.org", "Bo")                    # Bo: member, now signed in
    for r in (web_client.get("/v1/users"),
              web_client.post("/v1/users", headers=WEB, json={"email": "c@x.org", "name": "C"}),
              web_client.patch("/v1/users/1", headers=WEB, json={"name": "X"})):
        assert r.status_code == 403 and r.json()["detail"] == "super admin only"
    keyed = client.get("/v1/users", headers=auth)                   # API keys can't manage the team
    assert keyed.status_code == 403 and keyed.json()["detail"] == "super admin only"
    assert client.get("/v1/projects", headers=auth).status_code == 200  # ...but keep their other routes

def test_add_member_with_a_role_and_change_roles(web_client, session):
    ana = login(web_client, session)
    r = web_client.post("/v1/users", headers=WEB, json={"email": "bo@x.org", "name": "Bo"})
    assert r.json()["user"]["role"] == "member"
    r = web_client.post("/v1/users", headers=WEB,
                        json={"email": "cy@x.org", "name": "Cy", "role": "super_admin"})
    assert r.json()["user"]["role"] == "super_admin"
    bad = web_client.post("/v1/users", headers=WEB, json={"email": "d@x.org", "name": "D", "role": "owner"})
    assert bad.status_code == 422
    cy = r.json()["user"]["id"]
    assert web_client.patch(f"/v1/users/{cy}", headers=WEB, json={"role": "member"}).json()["user"]["role"] == "member"
    last = web_client.patch(f"/v1/users/{ana.id}", headers=WEB, json={"role": "member"})
    assert last.status_code == 409 and last.json()["detail"] == "cannot remove the last active super admin"
```

In `tests/api/test_auth_api.py`, assert `/v1/auth/me` returns `role == "super_admin"` for the first user signed in by cookie, and `role is None` with an API key.

- [ ] **Step 2: Write the failing CLI tests** in `tests/test_cli.py`.
  - `users create b@x.org --name B --role member` on an empty team leaves B a `member`.
  - `users set-role b@x.org super_admin` exits 0 and prints `b@x.org is now super_admin`.
  - `users set-role nobody@x.org member` exits 2 with `Error:` on stderr.
  - Demoting the last super admin exits 2, with `last active super admin` on stderr.
  - `users create ... --role owner` exits 2.

- [ ] **Step 3: Run the tests to verify they fail.**
  - Run: `JEROME_TEST_DATABASE_URL=... uv run pytest tests/api/test_users_api.py tests/api/test_auth_api.py tests/test_cli.py -q`
  - Expected: FAIL. The 403 assertions get 200, `role` keys are missing, and `set-role` is not a command.

- [ ] **Step 4: Implement the API.**
  - `current_principal` sets `role=user.role` for cookie sessions.
  - `require_super_admin` raises `HTTPException(403, "super admin only")` unless `principal.channel == "web"` and `principal.role == "super_admin"`. Inactive users never reach it, because `resolve_session` refuses them.
  - Attach it router-wide in `routes_users.py` with `APIRouter(dependencies=[Depends(require_super_admin)])`. The app's `auth` dependency still runs first, so 401 and the missing-header 403 are unchanged.
  - `add_member` passes `body.role`.
  - `update_member` calls `users.set_role` after `update_user` when `body.role` is set.
  - `MeOut` and `_me` carry `role`. The API-key branch of `me` returns `role=None`.

- [ ] **Step 5: Implement the CLI and the README.**
  - `users create` gains `--role`, passed to `create_user`.
  - New `users set-role EMAIL ROLE` uses `get_user_by_email` and `set_role`.
  - Both catch `JeromeError` and print `Error: …` to stderr with exit 2, like `export`.
  - README **First login:**
    - The first account becomes the super admin.
    - Only super admins manage the team, in the web Team page or with `--role`.
    - `jerome users set-role EMAIL super_admin` recovers access when no one can reach the Team page.

- [ ] **Step 6: Run the tests to verify they pass.** Run the same command, then the full suite and ruff. Expected: PASS.

- [ ] **Step 7: Commit.** `git commit -m "feat: team management for super admins only (API and CLI)"`

---

### Task 3: Theme switch

**Files:**
- Create: `web/src/components/ThemeProvider.tsx`, `web/src/App.test.tsx`, `web/src/themeInit.test.ts`
- Modify: `web/index.html`, `web/src/index.css`, `web/src/App.tsx`, `web/src/components/AppShell.tsx`, `web/src/test-utils.tsx`, `web/src/test-setup.ts`
- Test: `web/src/components/AppShell.test.tsx`

**Interfaces:**
- **Produces:**
  - `THEME_STORAGE_KEY = "jerome-theme"`.
  - A default export `ThemeProvider({ children }: { children: ReactNode })` in `components/ThemeProvider.tsx`. It wraps next-themes with the Global Constraints settings plus `disableTransitionOnChange`.
  - `renderApp` wraps its tree in this provider.

- [ ] **Step 1: Prepare the test environment** in `test-setup.ts`.
  - Stub `window.matchMedia`. jsdom lacks it, and next-themes calls it. The stub returns `matches: false` and no-op add/remove listeners. Tests may override it.
  - Add a global `afterEach` that clears `localStorage`, `document.documentElement.className` and `document.documentElement.style.colorScheme`.

- [ ] **Step 2: Write the failing tests.**
  - **`AppShell.test.tsx`:**
    - Open the account menu. It shows a "Theme" group whose radios "System", "Light" and "Dark" have "System" checked.
    - Choose "Dark". Then `document.documentElement` has class `dark` and `localStorage.getItem("jerome-theme") === "dark"`.
    - Reopen the menu. "Dark" is checked.
  - **`App.test.tsx`:**
    - Set `localStorage.setItem("jerome-theme", "dark")`, then `window.history.pushState({}, "", "/login")`.
    - Render `<App />`, mocking whatever the login page requests.
    - Expect `document.documentElement` to have class `dark`. This pins the provider outside the router.
  - **`themeInit.test.ts`:**
    - Read `index.html` with `node:fs`, take the text of `<script id="theme-init">` and run it with `new Function(code)()`.
    - Stored `"dark"` gives class `dark`.
    - Stored `"light"` with a dark `matchMedia` gives no `dark` class.
    - Nothing stored with a dark `matchMedia` gives class `dark`.
    - `localStorage.getItem` throwing does not throw out of the script.

- [ ] **Step 3: Run the tests to verify they fail.**
  - Run: `cd web && npx vitest run src/components/AppShell.test.tsx src/App.test.tsx src/themeInit.test.ts`
  - Expected: FAIL. There is no "Theme" group, no `dark` class, and no `#theme-init` script.

- [ ] **Step 4: Implement the theme.**
  - **`index.css`:** `@custom-variant dark (&:where(.dark, .dark *));`, and the dark token block moves from the media query to `.dark { … }`.
  - **`App.tsx`:** `ThemeProvider` wraps `QueryClientProvider`, the router and `Toaster`.
  - **`AppShell`:**
    - Add a `DropdownMenuLabel` "Theme" and a `DropdownMenuRadioGroup` bound to `useTheme()`'s `theme` and `setTheme`.
    - Its values are `system`, `light` and `dark`, labelled System, Light and Dark.
    - It sits between "Change password" and "Log out", with separators.
  - **`index.html`:** add `<script id="theme-init">` in `<head>`.
    - It is plain ES5 inside an IIFE and wrapped in `try`.
    - It reads `localStorage["jerome-theme"]`.
    - It adds class `dark` when the value is `"dark"`, or when it is missing or `"system"` and `matchMedia("(prefers-color-scheme: dark)")` matches.
    - It sets `style.colorScheme` to match.
    - This exists because in a client-only app next-themes applies its class only after the bundle runs. The server sets no CSP, so an inline script is allowed.

- [ ] **Step 5: Run the tests to verify they pass.** Run `cd web && npm test && npm run build`. Expected: all pass, and the build is clean.

- [ ] **Step 6: Commit.** `git commit -m "feat(web): System, Light and Dark theme choice"`

---

### Task 4: Roles in the web interface

**Files:**
- Modify: `web/src/hooks/useMe.ts`, `web/src/test-utils.tsx`, `web/src/components/AppShell.tsx`, `web/src/pages/TeamPage.tsx`
- Test: `web/src/components/AppShell.test.tsx`, `web/src/pages/TeamPage.test.tsx`

**Interfaces:**
- **Consumes:**
  - The API from Task 2: `role` on `/v1/auth/me` and on `/v1/users` rows; `role` accepted by POST and PATCH `/v1/users`; 409 detail strings.
  - `renderApp` from Task 3.
- **Produces:**
  - `Role = "super_admin" | "member"` and `Me.role: Role | null` in `useMe.ts`.
  - In `test-utils.tsx`, `ME` gains `role: "super_admin"`, and a new `MEMBER = { ...ME, role: "member" }`.

- [ ] **Step 1: Write the failing tests.**
  - **`AppShell.test.tsx`:** the "Team" nav link shows for `ME` and is absent for `MEMBER`.
  - **`TeamPage.test.tsx`** (the `ann` and `bob` fixtures gain `role: "super_admin"` and `role: "member"`):
    - **Member:** with `/v1/auth/me` returning `MEMBER`, the page shows `/only a super admin can manage the team/i`. `fetch` is never called with `/v1/users`.
    - **Role column:** a "Role" column shows "Super admin" for Ann and "Member" for Bob.
    - **Add member:**
      - The form has a "Role" select whose value is `member`.
      - Choosing "Super admin" and submitting POSTs `{ email, name, role: "super_admin" }`.
      - After success the select is back to `member`.
    - **Confirm and cancel:**
      - "Make Bob super admin" opens a confirmation. Confirming PATCHes `/v1/users/2` with `{ role: "super_admin" }`.
      - Cancel sends nothing.
    - **Last super admin:**
      - "Make Ann a member" is Ann's own row, since `ME.id === 1`.
      - Confirming against a 409 `{ detail: "cannot remove the last active super admin" }` calls `toast.error` with that detail.
    - **Self-demotion:**
      - The same action answered with 200 makes `GET /v1/auth/me` run again, so the cached role refreshes.
      - With the refetched `MEMBER`, the page shows the member message.

- [ ] **Step 2: Run the tests to verify they fail.**
  - Run: `cd web && npx vitest run src/components/AppShell.test.tsx src/pages/TeamPage.test.tsx`
  - Expected: FAIL.

- [ ] **Step 3: Implement.**
  - **`AppShell`:** render the Team `NavLink` only when `me.data.role === "super_admin"`.
  - **`TeamPage`:**
    - The users query is `enabled` only for a super admin. Any other loaded role gets the heading and the member message.
    - `Member` gains `role`.
    - **Role column:** add it with `Badge`, using the default variant for "Super admin" and `outline` for "Member".
    - **Add member:** add a Role native `<select>`, styled with `NATIVE_SELECT` and labelled "Role". Its options are Member (`member`, the default) and Super admin (`super_admin`).
    - **Role action:** add a row action whose aria-label is `Make ${name} super admin` or `Make ${name} a member`. It opens an `AlertDialog`; the description for your own row says you will no longer be able to manage the team. Confirming PATCHes `{ role }`.
    - **After your own role changes:** after a PATCH that changed the signed-in user's own role, invalidate `ME_KEY` as well as the users list.

- [ ] **Step 4: Run the tests to verify they pass.** Run `cd web && npm test && npm run build`. Expected: all pass, and the build is clean.

- [ ] **Step 5: Commit.** `git commit -m "feat(web): roles on the Team page; Team hidden from members"`
