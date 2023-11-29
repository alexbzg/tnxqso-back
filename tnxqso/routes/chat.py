#!/usr/bin/python3
#coding=utf-8
import json

from aiohttp import web

from tnxqso.common import WEB_ROOT, loadJSON
from tnxqso.db import DB
from tnxqso.services.auth import auth, SITE_ADMINS
from tnxqso.services.station_dir import get_station_path
from tnxqso.services.chat import insert_chat_message

CHAT_ROUTES = web.RouteTableDef()

def replace0(val):
    return val.replace("0", "\u00D8")

@CHAT_ROUTES.delete('/aiohttp/chat')
@auth(require_email_confirmed=True)
async def chat_delete_handler(data, *, callsign, **_):
    station = data['station'] if 'station' in data else None
    admins = SITE_ADMINS
    chat_path = None
    if station:
        station_path = get_station_path(data['station'])
        station_settings = loadJSON(station_path + '/settings.json')
        admins = set(admins)
        admins.update([x.lower() for x in\
            station_settings['chatAdmins'] + 
            [station_settings['admin']]])
        chat_path = station_path + '/chat.json'
    else:
        chat_path = WEB_ROOT + '/js/talks.json'
    chat = []
    if 'ts' in data:
        chat = loadJSON(chat_path) or []
        if not callsign in admins:
            message = [ x for x in chat if x['ts'] == data['ts'] ]
            if message:
                message = message[0]
            else:
                return web.HTTPNotFound(text='Message not found')
            if message['cs'] != callsign:
                raise web.HTTPUnauthorized(text='You must be logged in as station or site admin ')
        chat = [ x for x in chat if x['ts'] != data['ts'] ]
    else:
        if not callsign in admins:
            raise web.HTTPUnauthorized(text='You must be logged in as station or site admin')
        if data.get('keepPinned'):
            chat = loadJSON(chat_path) or []
            chat = [m for m in chat if m['admin'] and m['text'].startswith('***')]
    with open(chat_path, 'w') as f_chat:
        json.dump(chat, f_chat, ensure_ascii = False)
    return web.Response(text = 'OK')

@CHAT_ROUTES.post('/aiohttp/chat')
@auth(require_email_confirmed=True)
async def chat_post_handler(data, *, callsign, request, **_):
    await insert_chat_message(data, callsign, request)
    return web.Response(text = 'OK')
