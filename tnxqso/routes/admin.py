#!/usr/bin/python3
#coding=utf-8

import json

from aiohttp import web

from tnxqso.common import WEB_ROOT, loadJSON
from tnxqso.db import DB, splice_params
from tnxqso.services.auth import auth, BANLIST
from tnxqso.services.station_dir import save_station_settings, get_station_path

ADMIN_ROUTES = web.RouteTableDef()

@ADMIN_ROUTES.get('/aiohttp/suspicious')
async def suspicious_handler(_):
    return web.json_response(await DB.execute("""
            select callsign, email, chat_callsign 
            from users
            where chat_callsign is not null and chat_callsign not in ('', upper(callsign))
            """))

@ADMIN_ROUTES.post('/aiohttp/publish')
@auth(require_admin=True)
async def publish_handler(data, _):
    publish_path = WEB_ROOT + '/js/publish.json'
    publish = loadJSON(publish_path) or {}
    publish[data['station']] = data['publish']
    with open(publish_path, 'w') as f_publish:
        json.dump(publish, f_publish, ensure_ascii = False)
    station_path = get_station_path(data['station'])
    station_settings = loadJSON(station_path + '/settings.json')
    station_settings['publish'] = data['publish']['user']
    await save_station_settings(data['station'], station_settings['admin'],
            station_settings)
    return web.Response(text = 'OK')

@ADMIN_ROUTES.post('/aiohttp/banUser')
@auth(require_admin=True)
async def ban_user_handler(data, **_):
    user_data = await DB.get_user_data(data['user'])
    if not user_data:
        return web.HTTPNotFound(text='User not found')
    alt_logins = await DB.execute(
            """select callsign
                from users
                where email = %(email)s and callsign <> %(callsign)s""", user_data)
    if alt_logins:
        if isinstance(alt_logins, dict):
            user_data['alts'] = [alt_logins['callsign']]
        else:
            user_data['alts'] = [row['callsign'] for row in alt_logins]
    else:
        user_data['alts'] = []
    if 'query' in data:
        return web.json_response({
            'login': user_data['callsign'],
            'email': user_data['email'],
            'alts': user_data['alts']
            })
    if data.get('unban'):
        if user_data['callsign'] in BANLIST['callsigns']:
            BANLIST['callsigns'].remove(user_data['callsign'])
        if 'alts' in user_data:
            for alt in user_data['alts']:
                if alt in BANLIST['callsigns']:
                    BANLIST['callsigns'].remove(alt)
        if user_data['email'] in BANLIST['emails']:
            BANLIST['emails'].remove(user_data['email'])
    else:
        if user_data['callsign'] not in BANLIST['callsigns']:
            BANLIST['callsigns'].append(user_data['callsign'])
        if 'alts' in user_data:
            for alt in user_data['alts']:
                if alt not in BANLIST['callsigns']:
                    BANLIST['callsigns'].append(alt)
        if user_data['email'] not in BANLIST['emails']:
            BANLIST['emails'].append(user_data['email'])
    with open(WEB_ROOT + '/js/banlist.json', 'w') as f_bl:
        json.dump(BANLIST, f_bl)
    return web.Response(text='OK')

@ADMIN_ROUTES.post('/aiohttp/users')
@auth(require_admin=True)
async def users_list_handler(data, **_):
    where_clause = 'where email not in %(banned)s'
    params = {'banned': tuple(BANLIST['emails'])}
    if data.get('filter') == 'new':
        where_clause += ' and not verified'
    elif data.get('filter') == 'no_chatcall':
        where_clause += " and (chat_callsign is null or chat_callsign = '')"
    elif data.get('filter') == 'banned':
        where_clause = "where email in %(banned)s"
    ulist = await DB.execute(f"""
        select callsign, email, email_confirmed, verified, chat_callsign, name
            from users {where_clause} 
            order by callsign""", params)
    if not ulist:
        ulist = []
    if isinstance(ulist, dict):
        ulist = [ulist,]
    for user in ulist:
        user['banned'] = data.get('filter') == 'banned' or user['email'] in BANLIST['emails']
    return web.json_response(ulist)

@ADMIN_ROUTES.post('/aiohttp/editUser')
@auth(require_admin=True)
async def user_edit_handler(data, **_):
    await DB.param_update('users', {'callsign': data['callsign']},
            splice_params(data, ['verified', 'email_confirmed']))
    return web.Response(text = 'OK')
