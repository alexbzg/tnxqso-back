#!/usr/bin/python
#coding=utf-8

import json
import logging
import asyncio
from datetime import datetime, timedelta

import httpx
import pytest

from tnxqso.common import WEB_ADDRESS, tzOffset
from tnxqso.services.station_dir import read_station_file

TEST_USER1_CALLSIGN = "qq3qq"

@pytest.mark.asyncio
async def test_valid_qso(tnxqso_request, user_login):
    TEST_USER1_TOKEN = (await user_login(TEST_USER1_CALLSIGN))['token']
    qso = {
        "myCS": "R6TEST",
        "band": "14",
        "freq": "14000.0",
        "mode": "CW",
        "cs": "CC1CC",
        "snt": "599",
        "rcv": "599",
        "no": 1120,
        "loc": None,
        "comments": "",
        "qth": ["", "", ""],
        "loc_rcv": None,
        "sound": None,
        "date": "07 nov",
        "time": "06:47z",
        "ts": "2023-12-06 16:57:10"
    }
    log_rsp = await tnxqso_request('aiohttp/log',
            json={'token': TEST_USER1_TOKEN,
                'qso': [qso]
                })
    log_rsp.raise_for_status()
    log = await read_station_file(TEST_USER1_CALLSIGN, 'log.json')
    qso_datetime = datetime.strptime(qso['ts'], "%Y-%m-%d %H:%M:%S")
    assert log[0]['qso_ts'] == (qso_datetime - datetime(1970, 1, 1)) / timedelta(seconds=1)
    status = await read_station_file(TEST_USER1_CALLSIGN, 'status.json')
    assert status['freq']['value'] == qso['freq']
    assert abs(status['freq']['ts'] - (qso_datetime.timestamp() + tzOffset())) < 1


