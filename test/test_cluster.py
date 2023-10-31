#!/usr/bin/python3
#coding=utf-8
import json
import logging
import asyncio

import httpx
import pytest

from tnxqso.common import WEB_ADDRESS

SPOT_CALLSIGN = "R1AV"
USER_CALLSIGN = "R7CL"
FREQ = "14074,0"

@pytest.mark.asyncio
async def test_valid_requests():
    async with httpx.AsyncClient() as client:
        payload = {
                "cs": SPOT_CALLSIGN,
                "userCS": USER_CALLSIGN,
                "freq": FREQ,
                "info": "www.TNXQSO.com"
                }
        
        rsp = await client.post(f"{WEB_ADDRESS}/aiohttp/sendSpot", json=payload)
        rsp.raise_for_status()
        rsp_data = json.loads(rsp.text)
        logging.debug(rsp_data)
        assert rsp_data['sent']
        
        rsp = await client.post(f"{WEB_ADDRESS}/aiohttp/sendSpot", json=payload)
        rsp.raise_for_status()
        rsp_data = json.loads(rsp.text)
        logging.debug(rsp_data)
        assert not rsp_data['sent']
        assert rsp_data['secondsLeft'] > 0

        await asyncio.sleep(rsp_data['secondsLeft'])
        
        rsp = await client.post(f"{WEB_ADDRESS}/aiohttp/sendSpot", json=payload)
        rsp.raise_for_status()
        rsp_data = json.loads(rsp.text)
        logging.debug(rsp_data)
        assert rsp_data['sent']

@pytest.mark.asyncio
async def test_invalid_callsign():
    async with httpx.AsyncClient() as client:
        payload = {
                "cs": SPOT_CALLSIGN,
                "userCS": "RRRRRRRRRRRRRRRRRR",
                "freq": FREQ,
                "info": "www.TNXQSO.com"
                }
        
        rsp = await client.post(f"{WEB_ADDRESS}/aiohttp/sendSpot", json=payload,
                timeout=30)
        rsp.raise_for_status()
        rsp_data = json.loads(rsp.text)
        logging.debug(rsp_data)
        assert not rsp_data['sent']
        assert rsp_data['reply']
