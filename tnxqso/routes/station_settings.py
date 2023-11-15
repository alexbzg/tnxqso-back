import os
import shutil
import json
import time
import base64
import zipfile
import io

from aiohttp import web

from tnxqso.services.auth import auth, extract_callsign
from tnxqso.db import DB, splice_params
from tnxqso.common import WEB_ROOT, loadJSON
from tnxqso.services.station_dir import (get_station_path, save_station_settings,
        create_station_dir, get_station_path_by_admin_cs)

STATION_SETTINGS_ROUTES = web.RouteTableDef()

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

@STATION_SETTINGS_ROUTES.post('/aiohttp/station/settings')
@auth(require_email_confirmed=True)
async def user_settings_handler(data, *, callsign, **_):
    if 'settings' in data:
        old_data = await DB.get_user_data(callsign)
        station_callsign = old_data['settings']['station']['callsign']
        station_path = get_station_path(station_callsign) if station_callsign else None
        publish_path = WEB_ROOT + '/js/publish.json'
        publish = loadJSON(publish_path) or {}
        new_station_callsign = data['settings']['station']['callsign']
        if station_callsign != new_station_callsign:
            new_path = get_station_path(new_station_callsign) if new_station_callsign else None
            if new_path != station_path:
                if new_path:
                    if os.path.exists(new_path):
                        return web.HTTPBadRequest(text=
                            f'Station callsign {new_station_callsign.upper()}' +
                                'is already registered')
                    create_station_dir(new_path)
                    if station_path and os.path.exists(f"{station_path}/gallery"):
                        os.rename(f"{station_path}/gallery", f"{new_path}/gallery")
                    if station_path and os.path.exists(f"{station_path}/chat.json"):
                        os.rename(f"{station_path}/chat.json", f"{new_path}/chat.json")
                else:
                    await DB.execute(
                        'delete from blog_entries where "user" = %(callsign)s',
                        {'callsign': callsign})
                if station_path and os.path.exists(station_path):
                    shutil.rmtree(station_path)
                await DB.execute(
                    "delete from visitors where station = %(callsign)s",
                    {'callsign': station_callsign})
            if station_callsign and station_callsign in publish:
                if new_station_callsign:
                    publish[new_station_callsign] = publish[station_callsign]
                del publish[station_callsign]
            station_path = new_path
            station_callsign = new_station_callsign
        if station_callsign:
            if not station_callsign in publish:
                publish[station_callsign] = {'admin': True}
            publish[station_callsign]['user'] = data['settings']['publish']
        with open(publish_path, 'w') as f_publish:
            json.dump(publish, f_publish, ensure_ascii = False)
        if station_path:
            if not os.path.exists(station_path):
                create_station_dir(station_path)
        await save_station_settings(station_callsign, callsign, data['settings'])
    elif 'userColumns' in data:
        user_data = await DB.get_user_data(callsign)
        settings = user_data['settings']
        user_columns = settings['userFields']
        for col in range(0, len(data['userColumns'])):
            if len(settings) <= col:
                user_columns.append(data['userColumns'][col])
            else:
                user_columns[col] = data['userColumns'][col]
        user_columns = user_columns[:len(data['userColumns'])]
        await save_station_settings(user_data['settings']['station']['callsign'],
                callsign, settings)
    elif 'manualStats' in data:
        user_data = await DB.get_user_data(callsign)
        station_callsign = user_data['settings']['station']['callsign']
        station_path = get_station_path(station_callsign) if station_callsign else None
        if station_path:
            with open(station_path + '/manualStats.json', 'w') as f_manual_stats:
                json.dump(data['manualStats'], f_manual_stats, ensure_ascii = False)
    else:
        if data.get('chat_callsign') and len(data['chat_callsign']) < 3:
            raise web.HTTPBadRequest(text='Chat callsign should have 3 or more characters.')
        await DB.param_update('users', {'callsign': callsign},
            splice_params(data, ('email', 'password', 'name', 'chat_callsign', 'pm_enabled')))
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
