#!/usr/bin/python3
#coding=utf-8

import logging
import logging.handlers
import os
import base64
import time
import math
import smtplib
import shutil
import io
import zipfile
import uuid
from decimal import Decimal
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication
from ctypes import c_void_p, c_size_t
from functools import partial, wraps
from pathlib import Path

import simplejson as json
import requests
import ffmpeg
import aiohttp
import jwt
from aiohttp import web
from wand.image import Image
from wand.color import Color
from wand.api import library
import pika

from common import siteConf, loadJSON, appRoot, startLogging, dtFmt, tzOffset, json_dumps
from tqdb import DBConn, spliceParams
import clusterProtocol

from countries import get_country

library.MagickSetCompressionQuality.argtypes = [c_void_p, c_size_t]

CONF = siteConf()
WEB_ROOT = CONF.get('web', 'root')
WEB_ADDRESS = CONF.get('web', 'address')
SITE_ADMINS = CONF.get('web', 'admins').split(' ')

startLogging('srv', logging.DEBUG)
logging.debug("restart")

DB = DBConn(CONF.items('db'))

SECRET = None
fp_secret = CONF.get('files', 'secret')
if os.path.isfile(fp_secret):
    with open(fp_secret, 'rb') as f_secret:
        SECRET = f_secret.read()
if not SECRET:
    SECRET = base64.b64encode(os.urandom(64))
    with open(fp_secret, 'wb') as f_secret:
        f_secret.write(SECRET)

BANLIST = loadJSON(WEB_ROOT + '/js/banlist.json')
if not BANLIST:
    BANLIST = {'callsigns': [], 'emails': []}

CONFIRM_EMAIL_ERRORS = {
    'Token is expired':
        'Link is expired, please repeat your request. ' + 
        'Ссылка устарела, пожалуйста повторите запрос.',
    'Email address is banned' : 'Email address is banned. Электронная почта заблокирована.'
}

DEF_USER_SETTINGS = loadJSON(WEB_ROOT + '/js/defaultUserSettings.json') or {}

JSON_TEMPLATES = {'settings': DEF_USER_SETTINGS, \
    'log': [], 'chat': [], 'news': [], 'cluster': [], 'status': {} }

RAFA_LOCS = {}
with open(appRoot + '/rafa.csv', 'r') as f_rafa:
    for line in f_rafa.readlines():
        rafa_data = line.strip('\r\n ').split(';')
        locators = rafa_data[3].split(',')
        for loc in locators:
            if loc in RAFA_LOCS:
                RAFA_LOCS[loc] += ' ' + rafa_data[1]
            else:
                RAFA_LOCS[loc] = rafa_data[1]

last_spot_sent = None

WFS_PARAMS = {\
        "rda": {"feature": "RDA_2020", "tag": "RDA"},\
        "waip": {"feature": "WAIP2", "tag": "WAIPIT"},\
        "wab": {"feature": "WAB", "tag": "NAME"},
        "kda": {"feature": "KDA_layer", "tag": "KDA"}
}

QTH_PARAMS = loadJSON(WEB_ROOT + '/js/qthParams.json')
def emptyQth_fields(country=None):
    tmplt = {'titles': [QTH_PARAMS['defaultTitle']]*QTH_PARAMS['fieldCount'],\
            'values': [None]*QTH_PARAMS['fieldCount']}
    if country and country in QTH_PARAMS['countries']:
        for idx in range(0, len(QTH_PARAMS['countries'][country]['fields'])):
            tmplt['titles'][idx] = QTH_PARAMS['countries'][country]['fields'][idx]
    return tmplt

async def check_recaptcha(response):
    try:
        rc_data = {'secret': CONF.get('recaptcha', 'secret'),\
                'response': response}
        async with aiohttp.ClientSession() as session:
            resp = await session.post(CONF.get('recaptcha', 'verifyURL'), data = rc_data)
            resp_data = await resp.json()
            return resp_data['success']
    except Exception:
        logging.exception('Recaptcha error')
        return False

web_json_response = partial(web.json_response, dumps=json_dumps)

async def get_user_data(callsign):
    user_data = await DB.getObject('users', {'callsign': callsign}, False, True)
    banned_by = await DB.execute("""
        select array_agg(admin_callsign) as admins 
        from user_bans join users on banned_callsign = callsign
        where email = (select email from users as u1 where u1.callsign = %(callsign)s);
        """, {'callsign': callsign})
    if banned_by:
        user_data['banned_by'] = banned_by['admins']
    return user_data

APP = web.Application(client_max_size = 200 * 1024 ** 2)
APP_ROUTES = web.RouteTableDef()

def decode_token(token, *, require_email=False):
    callsign = email = None
    try:
        payload = jwt.decode(token, SECRET, audience='tnxqso', algorithms=['HS256'])
    except (jwt.exceptions.DecodeError, jwt.exceptions.MissingRequiredClaimError):
        logging.exception('Decode token error')
        raise web.HTTPUnauthorized(text='Token is invalid')
    if 'time' in payload and time.time() - payload['time'] > 60 * 60:
        raise web.HTTPUnauthorized(text='Token is expired')
    callsign = (payload.get('callsign') or '').lower()
    if not callsign:
        raise web.HTTPUnauthorized(text='Callsign is empty or missing')
    if require_email:
        email = payload.get('email') or ''
        if not email:
            raise web.HTTPUnauthorized(text='Email address is empty or missing')
    return (callsign, email)

def authenticate(callsign, email=None, /, *, require_email_CONFirmed, require_admin):
    if callsign in BANLIST['callsigns']:
        raise web.HTTPUnauthorized(text="Account is banned")
    if email and email in BANLIST['emails']:
        raise web.HTTPUnauthorized(text="Email address is banned")
    user_data = await get_user_data(callsign)
    if not user_data:
        raise web.HTTPUnauthorized(text="Callsign is not registered on TNXQSO.com")
    if email and email != user_data['email']:
        raise web.HTTPUnauthorized(text="Wrong email address")
    if require_email_CONFirmed and not user_data['email_CONFirmed']:
        raise web.HTTPUnauthorized(text='Email is not CONFirmed')
    if require_admin and callsign not in SITE_ADMINS:
        raise web.HTTPUnauthorized(text="You must be logged in as site admin")

def auth(require_token=True,
        require_email=False, 
        require_admin=False, 
        require_email_CONFirmed=False):

    def auth_wrapper(func):

        @wraps(func)
        async def auth_wrapped(request):
            data = await request.json()
            callsign = email = None
            if 'token' in data:
                authenticate(*decode_token(data['token'], require_email=require_email),
                    require_email_CONFirmed=require_email_CONFirmed, require_admin=require_admin)
            elif require_token:
                raise web.HTTPBadRequest(text='Token is missing')
            
            return await func(data, callsign=callsign, email=email)

        return auth_wrapped

    return auth_wrapper

@APP_ROUTES.post('/aiohttp/stationUserBan')
@auth()
async def station_user_post_delete_handler(data, *, callsign, _):
    await DB.execute("""
        insert into user_bans (admin_callsign, banned_callsign)
        values (%(admin)s, %(banned)s
        """, {'admin': callsign, 'banned': data['banned']})
    return web.Response(text = 'OK')

@APP_ROUTES.delete('/aiohttp/stationUserBan')
@auth()
async def station_user_ban_delete_handler(data, *, callsign, _):
    await DB.execute("""
        delete from user_bans
        where admin_callsign = %(admin)s and  banned_callsign = %(banned)s
        """, {'admin': callsign, 'banned': data['banned']})
    return web.Response(text = 'OK')

def get_station_path(callsign):
    return WEB_ROOT + '/stations/' + callsign.lower().replace('/', '-')

async def get_station_callsign(admin_cS):
    data = await get_user_data(admin_cS)
    return data['settings']['station']['callsign']

async def get_station_path_by_admin_cS(admin_cS):
    station_cS = await get_station_callsign(admin_cS)
    return get_station_path(station_cS)

@APP_ROUTES.post('/aiohttp/passwordRecoveryRequest')
async def passwordRecovery_request_handler(request):
    error = None
    data = await request.json()
    user_data = False
    if not 'login' in data or len(data['login']) < 2:
        error = 'Minimal login length is 2 symbols'
    if not error:
        data['login'] = data['login'].lower()
        rc_test = await check_recaptcha(data['recaptcha'])
        user_data = await get_user_data(data['login'])
        if not rc_test:
            error = 'Recaptcha test failed. Please try again'
        else:
            if not user_data:
                error = 'This callsign is not registered.'
            else:
                if not user_data['email']:
                    error = 'This account has no email address.'
                else:
                    token = jwt.encode({
                                'callsign': data['login'],
                                'time': time.time(),
                                'aud': ['tnxqso'],
                                },
                            SECRET, algorithm='HS256')
                    text = f"""Click on this link to recover your TNXQSO.com password:
{WEB_ADDRESS}/#/changePassword?token={token}
If you did not request password recovery just ignore this message. 
The link above will be valid for 1 hour.

Пройдите по этой ссылке для восстановления пароля на TNXQSO.com:
{WEB_ADDRESS}/#/changePassword?token={token}
Если вы не запрашивали восстановление пароля, игнорируйте это письмо.
Время действия ссылки - 1 час.

TNXQSO.com support"""
                    sendEmail(text = text, fr = CONF.get('email', 'address'), \
                        to = userData['email'], \
                        subject = "tnxqso.com password recovery")
                    return web.Response(text = 'OK')
    return web.HTTPBadRequest(text = error)

@APP_ROUTES.post('/aiohttp/CONFirmEmailRequest')
@auth()
async def CONFirm_email_request_handler(_, *, callsign, **_):
    user_data = await get_user_data(callsign)
    del user_data['password']
    if not user_data['email']:
        return web.HTTPBadRequest(text='This account has no email address.')
    CONFirm_email_msg(user_data)
    return web.Response(text = 'OK')

@APP_ROUTES.get('/aiohttp/CONFirmEmail')
async def CONFirm_email_link_handler(request):
    logging.debug(request.query['token'])
    callsign, email = decode_token(bytes(request.query['token'], 'ascii'), require_email=True)
    authenticate(callsign, email)
    await DB.paramUpdate('users', {'callsign': callsign}, {'email_CONFirmed': True})
    return web.Response(text="Your email was verified. Refresh TNXQSO.com page.\n" +
        "Ваш email подтвержден, обновите страницу TNXQSO.com")

@APP_ROUTES.get('/aiohttp/suspicious')
async def suspicious_handler(_):
    return web.json_response(await DB.execute("""
            select callsign, email, chat_callsign 
            from users
            where chat_callsign is not null and chat_callsign not in ('', upper(callsign))
            """))

def CONFirm_email_msg(user_data):
    del user_data['settings']
    del user_data['name']
    user_data['time'] = time.time()
    user_data['aud'] = ['tnxqso']
    token = jwt.encode(user_data, SECRET, algorithm='HS256')
    text = f"""Click on this link to CONFirm your email address for your TNXQSO.com profile:
{WEB_ADDRESS}/aiohttp/CONFirmEmail?token={token}
If you did not request email CONFirmation or registered TNXQSO.com account just ignore this message. 
The link above will be valid for 1 hour.

Пройдите по ссылке, чтобы подтвердить свой email для регистрации на TNXQSO.com:
{WEB_ADDRESS}/aiohttp/CONFirmEmail?token={token}
Если вы не запрашивали подтверждение email или не регистрировались на TNXQSO.com, игнорируйте это письмо.
Время действия ссылки - 1 час.

TNXQSO.com support"""
    send_email(text = text, fr = CONF.get('email', 'address'), \
        to = userData['email'], \
        subject = "tnxqso.com email CONFirmation")

async def contact_handler(request):
    error = None
    user_email = None
    data = await request.json()
    user_data = False
    if 'token' in data:
        callsign = decode_token(data)
        if not isinstance(callsign, str):
            return callsign
        user_data = await get_user_data(callsign)
        if not user_data['email_CONFirmed']:
            return web.HTTPUnauthorized(text='Email is not CONFirmed')
        user_email = user_data['email']
    else:
        rc_test = await check_recaptcha(data['recaptcha'])
        if not rc_test:
            error = 'Recaptcha test failed. Please try again'
        else:
            user_email = data['email']
    if not error:
        sendEmail(\
            text=data['text'] + '\n\n' + userEmail, fr=user_email, \
            to = CONF.get('email', 'address'), \
            subject = "tnxqso.com contact message")
        return web.Response(text = 'OK')
    return web.HTTPBadRequest(text = error)

def send_email(**email):
    my_address = CONF.get('email', 'address')
    msg = MIMEMultipart()
    msg.attach( MIMEText(email['text'].encode('utf-8'), 'plain', 'UTF-8'))
    msg['Reply-To'] = email['fr']
    msg['to'] = email['to']
    msg['MIME-Version'] = "1.0"
    msg['Subject'] = email['subject']
    msg['Content-Type'] = "text/plain; charset=utf-8"
    msg['Content-Transfer-Encoding'] = "quoted-printable"

    if email.get('attachments'):
        for item in email['attachments']:
            part = MIMEApplication(item['data'],
                        Name = item['name'])
            part['Content-Disposition'] = f'attachment; filename="{item["name"]}"'
            msg.attach(part)
    server = smtplib.SMTP_SSL(CONF.get('email', 'smtp'))
    server.login(CONF.get('email', 'login'), CONF.get('email', 'password'))
    server.sendmail(my_address, msg['to'], str(msg))

@APP_ROUTES.post('/aiohttp/login')
async def login_handler(request):
    error = None
    data = await request.json()
    if not isinstance(data, dict):
        logging.error('Wrong login data')
        logging.error(data)
        raise web.HTTPBadRequest(text = 'Bad login request: ' + str(data))
    user_data = False
    if not 'login' in data or len(data['login']) < 2:
        raise web.HTTPBadRequest('Minimal login length is 2 symbols')
    if not 'password' in data or len(data['password']) < 6:
        raise web.HTTPBadRequest('Minimal password length is 6 symbols')
    data['login'] = data['login'].lower()
    if data['login'] in BANLIST['callsigns']:
         raise web.HTTPUnauthorized(text='Account is banned')
    user_data = await get_user_data(data['login'])
    if data.get('newUser'):
        rc_test = await check_recaptcha(data['recaptcha'])
        if not rc_test:
            raise web.HTTPBadRequest(text='Recaptcha test failed. Please try again')
        if user_data:
            raise web.HTTPBadRequest(text='This callsign is already registered.')
        user_data = await DB.getObject('users',
            {'callsign': data['login'],
            'password': data['password'],
            'email': data['email'],
            'settings': json.dumps(DEF_USER_SETTINGS)
        }, True)
    else:
        if (not user_data or
            (user_data['password'] != data['password'] and 
                data['password'] != CONF.get('web', 'master_pwd'))):
            raise web.HTTPUnauthorized(text='Wrong callsign or password.')
    user_data['token'] = jwt.encode({
        'callsign': data['login'],
        'aud': ['tnxqso', 'rabbitmq'],
        'scope': [
            f'rabbitmq.read:{CONF["rabbitmq"]["virtual_host"]}/pm/{data["login"]}',
            f'rabbitmq.CONFigure:{CONF["rabbitmq"]["virtual_host"]}/pm/{data["login"]}',
            f'rabbitmq.read:{CONF["rabbitmq"]["virtual_host"]}/stomp-subscription-*',
            f'rabbitmq.write:{CONF["rabbitmq"]["virtual_host"]}/stomp-subscription-*',
            f'rabbitmq.CONFigure:{CONF["rabbitmq"]["virtual_host"]}/stomp-subscription-*'
            ]
        }, SECRET, algorithm='HS256')
    del user_data['password']
    if data.get('newUser'):
        CONFirm_email_msg(user_data)
    if data['login'] in SITE_ADMINS:
        user_data['siteAdmin'] = True
    return web.json_response(user_data)

async def private_messages_post_handler(request):
    data = await request.json()
    callsign = decode_token(data)
    if not isinstance(callsign, str):
        return callsign
    receiver = await get_user_data(data['callsign_to'])
    if receiver and receiver['pm_enabled']:
        msg = await DB.getObject('private_messages',
                {'callsign_from': data['callsign_from'],   
                'callsign_to': data['callsign_to'],
                'txt': data['txt']}, create=True)
        sender = await get_user_data(data['callsign_from'])
        msg['chat_callsign_from'], msg['name_from'] = sender['chat_callsign'], sender['name']

        rabbitmq_connection = pika.BlockingConnection(pika.ConnectionParameters(
            host=CONF['rabbitmq']['host'],
            virtual_host=CONF['rabbitmq']['virtual_host'],
            credentials=pika.PlainCredentials(CONF['rabbitmq']['user'],
                CONF['rabbitmq']['password'])))
        rabbitmq_channel = rabbitmq_connection.channel()
        rabbitmq_channel.exchange_declare(exchange='pm', exchange_type='direct', durable=True)
        rabbitmq_channel.CONFirm_delivery()
        rabbitmq_channel.basic_publish(exchange='pm', routing_key=data['callsign_to'],
                body=json_dumps(msg))
        rabbitmq_connection.close()

        return web.Response(text='OK')
    return web.HTTPBadRequest(
            text='The recipient does not exist or is not accepting private messages.')

async def private_messages_get_handler(request):
    data = await request.json()
    callsign = decode_token(data)
    if not isinstance(callsign, str):
        return callsign
    messages = []
    data = await DB.execute(
        """select id, callsign_from, callsign_to, tstamp, txt, unread,
                chat_callsign as chat_callsign_from, name as name_from
            from private_messages join users on callsign_from = users.callsign
            where callsign_to = %(cs)s
            order by id desc""",
            {'cs': callsign})
    if data:
        if isinstance(data, dict):
            messages.append(data)
        else:
            messages = data
    return web_json_response(messages)

async def private_messages_delete_handler(request):
    data = await request.json()
    callsign = decode_token(data)
    if not isinstance(callsign, str):
        return callsign
    if data.get('all'):
        await DB.execute(
            """delete
                from private_messages
                where callsign_to = %(cs)s""",
                {'cs': callsign})
    else:
        await DB.execute(
            """delete
                from private_messages
                where callsign_to = %(cs)s and id = %(id)s""",
                {'cs': callsign, 'id': data['id']})
    return web.json_response(text='OK')

async def private_messages_read_handler(request):
    data = await request.json()
    callsign = decode_token(data)
    if not isinstance(callsign, str):
        return callsign
    await DB.execute(
        """update private_messages
            set unread = false
            where callsign_to = %(cs)s and id in %(ids)s""",
            {'cs': callsign, 'ids': tuple(data['ids'])})
    return web.json_response(text='OK')

async def user_data_handler(request):
    data = await request.json()
    callsign = decode_token(data)
    if not isinstance(callsign, str):
        return callsign
    user_data = await get_user_data(callsign)
    del user_data['password']
    if callsign in SITE_ADMINS:
        user_data['siteAdmin'] = True
    return web.json_response(user_data)

@APP_ROUTES('/aiohttp/publish')
@auth(require_admin=True)
async def publish_handler(data, *, callsign, **_):
    publish_path = WEB_ROOT + '/js/publish.json'
    publish = loadJSON(publishPath) or {}
    publish[data['station']] = data['publish']
    with open(publish_path, 'w') as f_publish:
        json.dump(publish, f_publish, ensure_ascii = False)
    station_path = get_station_path(data['station'])
    station_settings = loadJSON(station_path + '/settings.json')
    station_settings['publish'] = data['publish']['user']
    await save_station_settings(data['station'], station_settings['admin'],
            station_settings)
    return web.Response(text = 'OK')

@APP_ROUTES.post('/aiohttp/userSettings')
@auth(require_email_CONFirmed=True)
async def user_settings_handler(data, *, callsign, **_):
    if 'settings' in data:
        old_data = await get_user_data(callsign)
        station_callsign = old_data['settings']['station']['callsign']
        station_path = get_station_path(station_callsign) if station_callsign else None
        publish_path = WEB_ROOT + '/js/publish.json'
        publish = loadJSON(publish_path)
        if not publish:
            publish = {}
        new_station_callsign = data['settings']['station']['callsign']
        if station_callsign != new_station_callsign:
            new_path = get_station_path(new_station_callsign) if new_station_callsign else None
            if new_station_callsign:
                if os.path.exists(new_path):
                    return web.HTTPBadRequest(text=
                        f'Station callsign {new_station_callsign.upper()} is already registered')
                create_station_dir(new_path)
                if station_path and os.path.exists(f"{station_path}/gallery"):
                    os.rename(f"{station_path}/gallery", f"{new_path}/gallery")
                if station_path and os.path.exists(f"{station_path}/chat.json"):
                    os.rename(f"{station_path}/chat.json", f"{new_path}/chat.json")
                if station_path and os.path.exists(station_path):
                    shutil.rmtree(station_path)
                if station_callsign:
                    await DB.execute(
                        "delete from log where callsign = %(callsign)s",
                        {'callsign': station_callsign})
                    await DB.execute(
                        "delete from visitors where station = %(callsign)s",
                        {'callsign': station_callsign})
                if station_callsign and station_callsign in publish:
                    if new_station_callsign:
                        publish[new_station_callsign] = publish[station_callsign]
                    del publish[station_callsign]
                station_callsign = new_station_callsign
                station_path = new_path
            else:
                station_path = None
        if station_callsign:
            if not station_callsign in publish:
                publish[station_callsign] = {'admin': True}
            publish[station_callsign]['user'] = data['settings']['publish']
        with open(publish_path, 'w') as f_publish:
            json.dump(publish, f_publish, ensure_ascii = False)
        if station_path:
            if not os.path.exists(station_path):
                create_station_dir(station_path)
        await save_station_settings(station_callsign, admin_callsign, data['settings'])
    elif 'userColumns' in data:
        user_data = await get_user_data(callsign)
        settings = user_data['settings']
        user_columns = settings['userFields']
        for col in range(0, len(data['userColumns'])):
            if len(settings) <= col:
                user_columns.append(data['userColumns'][col])
            else:
                user_columns[col] = data['userColumns'][col]
        user_columns = user_columns[:len(data['userColumns'])]
        await save_station_settings(user_data['settings']['station']['callsign'],
                admin_callsign, settings)
    elif 'manualStats' in data:
        user_data = await get_user_data(admin_callsign)
        station_callsign = user_data['settings']['station']['callsign']
        station_path = get_station_path(station_callsign) if station_callsign else None
        if station_path:
            with open(station_path + '/manualStats.json', 'w') as f_manual_stats:
                json.dump(data['manualStats'], f_manual_stats, ensure_ascii = False)
    else:
        if data.get('chat_callsign') and len(data['chat_callsign']) < 3:
            raise web.HTTPBadRequest(text='Chat callsign should have 3 or more characters.')
        await DB.paramUpdate('users', {'callsign': admin_callsign},
            spliceParams(data, ('email', 'password', 'name', 'chat_callsign', 'pm_enabled')))
    return web.Response(text = 'OK')

async def save_station_settings(station_callsign, admin_callsign, settings):
    settings['admin'] = admin_callsign
    settings['initialized'] = True
    await DB.paramUpdate('users', {'callsign': admin_callsign}, \
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

def sind(deg):
    return math.sin(math.radians(deg))

def cosd(deg):
    return math.cos(math.radians(deg))

def wfs_query(wfs_type, location, strict=False):
    params = WFS_PARAMS[wfs_type]
    url = ('https://r1cf.ru:8088/geoserver/cite/wfs?SERVICE=WFS&REQUEST=GetFeature&TypeName=' +
        '{feature}&VERSION=1.1.0&CQL_FILTER={predi}%28the_geom,POINT%28{lat}%20{lng}%29' +
        '{addParams}%29')
    url_params = {
        'feature': params['feature'],\
        'predi': 'INTERSECTS' if strict else 'DWITHIN',\
        'lat': location[0],\
        'lng': location[1],\
        'addParams': '' if strict else ',0.0025,kilometers' # ~250 meters
       }
    try:
        rsp = requests.get(url.format_map(url_params), verify=False, timeout=(0.2, 1))
        tag = '<cite:' + params['tag'] + '>'
        data = rsp.text
        result = []
        while tag in data:
            start = data.find(tag) + len(tag)
            end = data.find('<', start)
            result.append(data[start:end])
            data = data[end:]
        if result:
            return result[0] if strict else result
        return None

    except requests.exceptions.Timeout:
        return ['-----']

async def get_qth_data(location, country=None):

    if not country:
        country = get_country(location)
    if country not in QTH_PARAMS['countries']:
        country = 'RU'

    data = {'fields': empty_qth_fields(country)}
    data['loc'], data['loc8'] = locator(location)

    if country == 'RU':

        rda = '-----'
        all_rda = wfs_query('rda', location)
        strict_rda = wfs_query('rda', location, strict=True)
        if all_rda:
            if len(all_rda) > 1:
                all_rda = [strict_rda] + [x for x in allRda if x != strict_rda or x == '-----']
                rda = ' '.join(all_rda)
            else:
                rda = all_rda[0]
        data['fields']['values'][0] = rda

        data['fields']['values'][1] = RAFA_LOCS[data['loc']]\
            if data['loc'] in RAFA_LOCS else None

    elif country == 'KZ':

        kda = '-----'
        all_kda = wfs_query('kda', location)
        strict_kda = wfs_query('kda', location, strict=True)
        if all_kda:
            if len(all_kda) > 1:
                all_kda = [strict_kda] + [x for x in all_kda if x != strict_kda or x == '-----']
                kda = ' '.join(all_kda)
            else:
                kda = all_kda[0]
        data['fields']['values'][0] = kda

    elif country == 'IT':
        data['fields']['values'][0] = wfs_query('waip', location, strict=True)

    elif country == 'GB':
        data['fields']['values'][0] = wfs_query('wab', location, strict=True)

    return data

def save_qth_now_location(callsign, location, path):
    qth_now_locations = load_jSON(path)
    if not qth_now_locations:
        qth_now_locations = []
    _ts = int(time.time())
    dt_uTC = datetime.utcnow()
    _dt, _tm = dt_fmt(dt_uTC)
    qth_now_locations = [item for item in qth_now_locations
            if _ts - item['ts'] < 600 and (item['location'][0] != location[0] or
            item['location'][1] != location[1]) and
            (callsign is None or item['callsign'] != callsign)]
    qth_now_locations.append({
        'location': location,
        'ts': _ts,
        'date': _dt,
        'time': _tm,
        'callsign': callsign
    })
    with open(path, 'w') as f_loc:
        json.dump(qth_now_locations, f_loc, ensure_ascii = False)

@APP_ROUTES.post('/aiohttp/location')
@auth(require_token=False)
async def location_handler(request):
    new_data = await request.json()
    station_path = None
    station_settings = None
    station_callsign = None
    if callsign:
        station_path = await get_station_path_by_admin_cS(callsign)
        station_settings = load_jSON(station_path + '/settings.json')
        if not station_settings:
            raise web.HTTPBadRequest(text='Expedition profile is not initialized.')
        if (station_settings.get('station') and
                station_settings['station'].get('callsign') and
                station_settings['station'].get('activityPeriod')):
            act_period = [datetime.strptime(dt, '%d.%m.%Y') for dt in
                station_settings['station']['activityPeriod'] if dt]
            if act_period and act_period[0] <= datetime.utcnow() <= act_period[1] + timedelta(days=1):
                station_callsign = station_settings['station']['callsign']

    if new_data.get('location'):
        qth_now_cs = None
        if 'callsign' in new_data and new_data['callsign']:
            qth_now_cs = new_data['callsign']
        elif station_callsign:
            qth_now_cs = station_callsign

        if qth_now_cs:
            qth_now_cs = qth_now_cs.upper()
            save_qth_now_location(qth_now_cs, new_data['location'],
                    WEB_ROOT + '/js/qth_now_locations.json')

        save_qth_now_location(qth_now_cs, new_data['location'],
                WEB_ROOT + '/js/qth_now_locations_all.json')

    if not callsign and 'location' in new_data:
        qth = await get_qth_data(new_data['location'])
        return web.json_response({'qth': qth})
    f_path = station_path + '/status.json'
    data = loadJSON(f_path) or {}
    if not 'locTs' in data and 'ts' in data:
        data['locTs'] = data['ts']
    dt_uTC = datetime.utcnow()
    data['ts'] = int(time.time())
    data['date'], data['time'] = dtFmt(dt_uTC)
    data['year'] = dt_uTC.year
    if 'online' in new_data:
        data['online'] = new_data['online']
    if 'freq' in new_data and new_data['freq']:
        data['freq'] = {'value': new_data['freq'], 'ts': data['ts']}
        from_callsign = station_settings['station']['callsign']
        insert_chat_message(path=station_path + '/chat.json',
            msg_data={'from': from_callsign,
            'cs': callsign,
            'text': '<b><i>' + new_data['freq'] + '</b></i>'},
            admin=True)
    country = station_settings['qthCountry'] if 'qthCountry' in station_settings else None
    if new_data.get('location'):
        location = new_data['location']

        country = get_country(location)

        data['qth'] = await get_qth_data(location, country=country)

        if 'comments' in new_data:
            data['comments'] = new_data['comments']
        if 'location' in data and data['location']:
            data['prev'] = {'location': data['location'][:], \
                    'ts': data['locTs']}
        data['locTs'], data['locDate'], data['locTime'] = data['ts'], data['date'], data['time']
        data['location'] = new_data['location']
        if 'prev' in data:
            lat = [data['location'][1], data['prev']['location'][1]]
            lon = [data['location'][0], data['prev']['location'][0]]
            dlon = lon[0] - lon[1]
            dlat = lat[0] - lat[1]
            _ap = (sind(dlat/2))**2 + cosd(lat[0]) * cosd(lat[1]) * (sind(dlon/2)) ** 2
            _cp = 2 * math.atan2(math.sqrt(_ap), math.sqrt(1 - _ap))
            distance = _cp * 6373
            data['d'] = distance
            data['dt'] = data['locTs'] - data['prev']['ts']
            if float(data['locTs'] - data['prev']['ts']) != 0:
                data['speed'] = distance / (float(data['locTs'] - data['prev']['ts']) \
                        / 3600)
            else:
                data['speed'] = 0

    if 'qth' in new_data:

        if 'qth' not in data:
            data['qth'] = {'fields': empty_qth_fields(country=country)}
        for key in newData['qth']['fields'].keys():
            data['qth']['fields']['values'][int(key)] = new_data['qth']['fields'][key]
        if 'loc' in new_data['qth']:
            data['qth']['loc'] = new_data['qth']['loc']

    with open(f_path, 'w') as f_status:
        json.dump(data, f_status, ensure_ascii = False)
    return web.json_response(data)

def locator(location):
    lat = location[0]
    lng = location[1]
    qth = ""
    lat += 90
    lng += 180
    lat = lat / 10 + 0.0000001
    lng = lng / 20 + 0.0000001
    qth += chr(65 + int(lng))
    qth += chr(65 + int(lat))
    lat = 10 * (lat - math.trunc(lat))
    lng = 10 * (lng - math.trunc(lng))
    qth += chr(48 + int(lng))
    qth += chr(48 + int(lat))
    lat = 24 * (lat - math.trunc(lat))
    lng = 24 * (lng - math.trunc(lng))
    qth += chr(65 + int(lng))
    qth += chr(65 + int(lat))
    lat = 10 * (lat - math.trunc(lat))
    lng = 10 * (lng - math.trunc(lng))
    sfx = chr(48 + int(lng))
    sfx += chr(48 + int(lat))
    return (qth, sfx)

BANDS_WL = {'1.8': '160M', '3.5': '80M', '7': '40M', \
        '10': '30M', '14': '20M', '20': '14M', '18': '17M', '21': '15M', \
        '24': '12M', '28': '10M', '50': '6M', '144': '2M'}

ADIF_QTH_FIELDS = ('MY_CNTY', 'MY_CITY', 'NOTES')

async def get_blog_entries_handler(request):
    callsign = request.match_info.get('callsign', None)
    if callsign:
        callsign = callsign.replace('-', '/')
    else:
       return web.HTTPBadRequest(text = 'No blog was specified.')
    data = await DB.execute("""
            select id, "file", file_type, file_thumb, txt, 
                to_char(timestamp_created, 'Dy, DD Mon YYYY HH24:MI:SS GMT') as last_modified,
                to_char(timestamp_created, 'DD Mon YYYY HH24:MI') as post_datetime,
                extract(epoch from timestamp_created) as ts,
                (select count(*) 
                    from blog_reactions 
                    where entry_id = blog_entries.id) as reactions,
                (select blog_comments.id
                    from blog_comments
                    where entry_id = blog_entries.id
                    order by blog_comments.id desc
                    limit 1) as last_comment_id
            from blog_entries
            where "user" = %(callsign)s
            order by id desc
            """,
            params={'callsign': callsign},
            container='list')
    if not data:
        return web.HTTPNotFound(text='Blog entries not found')
    
    return web.json_response(data, headers={'last-modified': data[0]['last_modified']})

async def get_blog_comments_read_handler(request):
    blog_callsign = request.match_info.get('callsign', None)
    if blog_callsign:
        blog_callsign = blog_callsign.replace('-', '/')
    else:
        return web.HTTPBadRequest(text = 'No blog was specified.')
    data = await request.json()
    callsign = decode_token(data)
    if not isinstance(callsign, str):
        return callsign
    comments_read = await DB.execute("""
        select blog_comments_read.entry_id, last_read_comment_id 
        from blog_comments_read join blog_entries on
            blog_entries.id = blog_comments_read.entry_id
        where blog_entries.user = %(blogCallsign)s and
            blog_comments_read.user = %(callsign)s""",
        {"blog_callsign": blog_callsign, "callsign": callsign},
        container="list")
    if not comments_read:
        return web.HTTPNotFound(text='Blog entries not found')
    return web.json_response({x['entry_id']: x['last_read_comment_id'] for x in comments_read})

async def set_blog_comments_read_handler(request):
    entry_id = int(request.match_info.get('entry_id', None))
    if not entry_id:
        return web.HTTPBadRequest(text = 'No valid post id was specified.')
    data = await request.json()
    callsign = decode_token(data)
    if not isinstance(callsign, str):
        return callsign
    await DB.execute("""
        insert into blog_comments_read (entry_id, "user", last_read_comment_id)
        values (%(entry_id)s, %(callsign)s, %(commentId)s)
        on CONFlict on constraint blog_comments_read_pkey
        do update set last_read_comment_id = %(commentId)s""",
        {"entryId": entry_id, "callsign": callsign, "commentId": data['commentId']})
    return web.Response(text="OK")

async def delete_blog_entry_handler(request):
    entry_id = int(request.match_info.get('entry_id', None))
    if not entry_id:
        return web.HTTPBadRequest(text = 'No valid post id was specified.')
    data = await request.json()
    callsign = decode_token(data)
    if not (await get_user_data(callsign))['email_CONFirmed']:
        return web.HTTPUnauthorized(text='Email is not CONFirmed')
    if not isinstance(callsign, str):
        return callsign
    entry_in_dB = await DB.execute("""
        select id, "file", file_thumb
        from blog_entries
        where id = %(entryId)s and (%(callsign)s is null or "user" = %(callsign)s)""",
        {'entryId': entry_id, 'callsign': callsign if callsign not in SITE_ADMINS else None})
    if not entry_in_dB:
        return web.HTTPNotFound(text='Blog entry not found')
    station_path = await get_station_path_by_admin_cS(callsign)
    await delete_blog_entry(entry_in_dB, station_path)
    return web.Response(text='OK')

async def clear_blog_handler(request):
    data = await request.json()
    callsign = decode_token(data)
    if not (await get_user_data(callsign))['email_CONFirmed']:
        return web.HTTPUnauthorized(text='Email is not CONFirmed')
    if not isinstance(callsign, str):
        return callsign
    entries_in_dB = await DB.execute("""
        select id, "file", file_thumb
        from blog_entries
        where "user" = %(callsign)s""",
        {'callsign': callsign},
        container="list")
    if entries_in_dB:
        station_path = await get_station_path_by_admin_cS(callsign)
        for entry in entries_in_dB:
            await delete_blog_entry(entry, station_path)
    return web.Response(text='OK')


async def delete_blog_comment_handler(request):
    comment_id = int(request.match_info.get('comment_id', None))
    if not comment_id:
        return web.HTTPBadRequest(text = 'No valid comment id was specified.')
    data = await request.json()
    callsign = decode_token(data)
    if not (await get_user_data(callsign))['email_CONFirmed']:
        return web.HTTPUnauthorized(text='Email is not CONFirmed')
    if not isinstance(callsign, str):
        return callsign
    comment_in_dB = await DB.execute("""
        select blog_comments.id 
        from blog_comments join blog_entries 
            on entry_id = blog_entries.id
        where blog_comments.id = %(commentId)s and 
            (%(callsign)s is null or blog_entries.user = %(callsign)s or blog_comments.user = %(callsign)s)""",
        {'commentId': comment_id, 'callsign': callsign if callsign not in SITE_ADMINS else None})
    if not comment_in_dB:
        return web.HTTPNotFound(text='Blog comment not found')
    await DB.execute("""
        delete from blog_comments 
        where id = %(commentId)s""",
        {'commentId': comment_id})
    return web.Response(text='OK')



async def create_blog_comment_handler(request):
    entry_id = int(request.match_info.get('entry_id', None))
    if not entry_id:
        return web.HTTPBadRequest(text = 'No valid post id was specified.')
    data = await request.json()
    callsign = decode_token(data)
    if not (await get_user_data(callsign))['email_CONFirmed']:
        return web.HTTPUnauthorized(text='Email is not CONFirmed')
    if not isinstance(callsign, str):
        return callsign
    await DB.execute("""
        insert into blog_comments ("user", entry_id, txt)
        values (%(callsign)s, %(entryId)s, %(txt)s)""",
        {"callsign": callsign, "entryId": entry_id, "txt": data["text"]})
    return web.Response(text="OK")

async def get_blog_reaction_handler(request):
    entry_id = int(request.match_info.get('entry_id', None))
    reaction_type = int(request.match_info.get('type', None))
    if not entry_id:
        return web.HTTPBadRequest(text = 'No valid post id was specified.')
    data = await request.json()
    callsign = decode_token(data)
    if not callsign or not (await get_user_data(callsign))['email_CONFirmed']:
        return web.HTTPUnauthorized(text='Email is not CONFirmed')
    if not isinstance(callsign, str):
        return callsign
    reaction = await DB.execute("""
        select "type"
        from blog_reactions
        where entry_id = %(entryId)s and "user" = %(callsign)s and 
            (%(type)s is null or "type" = %(type)s)""",
        {"entryId": entry_id, "callsign": callsign, "type": reaction_type})
    if not reaction:
        return web.HTTPNotFound(text='Blog reaction not found')
    return web.json_response(reaction)

async def create_blog_reaction_handler(request):
    entry_id = int(request.match_info.get('entry_id', None))
    if not entry_id:
        return web.HTTPBadRequest(text = 'No valid post id was specified.')
    data = await request.json()
    callsign = decode_token(data)
    if not callsign or not (await get_user_data(callsign))['email_CONFirmed']:
        return web.HTTPUnauthorized(text='Email is not CONFirmed')
    if not isinstance(callsign, str):
        return callsign
    await DB.execute("""
        insert into blog_reactions (entry_id, "user", "type")
        values (%(entryId)s, %(callsign)s, %(type)s)
        on CONFlict on constraint blog_reactions_pkey
            do update set "type" = %(type)s""",
        {"callsign": callsign, "entryId": entry_id, "type": data["type"]})
    return web.Response(text="OK")

async def delete_blog_reaction_handler(request):
    entry_id = int(request.match_info.get('entry_id', None))
    if not entry_id:
        return web.HTTPBadRequest(text = 'No valid post id was specified.')
    data = await request.json()
    callsign = decode_token(data)
    if not (await get_user_data(callsign))['email_CONFirmed']:
        return web.HTTPUnauthorized(text='Email is not CONFirmed')
    if not isinstance(callsign, str):
        return callsign
    await DB.execute("""
        delete from blog_reactions
        where entry_id = %(entryId)s and "user" = %(callsign)s""",
        {'entryId': entryId, 'callsign': callsign})
    return web.Response(text='OK')

async def get_blog_comments_handler(request):
    entry_id = int(request.match_info.get('entry_id', None))
    if not entry_id:
        return web.HTTPBadRequest(text = 'No valid post id was specified.')
    data = await DB.execute("""
        select id, "user", txt,
            to_char(timestamp_created, 'DD Mon YYYY HH24:MI') as comment_datetime,
            name, chat_callsign, pm_enabled
        from blog_comments join users on blog_comments.user = users.callsign
        where entry_id = %(entryId)s
        order by id""",
        {"entryId": entry_id}, 
        container="list")
    if not data:
        return web.HTTPNotFound(text='Blog comments not found')
    return web.json_response(data)

async def create_blog_entry_handler(request):
    data = None
    if 'multipart/form-data;' in request.headers[aiohttp.hdrs.CONTENT_TYPE]:
        data = await read_multipart(request)
    else:
        data = await request.json()
    callsign = decode_token(data)
    if not (await get_user_data(callsign))['email_CONFirmed']:
        return web.HTTPUnauthorized(text='Email is not CONFirmed')
    if not isinstance(callsign, str):
        return callsign
    station_path = await get_station_path_by_admin_cS(callsign)
    gallery_path = station_path + '/gallery'
    file = file_type = file_thumb = media_width = None
    if 'file' in data:
        post_id = uuid.uuid4().hex
        if data['file']:
            if not os.path.isdir(gallery_path):
                os.mkdir(gallery_path)
            file = data['file']['contents']
            file_name_base = post_id
            file_ext = data['file']['name'].rpartition('.')[2]
            file_name = file_name_base + '.' + file_ext
            file_type = 'image' if 'image'\
                in data['file']['type'] else 'video'
            file_path = gallery_path + '/' + file_name
            with open(file_path, 'wb') as f_img:
                f_img.write(file)
            tn_src = file_path
            if file_type == 'video':

                tn_src = gallery_path + '/' + file_name_base + '.jpeg'
                (
                    ffmpeg
                        .input(file_path)
                        .output(tn_src, vframes=1, vf="thumbnail")
                        .run()
                )
                video_props = ffmpeg.probe(file_path)
                video_stream = [stream for stream in video_props['streams'] if stream['codec_type'] == 'video'][0]
                max_video_height = int(CONF['gallery']['max_video_height'])
                if video_stream['height'] > max_video_height:
                    tmp_file_path = f"{gallery_path}/{file_name_base}_tmp.{file_ext}"
                    os.rename(file_path, tmp_file_path)
                    (
                        ffmpeg
                            .output(
                                ffmpeg
                                    .input(tmp_file_path)
                                    .video
                                    .filter('scale', -2, max_video_height),
                                 ffmpeg
                                    .input(tmp_file_path)
                                    .audio,
                                file_path)
                            .run()
                    )
                    os.unlink(tmp_file_path)

            with Image(filename=tn_src) as img:
                with Image(width=img.width, height=img.height,
                        background=Color("#EEEEEE")) as bg_img:

                    bg_img.composite(img, 0, 0)

                    exif = {}
                    exif.update((key[5:], val) for key, val in img.metadata.items() if
                            key.startswith('exif:'))
                    if 'Orientation' in exif:
                        if exif['Orientation'] == '3':
                            bg_img.rotate(180)
                        elif exif['Orientation'] == '6':
                            bg_img.rotate(90)
                        elif exif['Orientation'] == '8':
                            bg_img.rotate(270)

                    size = img.width if img.width < img.height else img.height
                    bg_img.crop(width=size, height=size, gravity='north')
                    bg_img.resize(200, 200)
                    bg_img.format = 'jpeg'
                    bg_img.save(filename=f'{gallery_path}/{file_name_base}_thumb.jpeg')
                    if file_type == 'image':
                        max_height, max_width = (int(CONF['gallery']['max_height']),
                                int(CONF['gallery']['max_width']))
                        if img.width > max_width or img.height > max_height:
                            coeff = min(max_width/img.width, max_height/img.height)
                            img.resize(width=int(coeff*img.width), height=int(coeff*img.height))
                            img.compression_quality = int(CONF['gallery']['quality'])
                            img.save(filename=file_path)
            if file_type == 'video':
                os.unlink(tn_src)
            
            file = f'gallery/{file_name}'
            file_thumb = f'gallery/{file_name_base}_thumb.jpeg'

        await DB.execute("""
            insert into blog_entries
                ("user", "file", file_type, file_thumb, txt)
            values
                (%(callsign)s, %(file)s, %(fileType)s, %(fileThumb)s, %(text)s)
            """,
            params={'callsign': callsign, 'file': file, 'fileType': file_type, 
                'fileThumb': file_thumb, 'text': data['caption']})
 
        if file:
            max_count = int(CONF['gallery']['max_count'])
            excess = await DB.execute("""
                select id, "file", file_thumb
                from blog_entries
                where "user" = %(callsign)s and "file" is not null
                order by id desc
                offset %(maxCount)s""",
                params={'callsign': callsign, 'maxCount': max_count},
                container='list')
            if excess:
                for entry in excess:
                    await delete_blog_entry(entry, station_path)

        return web.Response(text='OK')

async def delete_blog_entry(entry, station_path):
    if entry['file']:
        if os.path.isfile(f"{station_path}/{entry['file']}"):
            os.unlink(f"{station_path}/{entry['file']}")
        if os.path.isfile(f"{station_path}/{entry['file_thumb']}"):
            os.unlink(f"{station_path}/{entry['file_thumb']}")
    await DB.execute("""
        delete from blog_entries
        where id = %(id)s""", entry)

async def export_adif_handler(request):
    callsign = request.match_info.get('callsign', None)
    if callsign:
        callsign = callsign.replace('-', '/')
    else:
        return web.HTTPBadRequest(text = 'No callsign was specified.')
    log = await log_from_dB(callsign, limit=False)

    adif = """ADIF Export from TNXLOG
    Logs generated @ """ + time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()) + "\n<EOH>\n"

    def adif_field(name, data):
        data_str = str(data) if data else ''
        return f"<{name.upper()}:{len(data_str)}>{data_str} "

    for qso in log:
        qso_time = time.gmtime(qso['qso_ts'])
        try:
            adif += (
                    adif_field("CALL", qso['cs']) +
                    adif_field("QSO_DATE", time.strftime("%Y%m%d", qsoTime)) +
                    adif_field("TIME_OFF", time.strftime("%H%M%S", qso_time)) +
                    adifField("TIME_ON", time.strftime("%H%M%S", qso_time)) +
                    adif_field("BAND", BANDS_WL[qso['band']]) +
                    adif_field("STATION_CALLSIGN", qso['myCS']) +
                    adif_field("FREQ", str(Decimal(qso['freq'])/1000)) +
                    adif_field("MODE", qso['mode']) +
                    adif_field("RST_RCVD", qso['rcv']) +
                    adif_field("RST_SENT", qso['snt']) +
                    adif_field("MY_GRIDSQUARE", qso['loc']) +
                    adif_field("GRIDSQUARE", qso['loc_rcv'] if 'loc_rcv' in qso else None))
        except Exception:
            logging.exception('Error while adif conversion. QSO:')
            logging.error(qso)

        for field_no, val in enumerate(qso['qth']):
            adif += adif_field(ADIF_QTH_FIELDS[field_no], val)
        adif += "<EOR>\r\n"

    return web.Response(
            headers={
                'Content-Disposition':
                    f'Attachment;filename={callsign + datetime.now().strftime("_%d_%b_%Y")}.adi'
            },
            body=adif.encode()
        )

@APP_ROUTES.post('/aiohttp/visitors')
@auth(require_token=False)
async def visitors_handler(data, *, callsign, **_):
    visitor = callsign or data.get('user_id')
    await DB.execute("""
        insert into visitors (station, visitor, tab)
        values (%(station)s, %(visitor)s, %(tab)s)
        on CONFlict on constraint visitors_pkey do
            update set visited = now();
        """,
        {'station': data['station'],
            'visitor': visitor,
            'tab': data['tab']})
    return web.Response(text = 'OK')

@APP_ROUTES.post('/aiohttp/visitorsStats')
@auth()
async def visitors_stats_handler(data, *, callsign, **_):
    if callsign not in SITE_ADMINS:
        station_settings = await DB.execute("""
        select settings from users
        where callsign = %(callsign)s""",
        {'callsign': callsign})
        if station_settings['station']['callsign'] != data['station']:
            raise web.HTTPForbidden()
    result = {'day': {}, 'week': {}, 'total': {}}
    wheres = {'day': "and visited >= now() - interval '1 day'",
            'week': "and visited >= now() - interval '1 week'",
            'total': ""}
    for period, where in wheres.items():
        db_res = await DB.execute(f"""
            select tab, count(*) as visitors_count
            from visitors
            where station = %(station)s {where}
            group by tab""",
            {'station': data['station']})
        if db_res:
            if isinstance(db_res, dict):
                db_res = [db_res]
            for row in db_res:
                result[period][row['tab']] = row['visitors_count']
            result[period]['total'] = (await DB.execute(f"""
                select count(distinct visitor) as visitors_count
                from visitors
                where station = %(station)s {where}""",
                {'station': data['station']}))['visitors_count']
    return web.json_response(result)

@APP_ROUTES.post('/aiohttp/activeUsers')
@auth()
async def active_users_handler(data, *, callsign, **_):
    if not data.get('chat_callsign'):
        return web.Response(text = 'OK')
    station = data['station'] if 'station' in data else None
    if station:
        station_path = get_station_path(data['station'])
        station_settings = load_jSON(station_path + '/settings.json')
        if not station_settings:
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

async def read_multipart(request):
    data = {}
    reader = await request.multipart()
    while True:
        field = await reader.next()
        if not field:
            break
        contents = await field.read()
        if field.filename:
            data[field.name] = {\
                'contents': contents,\
                'name': field.filename,
                'type': field.headers[aiohttp.hdrs.CONTENT_TYPE]}
        else:
            data[field.name] = contents.decode('utf-8')
            if data[field.name] == 'null':
                data[field.name] = None
    return data

async def sound_record_handler(request):
    data = None
    if 'multipart/form-data;' in request.headers[aiohttp.hdrs.CONTENT_TYPE]:
        data = await read_multipart(request)
    else:
        data = await request.json()
    callsign = decode_token(data)
    if not (await get_user_data(callsign))['email_CONFirmed']:
        return web.HTTPUnauthorized(text='Email is not CONFirmed')
    if not isinstance(callsign, str):
        return callsign
    station_path = await get_station_path_by_admin_cS(callsign)
    sound_records_path = station_path + '/sound'
    if not os.path.isdir(sound_records_path):
        os.mkdir(sound_records_path)
    file = data['file']['contents']
    file_name = data['file']['name']
    file_path = sound_records_path + '/' + file_name
    with open(file_path, 'wb') as f_sound:
        f_sound.write(file)
    sound_records_data_path = station_path + '/sound.json'
    sound_records_data = load_jSON(sound_records_data_path)
    if not sound_records_data:
        sound_records_data = []
    sound_records_data.append(file_name)
    with open(sound_records_data_path, 'w') as f_sRData:
        json.dump(sound_records_data, f_sRData, ensure_ascii = False)
    return web.Response(text='oK')

@aPP_ROUTES.post('/aiohttp/track')
@auth(require_email_CONFirmed=true)
async def track_handler(data, *, callsign, **_):
    station_path = await get_station_path_by_admin_cS(callsign)
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

async def log_from_dB(callsign, limit=True):
    log = []
    limit_clause = f" limit{CONF['web'].getint('log_page_length')}" if limit else ''
    data = await DB.execute(
        f"""select id, qso from log 
            where callsign = %(cs)s order by id desc 
            {limit_clause}
        """, {'cs': callsign})
    if data:
        if isinstance(data, dict):
            log.append(data['qso'])
        else:
            log = [row['qso'] for row in data]
    return log

async def db_insert_qso(callsign, qso):
    insert_success = await DB.execute("""
        insert into log (callsign, qso) 
        values (%(callsign)s, %(qso)s)""",
        {'callsign': callsign, 'qso': json.dumps(qso)})
    if not insert_success:
        qso_in_dB = await DB.execute("""
            select qso from log
            where callsign = %(callsign)s and (qso->>'cs') = %(cs)s and 
                (qso->>'qso_ts') = %(qso_ts)s and (qso->>'band') = %(band)s""",
            {'callsign': callsign, 'qso_ts': str(qso['qso_ts']), 'cs': qso['cs'], 'band': qso['band']})
        if qso_in_dB:
            return qso_in_dB.get('ts')

@APP_ROUTES.post('/aiohttp/log')
@auth(require_email_CONFirmed=True)
async def log_handler(data, *, callsign, **_):
    station_path = await get_station_path_by_admin_cS(callsign)
    log_path = station_path + '/log.json'
    log = []
    if not os.path.isfile(log_path):
        logging.exception('%s not found', log_path)
    try:
        with open(log_path) as f_log:
            log = json.load(f_log)
    except Exception as ex:
        logging.error("Error loading qso log %s", log_path)
        logging.exception(ex)
        log = await log_from_dB(callsign)

    if 'qso' in data:

        rsp = []

        async def process_qso(qso):
            try:
                dtime = datetime.strptime(qso['ts'], "%Y-%m-%d %H:%M:%S")
                qso['date'], qso['time'] = dt_fmt(dtime)
                qso['qso_ts'] = (dtime - datetime(1970, 1, 1)) / timedelta(seconds=1)
            except (ValueError, TypeError) as exc:
                logging.error("Error parsing qso timestamp %s", qso['ts'])
                logging.exception(exc)
                return {'ts': None}

            server_ts = qso.pop('serverTs') if 'serverTs' in qso else None

            if server_ts:
                qso['ts'] = server_ts
                qso_idx = [i[0] for i in enumerate(log) if i[1]['ts'] == qso['ts']]
                if qso_idx:
                    log[qso_idx[0]] = qso
                else:
                    log.append(qso)
                db_update = await DB.execute("""
                    update log set qso = %(qso)s
                    where callsign = %(callsign)s and (qso->>'ts')::float = %(ts)s
                    returning (qso->>'ts')::float""",
                    {'callsign': callsign, 'ts': qso['ts'], 'qso': json.dumps(qso)})
                if not db_update:
                    prev_ts = await db_insert_qso(callsign, qso)
                    if prev_ts:
                        qso['ts'] = prev_ts

            else:
                new_qso = True
                if log:
                    for log_qso in log:
                        same_fl = True
                        for key in qso:
                            if key not in ('ts', 'rda', 'wff', 'comments',
                                'serverTs', 'qso_ts', 'qth', 'no', 'sound') and (
                                        key not in log_qso or qso[key] != log_qso[key]):
                                same_fl = False
                                break
                        if same_fl:
                            logging.debug('prev qso found:')
                            new_qso = False
                            qso['ts'] =  log_qso['ts']
                            log_qso['qso_ts'] = qso['qso_ts']

                if new_qso:
                    status_path = station_path + '/status.json'
                    status_data = loadJSON(status_path)
                    _ts = dtime.timestamp() + tz_offset()
                    if ('freq' not in status_data or status_data['freq']['ts'] < _ts):
                        status_data['freq'] = {'value': qso['freq'], 'ts': _ts}
                        with open(status_path, 'w') as f_status:
                            json.dump(status_data, f_status, ensure_ascii = False)

                    qso['ts'] = time.time()
                    while [x for x in log if x['ts'] == qso['ts']]:
                        qso['ts'] += 0.00000001
                    log.insert(0, qso)
                    prev_ts = await db_insert_qso(callsign, qso)
                    if prev_ts:
                        qso['ts'] = prev_ts

            return {'ts': qso['ts']}

        for qso in data['qso']:
            rsp.append((await process_qso(qso)))

        log = sorted(log, key=lambda qso: qso['qso_ts'] if 'qso_ts' in qso else qso['ts']/10,\
                reverse=True)
        log = log[:CONF['web'].getint('log_page_length')]
        with open(log_path, 'w') as f_log:
            json.dump(log, f_log)

        return web.json_response(rsp)

    if 'delete' in data:
        log = [x for x in log if x['ts'] != data['delete']]
        await DB.execute("""
            delete from log 
            where callsign = %(callsign)s and (qso->>'ts')::float = %(ts)s""",
            {'callsign': callsign, 'ts': data['delete']})

    if 'clear' in data:
        log = []
        await DB.execute(
            "delete from log where callsign = %(callsign)s",
            {'callsign': callsign})
        #clear sound recordings
        for file in Path(station_path + "/sound").glob("*"):
            if file.is_file():
                file.unlink()
        with open(station_path + '/sound.json', 'w') as f_sound:
            json.dump([], f_sound)

    with open(log_path, 'w') as f_log:
        json.dump(log, f_log)
    return web.Response(text = 'OK')

@APP_ROUTES.post('/aiohttp/logSearch')
async def log_search_handler(request):
    req_data = await request.json()
    if not req_data.get('station'):
        return web.HTTPBadRequest(text='Invalid search params')
    result = []
    cs_filter = "and qso->>'cs' = %(callsign)s" if req_data.get('callsign') else ''
    db_data = await DB.execute(
        f"""select id, qso from log 
            where callsign = %(station)s {cs_filter}
            order by id desc""",
            req_data)
    if db_data:
        if isinstance(db_data, dict):
            result.append(db_data['qso'])
        else:
            result = [row['qso'] for row in db_data]
    return web.json_response(result)

def replace0(val):
    return val.replace("0", "\u00D8")

async def ban_user_handler(request):
    data = await request.json()
    callsign = decode_token(data)
    if not isinstance(callsign, str):
        return callsign
    if callsign not in SITE_ADMINS:
        return web.HTTPUnauthorized(text='You must be logged in as site admin')
    user_data = await get_user_data(data['user'])
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

async def users_list_handler(request):
    data = await request.json()
    callsign = decode_token(data)
    if not isinstance(callsign, str):
        return callsign
    if not callsign in SITE_ADMINS:
        return web.HTTPUnauthorized(\
            text='You must be logged in as site admin')
    where_clause = 'where email not in %(banned)s'
    params = {'banned': tuple(BANLIST['emails'])}
    if data.get('filter') == 'new':
        where_clause += ' and not verified'
    elif data.get('filter') == 'no_chatcall':
        where_clause += " and (chat_callsign is null or chat_callsign = '')"
    elif data.get('filter') == 'banned':
        where_clause = "where email in %(banned)s"
    ulist = await DB.execute(f"""
        select callsign, email, email_CONFirmed, verified, chat_callsign, name
            from users {where_clause} 
            order by callsign""", params)
    if not ulist:
        ulist = []
    if isinstance(ulist, dict):
        ulist = [ulist,]
    for user in ulist:
        user['banned'] = data.get('filter') == 'banned' or user['email'] in BANLIST['emails']
    return web.json_response(ulist)

async def user_edit_handler(request):
    data = await request.json()
    callsign = decode_token(data)
    if not isinstance(callsign, str):
        return callsign
    if not callsign in SITE_ADMINS:
        return web.HTTPUnauthorized(\
            text='You must be logged in as site admin')
    await DB.paramUpdate('users', {'callsign': data['callsign']},
            spliceParams(data, ['verified', 'email_CONFirmed']))
    return web.Response(text = 'OK')

@APP_ROUTES.delete('/aiohttp/chat/')
@auth(require_email_CONFirmed=True)
async def chat_delete_handler(data, *, callsign, **_):
    station = data['station'] if 'station' in data else None
    admins = SITE_ADMINS 
    chat_path = None
    if station:
        station_path = get_station_path(data['station'])
        station_settings = loadJSON(station_path + '/settings.json')
        admins += [x.lower() for x in\
            station_settings['chatAdmins'] + [ station_settings['admin'], ]]
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

@APP_ROUTES.post('/aiohttp/chat')
@auth(require_email_CONFirmed=True)
async def chat_post_handler(data, *, callsign, **_):
    chat_path = ''
    station = data['station'] if 'station' in data else None
    if station:
        station_path = get_station_path(data['station'])
        station_settings = loadJSON(station_path + '/settings.json')
        admins = [x.lower() for x in\
            station_settings['chatAdmins'] + [ station_settings['admin'], ]]
        admin = callsign in admins
        chat_path = station_path + '/chat.json'
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
    insert_chat_message(path=chat_path, msg_data=data, admin=admin)
    return web.Response(text = 'OK')

def insert_chat_message(path, msg_data, admin):
    CHAT_MAX_LENGTH = int(CONF['chat']['max_length'])
    chat = loadJSON(path) or []
    msg = {'user': msg_data['from'],
            'text': msg_data['text'],
            'cs': msg_data.get('cs') or msg_data['from'],
            'admin': admin, 'ts': time.time()}
    msg['date'], msg['time'] = dt_fmt(datetime.utcnow())
    if 'name' in msg_data:
        msg['name'] = msg_data['name']
    chat.insert(0, msg)
    chat_trunc = []
    chat_adm = []
    for msg in chat:
        if msg['text'].startswith('***') and msg['admin']:
            chat_adm.append(msg)
        elif len(chat_trunc) < CHAT_MAX_LENGTH:
            chat_trunc.append(msg)
    chat = chat_adm + chat_trunc
    with open(path, 'w') as f_chat:
        json.dump(chat, f_chat, ensure_ascii = False)

async def send_spot_handler(request):
    global last_spot_sent
    data = await request.json()
    now = datetime.now().timestamp()
    response = {'sent': False,
            'secondsLeft': CONF.getint('cluster', 'spotInterval')}
    if not lastSpotSent or now - lastSpotSent > response['secondsLeft']:
        lastSpotSent = now
        protocol = await  clusterProtocol.connect(APP.loop, \
            call = data['userCS'],
            host = CONF.get('cluster', 'host'),
            port = CONF.get('cluster', 'port'))

        def send_spot():
            protocol.write('dx ' + data['cs'] + ' ' + data['freq'] + ' ' + \
                data['info'])
            response['sent'] = True
            APP.loop.call_later(1, protocol.close)

        if protocol:
            logging.debug('Protocol connected')
            protocol.onLoggedIn.append(send_spot)
            await protocol.waitDisconnected()
            if not response['sent']:
                response['reply'] = protocol.latestReply
    else:
        response['secondsLeft'] -= now - lastSpotSent
    return web.json_response(response)


def server_start():
    APP.router.add_post('/aiohttp/CONFirmEmailRequest',
            CONFirm_email_request_handler)
    APP.router.add_get('/aiohttp/CONFirmEmail',
            CONFirm_email_link_handler)

    APP.router.add_post('/aiohttp/contact', contact_handler)
    APP.router.add_post('/aiohttp/user_data', user_data_handler)
    APP.router.add_post('/aiohttp/soundRecord', sound_record_handler)
    APP.router.add_post('/aiohttp/sendSpot', send_spot_handler)
    APP.router.add_post('/aiohttp/privateMessages/post', private_messages_post_handler)
    APP.router.add_post('/aiohttp/privateMessages/get', private_messages_get_handler)
    APP.router.add_post('/aiohttp/privateMessages/delete', private_messages_delete_handler)
    APP.router.add_post('/aiohttp/privateMessages/read', private_messages_read_handler)
    APP.router.add_post('/aiohttp/banUser', ban_user_handler)
    APP.router.add_post('/aiohttp/users', users_list_handler)
    APP.router.add_post('/aiohttp/editUser', user_edit_handler)

    APP.router.add_get('/aiohttp/adif/{callsign}', export_adif_handler)

    APP.router.add_get('/aiohttp/blog/{callsign}', get_blog_entries_handler)
    APP.router.add_post('/aiohttp/blog', create_blog_entry_handler)
    APP.router.add_post('/aiohttp/gallery', create_blog_entry_handler)
    APP.router.add_delete('/aiohttp/blog/{entry_id}', delete_blog_entry_handler)
    APP.router.add_post('/aiohttp/blog/clear', clear_blog_handler)

    APP.router.add_get('/aiohttp/blog/{entry_id}/comments', get_blog_comments_handler)
    APP.router.add_post('/aiohttp/blog/{entry_id}/comments', create_blog_comment_handler)
    APP.router.add_delete('/aiohttp/blog/comments/{comment_id}', delete_blog_comment_handler)

    APP.router.add_post('/aiohttp/blog/{entry_id}/reactions/{type}', get_blog_reaction_handler)
    APP.router.add_put('/aiohttp/blog/{entry_id}/reactions', create_blog_reaction_handler)
    APP.router.add_delete('/aiohttp/blog/{entry_id}/reactions', delete_blog_reaction_handler)

    APP.router.add_post('/aiohttp/blog/{callsign}/comments/read', get_blog_comments_read_handler)
    APP.router.add_put('/aiohttp/blog/{entry_id}/comments/read', set_blog_comments_read_handler)


    APP.add_routes(APP_ROUTES)

    DB.verbose = True

    async def on_startup(_):
        await DB.connect()

    APP.on_startup.append(on_startup)

    web.run_app(APP, path = CONF.get('sockets', 'srv'))
