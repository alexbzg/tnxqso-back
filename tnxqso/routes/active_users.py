#!/usr/bin/python3
#coding=utf-8
import json
from datetime import datetime
import os

from aiohttp import web

from tnxqso.common import WEB_ROOT, loadJSON
from tnxqso.services.auth import auth
from tnxqso.services.station_dir import get_station_path

ACTIVE_USERS_ROUTES = web.RouteTableDef()

@ACTIVE_USERS_ROUTES.post('/aiohttp/activeUsers')
@auth()
async def active_users_handler(data, *, callsign, **_):
    if not data.get('chat_callsign'):
        return web.Response(text = 'OK')
    station = data['station'] if 'station' in data else None
    if station and not os.path.exists(get_station_path(data['station']) +
        '/settings.json'):
        return web.HTTPBadRequest(text = 'This station was deleted or moved')
    au_path = WEB_ROOT + '/js/activeUsers.json'
    au_data = loadJSON(au_path) or {}
    now_ts = int(datetime.now().timestamp())
    au_data = {key: val for key, val in au_data.items() if now_ts - val['ts'] < 120}
    au_data[callsign] = {
            'chat': data.get('chat'),
            'ts': now_ts,
            'station': station,
            'callsign': callsign,
            'pm_enabled': data.get('pm_enabled'),
            'chat_callsign': data.get('chat_callsign'),
            'name': data.get('name'),
            'typing': data.get('typing')
            }
    with open(au_path, 'w') as f_au:
        json.dump(au_data, f_au, ensure_ascii = False)
    return web.Response(text = 'OK')
