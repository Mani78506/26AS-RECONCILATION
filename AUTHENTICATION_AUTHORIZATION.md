# Authentication and Authorization

## Current status

**AUTHENTICATION PROVIDER CONFIGURATION REQUIRED.**

There is no configured identity provider, trusted server-side session, token
validation, organisation membership model, or authorization middleware in this
repository. The current browser-only display name is explicitly not security
authentication and must not grant access to any production data.

The repository now contains `backend/auth_boundary.py`: a provider-neutral
OIDC configuration, verified-claims, role and ownership-policy boundary. It is
not enabled as a login system and cannot authenticate a production request
until the required provider settings below are supplied and endpoint
dependencies are wired during deployment integration.

## Required production architecture

Use the CA office's chosen OpenID Connect provider. The frontend obtains an
OIDC access token; FastAPI validates signature, issuer, audience, expiry and
claims against the provider JWKS. The backend then constructs the trusted
principal from those claims. It never accepts identity, organisation, role, or
permissions from browser storage or arbitrary request headers.

```
User -> Organisation / CA office -> Client / assessee -> Run -> Result/report
                                      |                 -> Upload
                                      |                 -> Exception/identity decision
```

Every persisted client, upload, run, result, report history, exception state,
identity decision, AI context and rule must be scoped by `organization_id`.
Resources below a client additionally carry `client_id`; children are resolved
through that ownership boundary, not by a globally supplied ID alone.

## Roles and permissions

| Role | Minimum permissions |
|---|---|
| Admin | Manage organisation, users, client access, rules and settings. |
| CA / Partner | View assigned clients; upload, run, report, review/confirm/reject identities, use AI. |
| Staff | View assigned clients; upload and execute permitted runs; cannot administer rules/settings or approve identities. |
| Viewer | Read assigned clients, runs, results, exceptions and reports only. |

Permissions must separately cover client view, upload, create/execute run,
result/exception view, identity review/confirm/reject, reporting, AI, rules
and settings.

## Endpoint protection

Before every protected action, a FastAPI dependency must:

1. Authenticate the token and establish the user.
2. Resolve the organisation.
3. Resolve client and run ownership/access.
4. Require the operation-specific permission.
5. Query MongoDB using the trusted `organization_id` boundary.

This applies to uploads, validation, reconciliation, summaries, results,
details, exceptions, reports, reruns, deletion, identity actions, AI, TDS
rules and settings. Cross-organisation or unassigned-client access must return
the selected policy response (`404` is recommended to avoid disclosure).

## AI boundary

The AI remains read-only. Its route must authorize the requested run before
building context; it may receive only the already-authorized stored result
projection, never unrestricted collections or raw uploads.

## Provider integration inputs required

- OIDC issuer and JWKS URL
- expected audience/client ID
- production redirect/logout URLs
- claim mapping for immutable user ID, organisation and roles
- organisation/client membership source and CA-office role policy
- token rotation, revocation and incident procedures

Required runtime values are `OIDC_ISSUER`, `OIDC_AUDIENCE` and
`OIDC_JWKS_URL`. The isolated `build_test_principal` adapter only operates when
`APP_ENV=test`; it must never be enabled for a deployed process.

## Required acceptance tests after integration

- unauthenticated requests are rejected;
- organization A cannot access organization B resources by changing any ID;
- viewer/staff/admin permission boundaries are enforced;
- identity, reports, reruns and AI enforce the same run/client scope;
- expiry, issuer, audience and malformed-token checks are verified.

No fake JWT, local password store, sessionStorage, localStorage or test-only
identity adapter may be enabled in production.
