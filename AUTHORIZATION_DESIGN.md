# Authentication and authorization integration design

## Status

**Blocked pending deployment identity-provider selection.** The current frontend
display-name session is not authentication and must not be used as an access
control mechanism.

## Ownership model

```
Authenticated user -> Organisation / CA office -> Client / assessee -> Run
                                                        -> Upload
                                                        -> Result / report / exception
```

Every persisted client, upload, run, result, report-history entry, exception
state, identity decision and TDS rule must carry `organisation_id`. Client data
also carries `client_id`; child resources must be queried through their parent
ownership boundary, never only by an externally supplied identifier.

## Roles

| Role | Permissions |
|---|---|
| `ORG_ADMIN` | Manage organisation users, clients, rules and settings. |
| `CA_REVIEWER` | Create/upload/reconcile, view assigned clients, review identities/exceptions, export reports. |
| `PREPARER` | Create uploads/runs and view assigned clients; cannot approve identity or change rules/settings. |
| `READ_ONLY` | View assigned clients, runs, results and reports only. |

## Server-side boundary

1. Select a production OIDC provider compatible with the deployment (for
   example the organisation's existing SSO provider).
2. The frontend obtains an OIDC access token; it must not create a local token.
3. FastAPI validates issuer, audience, expiry and signature using the
   provider's JWKS. A dependency builds a trusted `CurrentPrincipal`.
4. Route dependencies resolve the requested resource with
   `organisation_id` and confirm role/client assignment before any read or
   mutation.
5. Mongo indexes include ownership fields, for example
   `(organisation_id, run_id)`, `(organisation_id, upload_id)` and
   `(organisation_id, client_id)`.
6. Audit fields record authenticated `user_id`, never a browser-provided
   reviewer display name.

## Protected resources

The authorization dependency applies to client/runs/uploads/results/reports,
exceptions, identity actions, TDS rules/settings, reruns and deletion. A
request changing `client_id`, `run_id` or `upload_id` must return `404` (or
`403`, according to the selected policy) if the authenticated principal has no
ownership grant.

## Required tests after provider integration

- User A cannot retrieve, export, rerun, edit or delete User B's organisation
  resources by identifier substitution.
- Role tests for every write endpoint.
- Token expiry, invalid issuer/audience and revoked-user handling.
- Multi-client assignment isolation inside the same organisation.

This design intentionally does not invent a fake JWT, local password store or
sessionStorage-based authorization.
