#!/usr/bin/python
#coding=utf-8
import time
import base64
import os
import logging
from functools import wraps

import jwt
import aiohttp
from aiohttp import web

from tnxqso.common import CONF, loadJSON, WEB_ROOT
from tnxqso.db import DB

SITE_ADMINS = frozenset(CONF.get('web', 'admins').split(' '))

BANLIST = loadJSON(WEB_ROOT + '/js/banlist.json') or {'callsigns': [], 'emails': []}

SECRET = None
fp_secret = CONF.get('files', 'secret')
if os.path.isfile(fp_secret):
    with open(fp_secret, 'rb') as f_secret:
        SECRET = f_secret.read()
if not SECRET:
    SECRET = base64.b64encode(os.urandom(64))
    with open(fp_secret, 'wb') as f_secret:
        f_secret.write(SECRET)

async def check_recaptcha(response):
    try:
        rc_data = {'secret': CONF.get('recaptcha', 'secret'), 'response': response}
        async with aiohttp.ClientSession() as session:
            resp = await session.post(CONF.get('recaptcha', 'verifyURL'), data = rc_data)
            resp_data = await resp.json()
            return resp_data['success']
    except Exception:
        logging.exception('Recaptcha error')
        return False

def extract_callsign(request):
    callsign = request.match_info.get('callsign', None)
    if not callsign:
        raise web.HTTPBadRequest(text = 'No callsign was specified.')
    return callsign.replace('-', '/')

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

def encode_token(payload, *, disable_time=False):
    if not disable_time:
        payload['time'] = time.time()
    return jwt.encode(payload, SECRET, algorithm='HS256')

def create_user_token(callsign):
    return encode_token({
        'callsign': callsign,
        'aud': ['tnxqso', 'rabbitmq'],
        'scope': [
            f'rabbitmq.read:{CONF["rabbitmq"]["virtual_host"]}/pm/{callsign}',
            f'rabbitmq.configure:{CONF["rabbitmq"]["virtual_host"]}/pm/{callsign}',
            f'rabbitmq.read:{CONF["rabbitmq"]["virtual_host"]}/chat/*',
            f'rabbitmq.configure:{CONF["rabbitmq"]["virtual_host"]}/chat/*',
            f'rabbitmq.read:{CONF["rabbitmq"]["virtual_host"]}/stomp-subscription-*',
            f'rabbitmq.write:{CONF["rabbitmq"]["virtual_host"]}/stomp-subscription-*',
            f'rabbitmq.configure:{CONF["rabbitmq"]["virtual_host"]}/stomp-subscription-*'
            ]
        }, disable_time=True)

async def authenticate(callsign, email=None, /, *,
        require_email_confirmed=True, require_admin=False):
    if callsign in BANLIST['callsigns']:
        raise web.HTTPUnauthorized(text="Account is banned")
    if email and email in BANLIST['emails']:
        raise web.HTTPUnauthorized(text="Email address is banned")
    user_data = await DB.get_user_data(callsign)
    if not user_data:
        raise web.HTTPUnauthorized(text="Callsign is not registered on TNXQSO.com")
    if email and email != user_data['email']:
        raise web.HTTPUnauthorized(text="Wrong email address")
    if require_email_confirmed and not user_data['email_confirmed']:
        raise web.HTTPUnauthorized(text='Email is not confirmed')
    if require_admin and callsign not in SITE_ADMINS:
        raise web.HTTPUnauthorized(text="You must be logged in as site admin")

async def read_multipart(request):
    data = {}
    reader = await request.multipart()
    while True:
        field = await reader.next()
        if not field:
            break
        contents = await field.read()
        if field.filename:
            data[field.name] = {
                'contents': contents,
                'name': field.filename,
                'type': field.headers[aiohttp.hdrs.CONTENT_TYPE]}
        else:
            data[field.name] = contents.decode('utf-8')
            if data[field.name] == 'null':
                data[field.name] = None
    return data


def auth(require_token=True,
        require_email=False,
        require_admin=False,
        require_email_confirmed=False):

    def auth_wrapper(handler):

        @wraps(handler)
        async def auth_wrapped(request):
            data = None
            if 'multipart/form-data;' in (request.headers.get(aiohttp.hdrs.CONTENT_TYPE) or ''):
                data = await read_multipart(request)
            else:
                data = await request.json()

            callsign = email = None
            if data.get('token'):
                callsign, email = decode_token(data['token'], require_email=require_email)
                await authenticate(callsign, email, require_email_confirmed=require_email_confirmed,
                        require_admin=require_admin)
            elif require_token:
                raise web.HTTPBadRequest(text='Token is missing')

            return await handler(data, callsign=callsign, email=email, request=request)

        return auth_wrapped

    return auth_wrapper
