#!/usr/bin/python3
#coding=utf-8
import time
import json
from datetime import datetime

from tnxqso.common import CONF, loadJSON, dtFmt, WEB_ROOT
from tnxqso.services.station_dir import get_station_path
from tnxqso.services.auth import SITE_ADMINS
from tnxqso.services.rabbitmq import rabbitmq_publish
from tnxqso.db import DB

CHAT_MAX_LENGTH = int(CONF['chat']['max_length'])

async def insert_chat_message(data, callsign, request, force_admin=False):
    station = data['station'] if 'station' in data else None
    if station:
        station_path = get_station_path(data['station'])
        chat_path = station_path + '/chat.json'
        if force_admin:
            admin = True
        else:
            station_settings = loadJSON(station_path + '/settings.json')
            admins = [x.lower() for x in\
                station_settings['chatAdmins'] + [ station_settings['admin'], ]]
            admin = callsign in admins
            chat_access = station_settings.get('chatAccess')
            if chat_access == 'admins' and not admin:
                raise web.HTTPUnauthorized(text='Station admin required')
            if await DB.execute("""
                select true from user_bans
                where admin_callsign = %(admin)s and banned_callsign = %(banned)s""",
                {'admin': station_settings['admin'], 'banned': callsign}):
                raise web.HTTPUnauthorized(text='Your account is set read-only in this chat')
    else:
        chat_path = WEB_ROOT + '/js/talks.json'
        admin = callsign in SITE_ADMINS
    data['cs'] = callsign

    chat = loadJSON(chat_path) or []
    msg = {'user': data['from'],
            'text': data['text'],
            'cs': data.get('cs') or data['from'],
            'admin': admin,
            'ts': time.time()}
    msg['date'], msg['time'] = dtFmt(datetime.utcnow())
    if 'name' in data:
        msg['name'] = data['name']
    chat.insert(0, msg)
    chat_trunc = []
    chat_adm = []
    for _msg in chat:
        if _msg['text'].startswith('***') and _msg['admin']:
            chat_adm.append(_msg)
        elif len(chat_trunc) < CHAT_MAX_LENGTH:
            chat_trunc.append(_msg)
    chat = chat_adm + chat_trunc
    with open(chat_path, 'w') as f_chat:
        json.dump(chat, f_chat, ensure_ascii = False)
    if request.app.get('rabbitmq') and request.app['rabbitmq']['exchanges'].get('chats'):
        await rabbitmq_publish(request.app['rabbitmq']['exchanges']['chats'],
                key=station if station else 'talks', message=msg)
