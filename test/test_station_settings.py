#!/usr/bin/python
#coding=utf-8

import json
import logging
import asyncio

import httpx
import pytest

from tnxqso.common import WEB_ADDRESS

TEST_USER1_CALLSIGN = "q0001"
TEST_USER2_CALLSIGN = "q0002"

@pytest.mark.asyncio
async def test_ban(tnxqso_request, user_login):
    TEST_USER1_TOKEN = (await user_login(TEST_USER1_CALLSIGN))['token']
    #ban 
    ban_rsp = await tnxqso_request('aiohttp/station/banlist',
            json={'token': TEST_USER1_TOKEN,
                'stationAdmin': TEST_USER1_CALLSIGN,
                'banned': TEST_USER2_CALLSIGN})
    ban_rsp.raise_for_status()

    #read bablist
    banlist_rsp = await tnxqso_request(f'aiohttp/station/{TEST_USER1_CALLSIGN}/banlist', method="GET")
    banlist_rsp.raise_for_status()
    banlist = json.loads(banlist_rsp.text)
    assert [ban for ban in banlist if ban['callsign'] == TEST_USER2_CALLSIGN]
    
    #unban
    unban_rsp = await tnxqso_request('aiohttp/station/banlist',
            method="DELETE",
            json={'token': TEST_USER1_TOKEN,
                'stationAdmin': TEST_USER1_CALLSIGN,
                'banned': TEST_USER2_CALLSIGN})
    unban_rsp.raise_for_status()

    #read banlist
    banlist_rsp = await tnxqso_request(f'aiohttp/station/{TEST_USER1_CALLSIGN}/banlist', method="GET")
    banlist_rsp.raise_for_status()
    banlist = json.loads(banlist_rsp.text)
    assert not banlist
