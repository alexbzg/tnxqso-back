#!/usr/bin/python3
#coding=utf-8

import json
import os
import pathlib
import re
import shutil

from aiohttp import web

from tnxqso.common import WEB_ROOT, DEF_USER_SETTINGS, loadJSON
from tnxqso.db import DB

JSON_TEMPLATES = {'settings': DEF_USER_SETTINGS,
    'log': [], 'chat': [], 'news': [], 'cluster': [], 'status': {} }

RE_STRIP_CALLSIGN = re.compile(r"\d?[a-z]+\d+[a-z]+")

def strip_callsign(callsign):
    """remove prefixes/suffixes from callsign"""
    cs_match = RE_STRIP_CALLSIGN.search(callsign.lower())
    return cs_match.group(0) if cs_match else None

def get_station_path(callsign):
    return f"{WEB_ROOT}/stations/{strip_callsign(callsign.lower())}"

async def get_station_path_by_admin_cs(admin_cs):
    station_cs = await DB.get_station_callsign(admin_cs)
    return get_station_path(station_cs)

async def update_station_settings(callsign, settings):
    old_data = await DB.get_user_data(callsign)
    station_callsign = old_data['settings']['station']['callsign']
    station_path = get_station_path(station_callsign) if station_callsign else None
    publish_path = WEB_ROOT + '/js/publish.json'
    publish = loadJSON(publish_path) or {}
    new_station_callsign = settings['station']['callsign']
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
        publish[station_callsign]['user'] = settings['publish']
    with open(publish_path, 'w') as f_publish:
        json.dump(publish, f_publish, ensure_ascii = False)
    if station_path:
        if not os.path.exists(station_path):
            create_station_dir(station_path)
    await save_station_settings(callsign, settings)

async def save_station_settings(admin_callsign, settings):
    settings['admin'] = admin_callsign
    settings['initialized'] = True
    await DB.param_update('users', {'callsign': admin_callsign},
            {'settings': json.dumps(settings)})
    await write_station_file(admin_callsign, 'settings.json', settings)

async def write_station_file(admin_callsign, file_name, contents, *, binary=False):
    station_path = await get_station_path_by_admin_cs(admin_callsign)
    if station_path and os.path.exists(station_path):
        with open(f'{station_path}/{file_name}', f'w{"b" if binary else""}') as f_dest:
            if file_name.endswith('json'):
                contents = json.dumps(contents, ensure_ascii=False)
            f_dest.write(contents)

def create_station_dir(path):
    if not os.path.exists(path):
        os.makedirs(path)
    for key, val in JSON_TEMPLATES.items():
        with open(f'{path}/{key}.json', 'w') as file:
            json.dump(val, file, ensure_ascii = False)

async def delete_blog_entry(entry, station_path):
    if entry['file']:
        if os.path.isfile(f"{station_path}/{entry['file']}"):
            os.unlink(f"{station_path}/{entry['file']}")
        if os.path.isfile(f"{station_path}/{entry['file_thumb']}"):
            os.unlink(f"{station_path}/{entry['file_thumb']}")
    await DB.execute("""
        delete from blog_entries
        where id = %(id)s""", entry)

def get_gallery_size(station_path):
    gallery_path = f"{station_path}/gallery"
    if not os.path.isdir(gallery_path):
        return 0
    return sum(os.path.getsize(str(file_path))
            for file_path in pathlib.Path(gallery_path).iterdir()
            if file_path.is_file() and '_thumb' not in file_path.name)
