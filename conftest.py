#!/usr/bin/python
#coding=utf-8

import json
import logging

import httpx
import pytest
import pytest_asyncio

from tnxqso.db import DB
from tnxqso.common import WEB_ADDRESS

@pytest_asyncio.fixture
async def _db(scope="function"):
    await DB.connect()
    yield DB

@pytest.fixture(scope="function")
def tnxqso_request():
        
    client = httpx.AsyncClient()

    async def _request(url, method="POST", **kwargs):
        return await client.request(method, f"{WEB_ADDRESS}/{url}", **kwargs)

    return _request

@pytest.fixture(scope="function")
def user_login(_db, tnxqso_request):

    async def _login(callsign):
        user_data = await _db.get_user_data(callsign)
        login_rsp = await tnxqso_request('aiohttp/login',
                json={'login': callsign,
                    'password': user_data['password']})
        login_rsp.raise_for_status()
        return json.loads(login_rsp.text)

    return _login
