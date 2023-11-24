import os
import json
import time
import base64
import zipfile
import io

from aiohttp import web

from tnxqso.services.auth import auth, extract_callsign
from tnxqso.db import DB
from tnxqso.services.station_dir import write_station_file, get_station_path_by_admin_cs

STATION_SETTINGS_ROUTES = web.RouteTableDef()
STATION_FILES = {
      'manualStats': {
            'file_name': 'manualStats.json'
            },
      'stationInfo': {
            'file_name': 'stationInfo.html'
            },
      'donateText': {
            'file_name': 'donateText.html'
            },
      'donateCode': {
            'file_name': 'donateCode.html'
            }
      }

async def check_station_chat_admin(station_admin, callsign):
    station_chat_admins = (await DB.get_user_data(station_admin)
            )['settings'].get('chatAdmins', [])
    if callsign not in station_chat_admins:
        raise web.HTTPUnauthorized(text="Permission denied")

@STATION_SETTINGS_ROUTES.post('/aiohttp/station/banlist')
@auth()
async def station_user_ban_post_handler(data, *, callsign, **_):
    if callsign != data['stationAdmin']:
        await check_station_chat_admin(data['stationAdmin'], callsign)
    await DB.execute("""
        insert into user_bans (admin_callsign, banned_callsign)
        values (%(admin)s, %(banned)s)
        """, {'admin': data['stationAdmin'], 'banned': data['banned']})
    return web.Response(text = 'OK')

@STATION_SETTINGS_ROUTES.delete('/aiohttp/station/banlist')
@auth()
async def station_user_ban_delete_handler(data, *, callsign, **_):
    if callsign != data['stationAdmin']:
        await check_station_chat_admin(data['stationAdmin'], callsign)
    await DB.execute("""
        delete from user_bans
        where admin_callsign = %(admin)s and  banned_callsign = %(banned)s
        """, {'admin': data['stationAdmin'], 'banned': data['banned']})
    return web.Response(text = 'OK')

@STATION_SETTINGS_ROUTES.get('/aiohttp/station/{callsign}/banlist')
async def station_user_ban_list_handler(request):
    admin_callsign = extract_callsign(request)
    rsp_data = (await DB.execute("""
        select users.callsign, chat_callsign 
        from user_bans join users on banned_callsign = users.callsign
        where admin_callsign = %(admin_callsign)s
        """, {'admin_callsign': admin_callsign},
        container="list")) or []
    return web.json_response(rsp_data)

@STATION_SETTINGS_ROUTES.post('/aiohttp/station/file')
@auth(require_email_confirmed=True)
async def station_file_post_handler(data, *, callsign, **_):
    for key in data:
        if key in STATION_FILES:
            await write_station_file(callsign, STATION_FILES[key]['file_name'], data[key],
                    binary=STATION_FILES['key'].get('binary'))
    return web.Response(text = 'OK')

@STATION_SETTINGS_ROUTES.post('/aiohttp/station/track')
@auth(require_email_confirmed=True)
async def track_handler(data, *, callsign, **_):
    station_path = await get_station_path_by_admin_cs(callsign)
    track_json_path = station_path + '/track.json'
    track_json = {'version': time.time(), 'file': 'track.xml'}
    if 'file' in data:
        track_json['filename'] = data['name']
        file = base64.b64decode(data['file'].split(',')[1])
        if data['name'].lower().endswith('kmz'):
            with zipfile.ZipFile(io.BytesIO(file), 'r') as z_file:
                for f_item in z_file.infolist():
                    if f_item.filename.endswith('kml'):
                        track_json['file'] = f_item.filename
                    z_file.extract(f_item, path = station_path)
        else:
            with open(station_path + '/track.xml', 'wb') as f_track:
                f_track.write(file)
        with open(track_json_path, 'w') as f_json_path:
            json.dump(track_json, f_json_path)
    if 'clear' in data and os.path.isfile(track_json_path):
        os.remove(track_json_path)
    return web.Response(text = 'OK')
