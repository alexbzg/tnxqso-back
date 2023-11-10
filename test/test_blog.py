#!/usr/bin/python
#coding=utf-8

import json
import logging
import asyncio

import httpx
import pytest

from tnxqso.common import WEB_ADDRESS

TEST_USER1_CALLSIGN = "q0001"

@pytest.mark.asyncio
async def test_quota(tnxqso_request, user_login):
    TEST_USER1_TOKEN = (await user_login(TEST_USER1_CALLSIGN))['token']
    loc_rsp = await tnxqso_request('aiohttp/blog/quota',
            json={'token': TEST_USER1_TOKEN})
    loc_rsp.raise_for_status()
    loc_data = json.loads(loc_rsp.text)
    logging.debug(loc_data)
