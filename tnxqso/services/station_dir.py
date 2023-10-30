#!/usr/bin/python3
#coding=utf-8

import json
import os

from tnxqso.common import WEB_ROOT, DEF_USER_SETTINGS
from tnxqso.db import DB

JSON_TEMPLATES = {'settings': DEF_USER_SETTINGS,
    'log': [], 'chat': [], 'news': [], 'cluster': [], 'status': {} }

def get_station_path(callsign):
    return WEB_ROOT + '/stations/' + callsign.lower().replace('/', '-')

async def get_station_path_by_admin_cs(admin_cs):
    station_cs = await DB.get_station_callsign(admin_cs)
    return get_station_path(station_cs)

async def save_station_settings(station_callsign, admin_callsign, settings):
    settings['admin'] = admin_callsign
    settings['initialized'] = True
    await DB.param_update('users', {'callsign': admin_callsign}, \
        {'settings': json.dumps(settings)})
    if station_callsign:
        station_path = get_station_path(station_callsign)
        if station_path:
            with open(station_path + '/settings.json', 'w') as f_settings:
                json.dump(settings, f_settings, ensure_ascii = False)

def create_station_dir(path):
    if not os.path.exists(path):
        os.makedirs(path)
    for key, val in JSON_TEMPLATES.items():
        with open(f'{path}/{key}.json', 'w') as file:
            json.dump(val, file, ensure_ascii = False)
