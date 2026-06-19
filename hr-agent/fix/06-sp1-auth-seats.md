# SP1 — Identity & Seats (Firebase Google login)

> **STATUS: DEFERRED.** The existing key-match auth stays for now. Build the
> backend workflow this weekend on top of it; come back to real auth after.
> This doc captures the design so it's ready when you reach it.
>
> Replace key-match with Google sign-in via Firebase, a `users` table (schema
> already added in SP2), and **seats, not RBAC**. Tenant-ready: users belong to an
> `organizations` row.

---

## 1. Approach

- **Firebase Authentication** with the Google provider (frontend SDK). Basic: no
  custom-claims gymnastics, no RBAC matrix.
- Backend verifies the Firebase **ID token** per request (middleware), maps
  `google_sub`/`email` → a `users` row scoped to the org.
- **Seats:** a user can sign in only if their email has an `active`/`invited`
  `users` row in the org. Admin adds users (DB insert now, Settings UI later).
  No seat = no access. "Seats not roles."

## 2. Backend

- `auth/firebase.py`: verify ID token (Firebase Admin SDK), cache JWKS.
- FastAPI dependency `current_user()`: read bearer token → verify → extract
  email/sub → look up `users` in org → reject if missing/disabled → attach
  `user` + `org_id` to request context.
- Replace the key-match dependency everywhere. `actor` in audit/events becomes the
  real user email.

## 3. Frontend

- Firebase web SDK + Google sign-in; token in the API client auth header
  (replaces the static key). Refresh via the SDK.
- Minimal **Settings → Members** page: list/invite/disable users.

## 4. Org scoping (tenant-ready payoff)

Every request carries `org_id`; all queries filter by it (already keyed in SP2).
Onboarding a second company = add an `organizations` row + its users + its
`company_persona`. No schema change.

## 5. Implementation plan

1. Firebase project + Google provider; service-account creds in secrets.
2. Backend token verification + `current_user()` + middleware.
3. Swap the key-auth dependency across routes; map `actor` to real users.
4. Frontend sign-in + token wiring.
5. Members settings page (minimal).
6. Remove the static key path + env var.

## Acceptance criteria

- No route accepts the old static key.
- Only emails with an active `users` row in the org can sign in.
- Audit/events record the real acting user.
- Adding a second org requires data inserts only, no migration.

## Out of scope (later)

RBAC/permissions, per-org billing, SSO/SAML, row-level security.
