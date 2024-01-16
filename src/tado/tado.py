"""Asynchronous Python client for the Tado API."""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Self
from urllib import request

import orjson
from aiohttp import ClientResponseError
from aiohttp.client import ClientSession
from yarl import URL

from tado.const import HttpMethod
from tado.exceptions import (
    TadoAuthenticationError,
    TadoBadRequestError,
    TadoConnectionError,
    TadoException,
    TadoForbiddenError,
)
from tado.models import Device, GetMe, MobileDevice, Zone


@dataclass
class Tado:
    """Base class for Tado."""

    session: ClientSession | None = None
    request_timeout: int = 10
    _username: str | None = None
    _password: str | None = None
    _debug: bool = False

    _client_id = "tado-web-app"
    _client_secret = "wZaRN7rpjn3FoNyF5IFuxg9uMzYJcvOoQ8QWiIqS3hfk6gLhVlG57j5YNoZL2Rtc"
    _authorization_base_url = "https://auth.tado.com/oauth/authorize"
    _token_url = "https://auth.tado.com/oauth/token"
    _api_url = "my.tado.com/api/v2"

    def __init__(self, username: str, password: str, debug: bool = False) -> None:
        """Initialize the Tado object."""
        self._username: str = username
        self._password: str = password
        self._headers: dict = {
            "Content-Type": "application/json",
            "Referer": "https://app.tado.com/",
        }
        self._access_token: str | None = None
        self._token_expiry: float | None = None
        self._refesh_token: str | None = None
        self._access_headers: dict | None = None
        self._home_id: int | None = None
        self._me: dict | None = None

    async def _login(self) -> None:
        """Perform login to Tado."""
        data = {
            "client_id": self._client_id,
            "client_secret": self._client_secret,
            "grant_type": "password",
            "scope": "home.user",
            "username": self._username,
            "password": self._password,
        }

        if self.session is None:
            self.session = ClientSession()
            self._close_session = True

        try:
            async with asyncio.timeout(self.request_timeout):
                request = await self.session.post(url=self._token_url, data=data)
                request.raise_for_status()
        except asyncio.TimeoutError:
            raise TadoConnectionError("Timeout occurred while connecting to Tado.")
        except ClientResponseError:
            await self.check_request_status(request)

        if "application/json" not in request.headers.get("content-type"):
            text = await request.text()
            raise TadoException(
                f"Unexpected response from Tado. Content-Type: {request.headers.get('content-type')}, Response body: {text}"
            )

        response = await request.json()
        self._access_token = response["access_token"]
        self._token_expiry = time.time() + float(response["expires_in"])
        self._refesh_token = response["refresh_token"]

        get_me = await self.get_me()
        self._home_id = get_me.homes[0].id

    async def check_request_status(self, request: request) -> None:
        """Check the status of the request and raise the proper exception if needed."""
        status_error_mapping = {
            400: TadoBadRequestError(
                f"Bad request to Tado. Response body: {await request.text()}"
            ),
            500: TadoException(
                f"Error {request.status} connecting to Tado. Response body: {await request.text()}"
            ),
            401: TadoAuthenticationError(
                f"Authentication error connecting to Tado. Response body: {await request.text()}"
            ),
            403: TadoForbiddenError(
                f"Forbidden error connecting to Tado. Response body: {await request.text()}"
            ),
        }

        if request.status in status_error_mapping:
            raise status_error_mapping.get(
                request.status,
                TadoException(f"Error {request.status} connecting to Tado."),
            )
        raise TadoException(
            f"Error {request.status} connecting to Tado. Response body: {await request.text()}"
        )

    async def _refresh_auth(self) -> None:
        """Refresh the authentication token."""
        if time.time() < self._token_expiry - 30:
            return

        data = {
            "client_id": self._client_id,
            "client_secret": self._client_secret,
            "grant_type": "refresh_token",
            "scope": "home.user",
            "refresh_token": self._refesh_token,
        }

        try:
            async with asyncio.timeout(self.request_timeout):
                request = await self.session.post(url=self._token_url, data=data)
                request.raise_for_status()
        except asyncio.TimeoutError:
            raise TadoConnectionError("Timeout occurred while connecting to Tado.")
        except ClientResponseError:
            await self.check_request_status(request)

        response = await request.json()
        self._access_token = response["access_token"]
        self._token_expiry = time.time() + float(response["expires_in"])
        self._refesh_token = response["refresh_token"]

    async def get_me(self) -> GetMe:
        """Get the user information."""
        if self._me is None:
            response = await self._request("me")
            self._me = GetMe.from_json(response)
        return self._me

    async def get_devices(self) -> dict[str, Device]:
        """Get the devices."""
        response = await self._request(f"homes/{self._home_id}/devices")
        obj = orjson.loads(response)
        return [Device.from_dict(device) for device in obj]
    
    async def get_mobile_devices(self) -> dict[str, MobileDevice]:
        """Get the mobile devices."""
        response = await self._request(f"homes/{self._home_id}/mobileDevices")
        obj = orjson.loads(response)
        return [MobileDevice.from_dict(device) for device in obj]
    
    async def get_zones(self) -> dict[str, Zone]:
        """Get the zones."""
        response = await self._request(f"homes/{self._home_id}/zones")
        obj = orjson.loads(response)
        return [Zone.from_dict(zone) for zone in obj]
    
    async def get_zone_states(self) -> dict[str, Zone]:
        """Get the zone states."""
        response = await self._request(f"homes/{self._home_id}/zoneStates")
        obj = orjson.loads(response)
        return [Zone.from_dict(zone) for zone in obj]

    async def _request(
        self, uri: str, data: dict | None = None, method: str = HttpMethod.GET
    ) -> dict[str, Any]:
        """Handle a request to the Tado API."""
        await self._refresh_auth()

        url = URL.build(scheme="https", host=self._api_url).joinpath(uri)

        # versienummer nog toevoegen
        headers = {
            "Authorization": f"Bearer {self._access_token}",
        }

        try:
            async with asyncio.timeout(self.request_timeout):
                request = await self.session.request(
                    method=method.value, url=str(url), headers=headers, json=data
                )
                request.raise_for_status()
        except asyncio.TimeoutError:
            raise TadoConnectionError("Timeout occurred while connecting to Tado.")
        except ClientResponseError:
            await self.check_request_status(request)

        return await request.text()

    async def close(self) -> None:
        """Close open client session."""
        if self.session and self._close_session:
            await self.session.close()

    async def __aenter__(self) -> Self:
        """Async enter.

        Returns
        -------
            The Tado object.
        """
        await self._login()
        return self

    async def __aexit__(self, *_exc_info: object) -> None:
        """Async exit.

        Args:
        ----
            _exc_info: Exec type.
        """
        await self.close()