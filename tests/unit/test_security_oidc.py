"""OIDC abstraction tests (Phase 2).

The validator is a mock IdP (static token→claims table) shaped like
Keycloak/Auth0 access tokens; the mapper is the production mapping path.
"""

from __future__ import annotations

from universal_agent.security import (
    OidcClaimsPrincipalMapper,
    Role,
    Scope,
    StaticClaimsTokenValidator,
)

_VALID_TOKEN = "eyJhbGciOiJSUzI1NiJ9.mock-signature"
_ADMIN_TOKEN = "admin-token"


def _validator() -> StaticClaimsTokenValidator:
    return StaticClaimsTokenValidator(
        tokens={
            _VALID_TOKEN: {
                "sub": "alice",
                "email": "alice@acme.test",
                "preferred_username": "alice",
                "tenant": "acme",
                "tenant_name": "Acme Corp",
                "roles": ["read_only", "worker"],
            },
            _ADMIN_TOKEN: {
                "sub": "root",
                "tenant": "acme",
                "roles": ["admin"],
            },
        }
    )


def test_validator_returns_verified_claims_for_known_token() -> None:
    claims = _validator().validate(_VALID_TOKEN)
    assert claims is not None
    assert claims["sub"] == "alice"
    assert claims["tenant"] == "acme"


def test_validator_rejects_unknown_token() -> None:
    assert _validator().validate("bogus") is None


def test_mapper_produces_principal_from_keycloak_style_claims() -> None:
    claims = _validator().validate(_VALID_TOKEN)
    assert claims is not None
    principal = OidcClaimsPrincipalMapper().map_claims(claims)
    assert principal is not None
    assert principal.user_id == "alice"
    assert principal.user.display_name == "alice"  # preferred_username wins
    assert principal.tenant_id == "acme"
    assert principal.tenant.name == "Acme Corp"
    assert principal.role is Role.READ_ONLY
    assert principal.scope is Scope.READ_ONLY


def test_mapper_admin_claims_get_read_write_scope() -> None:
    claims = _validator().validate(_ADMIN_TOKEN)
    assert claims is not None
    principal = OidcClaimsPrincipalMapper().map_claims(claims)
    assert principal is not None
    assert principal.role is Role.ADMIN
    assert principal.scope is Scope.READ_WRITE


def test_mapper_requires_subject_and_tenant() -> None:
    mapper = OidcClaimsPrincipalMapper()
    assert mapper.map_claims({"tenant": "acme"}) is None
    assert mapper.map_claims({"sub": "alice"}) is None
    assert mapper.map_claims({}) is None


def test_mapper_falls_back_to_default_role_without_roles_claim() -> None:
    mapper = OidcClaimsPrincipalMapper()
    principal = mapper.map_claims({"sub": "alice", "tenant": "acme"})
    assert principal is not None
    assert principal.role is Role.OPERATOR

    strict = OidcClaimsPrincipalMapper(default_role=Role.READ_ONLY)
    assert strict.map_claims({"sub": "alice", "tenant": "acme"}) is not None


def test_mapper_ignores_unknown_role_names() -> None:
    mapper = OidcClaimsPrincipalMapper()
    principal = mapper.map_claims(
        {"sub": "alice", "tenant": "acme", "roles": ["astronaut", "admin"]}
    )
    assert principal is not None
    assert principal.role is Role.ADMIN
