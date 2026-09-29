"""OIDC integration boundary; deliberately not a local-login substitute.

This module is inert until an identity provider is configured.  It provides
the trusted-principal and ownership contracts the FastAPI routes must consume
when production OIDC settings are supplied.  It never accepts a browser name,
role, organisation or arbitrary request header as an identity claim.
"""

from dataclasses import dataclass
import os
from typing import Iterable


ROLES = {"ADMIN", "CA_PARTNER", "STAFF", "VIEWER"}
PERMISSIONS = {
    "view_client", "create_run", "upload_source", "validate", "reconcile",
    "view_results", "view_exceptions", "review_identity", "confirm_identity",
    "reject_identity", "keep_unmapped", "generate_reports", "rerun", "use_ai",
    "manage_rules", "manage_settings", "delete_run",
}
ROLE_PERMISSIONS = {
    "ADMIN": PERMISSIONS,
    "CA_PARTNER": PERMISSIONS - {"manage_settings", "delete_run"},
    "STAFF": {"view_client", "create_run", "upload_source", "validate", "reconcile", "view_results", "view_exceptions", "review_identity", "generate_reports", "rerun", "use_ai"},
    "VIEWER": {"view_client", "view_results", "view_exceptions", "generate_reports", "use_ai"},
}


class AuthenticationProviderConfigurationRequired(RuntimeError):
    """Raised when an OIDC-protected deployment lacks required settings."""


class NonProductionAdapterDisabled(RuntimeError):
    """Raised if anyone attempts to use the test adapter outside test mode."""


@dataclass(frozen=True)
class OIDCConfiguration:
    issuer: str
    audience: str
    jwks_url: str

    @classmethod
    def from_environment(cls) -> "OIDCConfiguration":
        values = {
            "issuer": os.getenv("OIDC_ISSUER", "").strip(),
            "audience": os.getenv("OIDC_AUDIENCE", "").strip(),
            "jwks_url": os.getenv("OIDC_JWKS_URL", "").strip(),
        }
        missing = [name for name, value in values.items() if not value]
        if missing:
            raise AuthenticationProviderConfigurationRequired(
                "PRODUCTION AUTHENTICATION PROVIDER CONFIGURATION REQUIRED: " + ", ".join(missing)
            )
        return cls(**values)


@dataclass(frozen=True)
class Principal:
    user_id: str
    organization_id: str
    roles: frozenset[str]
    client_ids: frozenset[str] | None = None

    def permits(self, permission: str) -> bool:
        return permission in set().union(*(ROLE_PERMISSIONS.get(role, set()) for role in self.roles))

    def can_access_client(self, client_id: str) -> bool:
        return self.client_ids is None or client_id in self.client_ids


def principal_from_verified_claims(claims: dict) -> Principal:
    """Map claims *after* a provider JWT has been cryptographically verified."""
    user_id = str(claims.get("sub") or "").strip()
    organization_id = str(claims.get("organization_id") or claims.get("org_id") or "").strip()
    raw_roles = claims.get("roles") or claims.get("role") or []
    roles = {raw_roles} if isinstance(raw_roles, str) else set(raw_roles)
    roles = {str(role).upper().replace("CA / PARTNER", "CA_PARTNER") for role in roles} & ROLES
    if not user_id or not organization_id or not roles:
        raise PermissionError("Verified token is missing required subject, organisation or role claims.")
    clients = claims.get("client_ids")
    client_ids = None if clients is None else frozenset(str(value) for value in clients)
    return Principal(user_id=user_id, organization_id=organization_id, roles=frozenset(roles), client_ids=client_ids)


def authorize(principal: Principal, permission: str, *, organization_id: str, client_id: str | None = None) -> None:
    """Server-side policy check used after the resource is resolved from Mongo."""
    if permission not in PERMISSIONS or not principal.permits(permission):
        raise PermissionError("Not authorized for this operation.")
    if organization_id != principal.organization_id:
        raise PermissionError("Resource is not available.")
    if client_id and not principal.can_access_client(client_id):
        raise PermissionError("Resource is not available.")


def build_test_principal(*, user_id: str, organization_id: str, roles: Iterable[str], client_ids: Iterable[str] | None = None) -> Principal:
    """Test-only adapter. It cannot be used by a production process."""
    if os.getenv("APP_ENV") != "test":
        raise NonProductionAdapterDisabled("The test authentication adapter is unavailable outside APP_ENV=test.")
    return Principal(user_id=user_id, organization_id=organization_id, roles=frozenset(str(role).upper() for role in roles), client_ids=None if client_ids is None else frozenset(client_ids))


def build_development_principal() -> Principal:
    """Explicit opt-in local principal for browser workflow verification.

    This is intentionally narrower than a token verifier and is disabled
    unless both development environment markers are present.
    """
    if os.getenv("APP_ENV") != "development" or os.getenv("TDS_COMPLIANCE_DEV_AUTH", "").lower() != "true":
        raise NonProductionAdapterDisabled("Development authentication is disabled.")
    return Principal(
        user_id="tds-development-user",
        organization_id=os.getenv("TDS_DEV_ORGANIZATION_ID", "TEST_ORG"),
        roles=frozenset({"ADMIN"}),
        client_ids=frozenset({os.getenv("TDS_DEV_CLIENT_ID", "TDS-TEST-001")}),
    )
