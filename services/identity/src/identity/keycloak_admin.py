"""Thin async wrapper over the Keycloak Admin REST API.

Isolates every Keycloak-specific detail (token acquisition, org member endpoints,
role mapping) so the rest of the platform sees a plain user/role interface.
"""

from __future__ import annotations

import httpx

from ai_os_shared.errors import UpstreamError
from ai_os_shared.settings import Settings, get_settings


class KeycloakAdmin:
    def __init__(self, settings: Settings | None = None) -> None:
        self._s = settings or get_settings()
        self._base = self._s.keycloak_url.rstrip("/")
        self._realm = self._s.keycloak_realm

    async def _token(self, client: httpx.AsyncClient) -> str:
        """Obtain an admin access token from the master realm (admin-cli)."""
        resp = await client.post(
            f"{self._base}/realms/master/protocol/openid-connect/token",
            data={
                "grant_type": "password",
                "client_id": "admin-cli",
                "username": self._s.keycloak_admin,
                "password": self._s.keycloak_admin_password,
            },
        )
        if resp.status_code != 200:
            raise UpstreamError(f"Keycloak admin auth failed: {resp.status_code}")
        return resp.json()["access_token"]

    async def _headers(self, client: httpx.AsyncClient) -> dict:
        return {"Authorization": f"Bearer {await self._token(client)}"}

    async def list_org_members(self, org_id: str) -> list[dict]:
        async with httpx.AsyncClient(timeout=15) as client:
            h = await self._headers(client)
            resp = await client.get(
                f"{self._base}/admin/realms/{self._realm}/organizations/{org_id}/members",
                headers=h,
            )
            if resp.status_code == 404:
                return []
            resp.raise_for_status()
            return resp.json()

    async def get_user_roles(self, user_id: str) -> list[str]:
        async with httpx.AsyncClient(timeout=15) as client:
            h = await self._headers(client)
            resp = await client.get(
                f"{self._base}/admin/realms/{self._realm}/users/{user_id}"
                "/role-mappings/realm",
                headers=h,
            )
            resp.raise_for_status()
            return [r["name"] for r in resp.json()]

    async def create_user(
        self, org_id: str, email: str, first: str, last: str, password: str
    ) -> str:
        async with httpx.AsyncClient(timeout=20) as client:
            h = await self._headers(client)
            resp = await client.post(
                f"{self._base}/admin/realms/{self._realm}/users",
                headers=h,
                json={
                    "username": email,
                    "email": email,
                    "firstName": first,
                    "lastName": last,
                    "enabled": True,
                    "emailVerified": True,
                    "credentials": [
                        {"type": "password", "value": password, "temporary": False}
                    ],
                },
            )
            if resp.status_code not in (201, 204):
                raise UpstreamError(f"Create user failed: {resp.status_code} {resp.text}")
            # Look up the created user id by username.
            found = await client.get(
                f"{self._base}/admin/realms/{self._realm}/users",
                headers=h,
                params={"username": email, "exact": "true"},
            )
            found.raise_for_status()
            user_id = found.json()[0]["id"]
            # Add to the tenant's organization. KC 26 accepts the raw user id as the
            # request body on this endpoint.
            add = await client.post(
                f"{self._base}/admin/realms/{self._realm}/organizations/{org_id}/members",
                headers={**h, "Content-Type": "application/json"},
                json=user_id,
            )
            if add.status_code not in (201, 204):
                raise UpstreamError(
                    f"Add user to organization failed: {add.status_code} {add.text}"
                )
            return user_id

    async def assign_realm_role(self, user_id: str, role: str) -> None:
        async with httpx.AsyncClient(timeout=15) as client:
            h = await self._headers(client)
            role_resp = await client.get(
                f"{self._base}/admin/realms/{self._realm}/roles/{role}", headers=h
            )
            role_resp.raise_for_status()
            role_repr = role_resp.json()
            resp = await client.post(
                f"{self._base}/admin/realms/{self._realm}/users/{user_id}"
                "/role-mappings/realm",
                headers={**h, "Content-Type": "application/json"},
                json=[{"id": role_repr["id"], "name": role_repr["name"]}],
            )
            if resp.status_code not in (204, 201):
                raise UpstreamError(f"Assign role failed: {resp.status_code}")
