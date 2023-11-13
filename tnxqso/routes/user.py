#!/usr/bin/python3
#coding=utf-8

import logging
import json

from aiohttp import web

from tnxqso.common import CONF, WEB_ADDRESS, DEF_USER_SETTINGS, web_json_response
from tnxqso.db import DB
from tnxqso.services.auth import (auth, SITE_ADMINS, BANLIST, decode_token, encode_token,
        check_recaptcha, authenticate)
from tnxqso.services.email import send_email

USER_ROUTES = web.RouteTableDef()

@USER_ROUTES.post('/aiohttp/userData')
@auth()
async def user_data_handler(_data, *, callsign, **_):
    user_data = await DB.get_user_data(callsign)
    del user_data['password']
    if callsign in SITE_ADMINS:
        user_data['siteAdmin'] = True
    return web_json_response(user_data)

@USER_ROUTES.post('/aiohttp/login')
async def login_handler(request):
    data = await request.json()
    if not isinstance(data, dict):
        logging.error('Wrong login data')
        logging.error(data)
        raise web.HTTPBadRequest(text = 'Bad login request: ' + str(data))
    user_data = False
    if not 'login' in data or len(data['login']) < 2:
        raise web.HTTPBadRequest(text='Minimal login length is 2 symbols')
    if not 'password' in data or len(data['password']) < 6:
        raise web.HTTPBadRequest(text='Minimal password length is 6 symbols')
    data['login'] = data['login'].lower()
    if data['login'] in BANLIST['callsigns']:
        raise web.HTTPUnauthorized(text='Account is banned')
    user_data = await DB.get_user_data(data['login'])
    if data.get('newUser'):
        rc_test = await check_recaptcha(data['recaptcha'])
        if not rc_test:
            raise web.HTTPBadRequest(text='Recaptcha test failed. Please try again')
        if user_data:
            raise web.HTTPBadRequest(text='This callsign is already registered.')
        user_data = await DB.get_object('users',
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
    user_data['token'] = encode_token({
        'callsign': data['login'],
        'aud': ['tnxqso', 'rabbitmq'],
        'scope': [
            f'rabbitmq.read:{CONF["rabbitmq"]["virtual_host"]}/pm/{data["login"]}',
            f'rabbitmq.configure:{CONF["rabbitmq"]["virtual_host"]}/pm/{data["login"]}',
            f'rabbitmq.read:{CONF["rabbitmq"]["virtual_host"]}/stomp-subscription-*',
            f'rabbitmq.write:{CONF["rabbitmq"]["virtual_host"]}/stomp-subscription-*',
            f'rabbitmq.configure:{CONF["rabbitmq"]["virtual_host"]}/stomp-subscription-*'
            ]
        }, disable_time=True)
    del user_data['password']
    if data.get('newUser'):
        confirm_email_msg(user_data)
    if data['login'] in SITE_ADMINS:
        user_data['siteAdmin'] = True
    return web.json_response(user_data)

@USER_ROUTES.post('/aiohttp/passwordRecoveryRequest')
async def password_recovery_request_handler(request):
    error = None
    data = await request.json()
    user_data = False
    if not 'login' in data or len(data['login']) < 2:
        error = 'Minimal login length is 2 symbols'
    if not error:
        data['login'] = data['login'].lower()
        rc_test = await check_recaptcha(data['recaptcha'])
        user_data = await DB.get_user_data(data['login'])
        if not rc_test:
            error = 'Recaptcha test failed. Please try again'
        else:
            if not user_data:
                error = 'This callsign is not registered.'
            else:
                if not user_data['email']:
                    error = 'This account has no email address.'
                else:
                    token = encode_token({
                                'callsign': data['login'],
                                'aud': ['tnxqso'],
                                })
                    text = f"""Click on this link to recover your TNXQSO.com password:
{WEB_ADDRESS}/#/changePassword?token={token}
If you did not request password recovery just ignore this message. 
The link above will be valid for 1 hour.

Пройдите по этой ссылке для восстановления пароля на TNXQSO.com:
{WEB_ADDRESS}/#/changePassword?token={token}
Если вы не запрашивали восстановление пароля, игнорируйте это письмо.
Время действия ссылки - 1 час.

TNXQSO.com support"""
                    send_email(text = text, fr = CONF.get('email', 'address'), \
                        to = user_data['email'], \
                        subject = "tnxqso.com password recovery")
                    return web.Response(text = 'OK')
    return web.HTTPBadRequest(text = error)

@USER_ROUTES.post('/aiohttp/confirmEmailRequest')
@auth()
async def confirm_email_request_handler(_data, *, callsign, **_):
    user_data = await DB.get_user_data(callsign)
    del user_data['password']
    if not user_data['email']:
        return web.HTTPBadRequest(text='This account has no email address.')
    confirm_email_msg(user_data)
    return web.Response(text = 'OK')

@USER_ROUTES.get('/aiohttp/confirmEmail')
async def confirm_email_link_handler(request):
    logging.debug(request.query['token'])
    callsign, email = decode_token(bytes(request.query['token'], 'ascii'), require_email=True)
    await authenticate(callsign, email, require_email_confirmed=False)
    await DB.param_update('users', {'callsign': callsign}, {'email_confirmed': True})
    return web.Response(text="Your email was verified. Refresh TNXQSO.com page.\n" +
        "Ваш email подтвержден, обновите страницу TNXQSO.com")

def confirm_email_msg(user_data):
    del user_data['settings']
    del user_data['name']
    user_data['aud'] = ['tnxqso']
    token = encode_token(user_data)
    text = f"""Click on this link to confirm your email address for your TNXQSO.com profile:
{WEB_ADDRESS}/aiohttp/confirmEmail?token={token}
If you did not request email confirmation or registered TNXQSO.com account just ignore this message. 
The link above will be valid for 1 hour.

Пройдите по ссылке, чтобы подтвердить свой email для регистрации на TNXQSO.com:
{WEB_ADDRESS}/aiohttp/confirmEmail?token={token}
Если вы не запрашивали подтверждение email или не регистрировались на TNXQSO.com, игнорируйте это письмо.
Время действия ссылки - 1 час.

TNXQSO.com support"""
    send_email(text = text, fr = CONF.get('email', 'address'), \
        to = user_data['email'], \
        subject = "tnxqso.com email confirmation")

@USER_ROUTES.post('/aiohttp/contact')
@auth(require_token=False, require_email_confirmed=True)
async def contact_handler(data, *, callsign, **_):
    user_email = None
    if callsign:
        user_email = (await DB.get_user_data(callsign))['email']
    else:
        if not await check_recaptcha(data['recaptcha']):
            raise web.HTTPBadRequest(text='Recaptcha test failed. Please try again')
        user_email = data['email']
    send_email(
            text=f"{data['text']}'\n\n'{user_email}",
            fr=user_email,
            to = CONF.get('email', 'address'),
            subject = "tnxqso.com contact message")
    return web.Response(text = 'OK')
