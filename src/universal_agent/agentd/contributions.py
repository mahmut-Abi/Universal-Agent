"""agentd route contribution contracts (compatibility re-export).

The contracts live in the kernel (``universal_agent.domain.host_contracts``)
so domain packages contribute HTTP routes without importing the agentd
application adapter; agentd consumes them through this seam.
"""

from __future__ import annotations

from universal_agent.host_contracts import (
    AGENTD_ROUTES_ENTRY_POINT_GROUP,
    DomainRouteContribution,
    DomainRouteDefinition,
    DomainRouteResponse,
    load_route_contributions,
    match_domain_route,
)

__all__ = [
    "AGENTD_ROUTES_ENTRY_POINT_GROUP",
    "DomainRouteContribution",
    "DomainRouteDefinition",
    "DomainRouteResponse",
    "load_route_contributions",
    "match_domain_route",
]
