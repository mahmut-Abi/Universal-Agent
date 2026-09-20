"""OIDC / external identity abstraction (Phase 2).

The design deliberately does not couple the kernel to a concrete IdP. An
enterprise deployment plugs in a ``TokenValidator`` that verifies an external
token (Keycloak / Auth0 / Okta / self-hosted) and returns its **verified
claims**; the ``OidcClaimsPrincipalMapper`` then maps those claims onto the
runtime's principal model (user + tenant + role).

JWT signature verification, JWKS fetch, and issuer/audience checks are the
validator implementation's job and remain deliberately out of tree until an
IdP decision lands (see docs/security-production-decisions.md, Identity
provider row).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from universal_agent.core import JsonValue
from universal_agent.security.principal import (
    RequestPrincipal,
    Role,
    Scope,
    Tenant,
    UserPrincipal,
)


class TokenValidator(Protocol):
    """Verifies an external identity token and returns its claims.

    Implementations must verify authenticity (signature/issuer/audience) and
    return ``None`` for invalid or untrusted tokens. Claims are untrusted data
    until the validator has verified them.
    """

    def validate(self, token: str) -> Mapping[str, JsonValue] | None:
        """Return verified claims for a valid token, or None."""
        ...


@dataclass(frozen=True, slots=True)
class StaticClaimsTokenValidator:
    """Test/dev validator: a fixed token→claims table (a mock IdP).

    Mirrors the claim shapes of Keycloak/Auth0 style access tokens closely
    enough to exercise the mapping path in tests without network or crypto.
    """

    tokens: Mapping[str, Mapping[str, JsonValue]]

    def validate(self, token: str) -> Mapping[str, JsonValue] | None:
        claims = self.tokens.get(token)
        return dict(claims) if claims is not None else None


class OidcClaimsPrincipalMapper:
    """Maps verified OIDC claims onto a RequestPrincipal.

    Recognized claims:
      - ``sub`` (required): the external subject, used as the user id
      - ``email`` / ``preferred_username``: display fallbacks
      - ``tenant``: the tenant the subject acts within
      - ``roles``: list of role names; the first recognized one wins
    """

    def __init__(self, *, default_role: Role = Role.OPERATOR) -> None:
        self._default_role = default_role

    def map_claims(self, claims: Mapping[str, JsonValue]) -> RequestPrincipal | None:
        subject = claims.get("sub")
        if not isinstance(subject, str) or not subject:
            return None
        tenant_value = claims.get("tenant")
        if not isinstance(tenant_value, str) or not tenant_value:
            return None
        role = self._role_from_claims(claims)
        tenant_name = tenant_value
        name_claim = claims.get("tenant_name")
        if isinstance(name_claim, str) and name_claim:
            tenant_name = name_claim
        return RequestPrincipal(
            user=UserPrincipal(user_id=subject, display_name=_display_name(claims)),
            tenant=Tenant(tenant_id=tenant_value, name=tenant_name),
            role=role,
            scope=Scope.READ_WRITE if role is not Role.READ_ONLY else Scope.READ_ONLY,
        )

    def _role_from_claims(self, claims: Mapping[str, JsonValue]) -> Role:
        roles = claims.get("roles")
        if isinstance(roles, list):
            for item in roles:
                if isinstance(item, str):
                    try:
                        return Role(item)
                    except ValueError:
                        continue
        return self._default_role


def _display_name(claims: Mapping[str, JsonValue]) -> str | None:
    for key in ("preferred_username", "email", "name"):
        value = claims.get(key)
        if isinstance(value, str) and value:
            return value
    return None
