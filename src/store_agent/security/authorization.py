"""Deterministic authorization. No model output can widen a scope computed here."""

from pydantic import BaseModel

from store_agent.config import Settings
from store_agent.contracts import Capability, UserContext


class AuthorizationError(Exception):
    pass


class AuthorizedScope(BaseModel):
    user_id: str
    home_store: str
    stores: frozenset[str]
    capabilities: frozenset[Capability]


def resolve_scope(user: UserContext, settings: Settings) -> AuthorizedScope:
    role = settings.capabilities.roles.get(user.role)
    if role is None:
        return AuthorizedScope(user_id=user.user_id, home_store=user.store_id, stores=frozenset(), capabilities=frozenset())

    if role.scope == "market":
        stores = {sid for sid, s in settings.catalog.stores.items() if s.market_id == user.market_id}
    else:
        stores = {user.store_id} if user.store_id in settings.catalog.stores else set()

    granted = {Capability(p) for p in user.permissions if p in Capability.__members__}
    return AuthorizedScope(
        user_id=user.user_id,
        home_store=user.store_id,
        stores=frozenset(stores),
        capabilities=frozenset(granted & set(role.capabilities)),
    )


def require_store(scope: AuthorizedScope, store_id: str) -> None:
    if store_id not in scope.stores:
        raise AuthorizationError(f"store {store_id} is outside the caller's scope")
