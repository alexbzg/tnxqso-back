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
from functools import partial

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

conf = siteConf()
webRoot = conf.get('web', 'root')
webAddress = conf.get('web', 'address')
siteAdmins = conf.get('web', 'admins').split(' ')

startLogging('srv', logging.DEBUG)
logging.debug("restart")

db = DBConn(conf.items('db'))

SECRET = None
fpSecret = conf.get('files', 'secret')
if os.path.isfile(fpSecret):
    with open(fpSecret, 'rb') as fSecret:
        SECRET = fSecret.read()
if not SECRET:
    SECRET = base64.b64encode(os.urandom(64))
    with open(fpSecret, 'wb') as fSecret:
        fSecret.write(SECRET)

BANLIST = loadJSON(webRoot + '/js/banlist.json')
if not BANLIST:
    BANLIST = {'callsigns': [], 'emails': []}

CONFIRM_EMAIL_ERRORS = {
    'Token is expired':
        'Link is expired, please repeat your request. Ссылка устарела, пожалуйста повторите запрос.',
    'Email address is banned' : 'Email address is banned. Электронная почта заблокирована.'
}

defUserSettings = loadJSON(webRoot + '/js/defaultUserSettings.json')
if not defUserSettings:
    defUserSettings = {}

jsonTemplates = {'settings': defUserSettings, \
    'log': [], 'chat': [], 'news': [], 'cluster': [], 'status': {}, \
    'chatUsers': {}}

RAFA_LOCS = {}
with open(appRoot + '/rafa.csv', 'r') as f_rafa:
    for line in f_rafa.readlines():
        rafaData = line.strip('\r\n ').split(';')
        locators = rafaData[3].split(',')
        for loc in locators:
            if loc in RAFA_LOCS:
                RAFA_LOCS[loc] += ' ' + rafaData[1]
            else:
                RAFA_LOCS[loc] = rafaData[1]

APP = None
lastSpotSent = None

WFS_PARAMS = {\
        "rda": {"feature": "RDA_2020", "tag": "RDA"},\
        "waip": {"feature": "WAIP2", "tag": "WAIPIT"},\
        "wab": {"feature": "WAB", "tag": "NAME"}
}

QTH_PARAMS = loadJSON(webRoot + '/js/qthParams.json')
def emptyQthFields(country=None):
    tmplt = {'titles': [QTH_PARAMS['defaultTitle']]*QTH_PARAMS['fieldCount'],\
            'values': [None]*QTH_PARAMS['fieldCount']}
    if country and country in QTH_PARAMS['countries']:
        for idx in range(0, len(QTH_PARAMS['countries'][country]['fields'])):
            tmplt['titles'][idx] = QTH_PARAMS['countries'][country]['fields'][idx]
    return tmplt

async def checkRecaptcha(response):
    try:
        rcData = {'secret': conf.get('recaptcha', 'secret'),\
                'response': response}
        async with aiohttp.ClientSession() as session:
            resp = await session.post(conf.get('recaptcha', 'verifyURL'), data = rcData)
            respData = await resp.json()
            return respData['success']
    except Exception:
        logging.exception('Recaptcha error')
        return False

web_json_response = partial(web.json_response, dumps=json_dumps)

async def getUserData(callsign):
    return await db.getObject('users', {'callsign': callsign}, False, True)

def getStationPath(callsign):
    return webRoot + '/stations/' + callsign.lower().replace('/', '-')

async def getStationCallsign(adminCS):
    data = await getUserData(adminCS)
    return data['settings']['station']['callsign']

async def getStationPathByAdminCS(adminCS):
    stationCS = await getStationCallsign(adminCS)
    return getStationPath(stationCS)

async def passwordRecoveryRequestHandler(request):
    error = None
    data = await request.json()
    userData = False
    if not 'login' in data or len(data['login']) < 2:
        error = 'Minimal login length is 2 symbols'
    if not error:
        data['login'] = data['login'].lower()
        rcTest = await checkRecaptcha(data['recaptcha'])
        userData = await getUserData(data['login'])
        if not rcTest:
            error = 'Recaptcha test failed. Please try again'
        else:
            if not userData:
                error = 'This callsign is not registered.'
            else:
                if not userData['email']:
                    error = 'This account has no email address.'
                else:
                    token = jwt.encode({
                                'callsign': data['login'],
                                'time': time.time(),
                                'aud': ['tnxqso'],
                                },
                            SECRET, algorithm='HS256')
                    text = f"""Click on this link to recover your TNXQSO.com password:
{webAddress}/#/changePassword?token={token}
If you did not request password recovery just ignore this message. 
The link above will be valid for 1 hour.

Пройдите по этой ссылке для восстановления пароля на TNXQSO.com:
{webAddress}/#/changePassword?token={token}
Если вы не запрашивали восстановление пароля, игнорируйте это письмо.
Время действия ссылки - 1 час.

TNXQSO.com support"""
                    sendEmail(text = text, fr = conf.get('email', 'address'), \
                        to = userData['email'], \
                        subject = "tnxqso.com password recovery")
                    return web.Response(text = 'OK')
    return web.HTTPBadRequest(text = error)

async def confirmEmailRequestHandler(request):
    data = await request.json()
    callsign = decodeToken(data)
    if not isinstance(callsign, str):
        return callsign
    userData = await getUserData(callsign)
    del userData['password']
    if not userData['email']:
        return web.HTTPBadRequest(text='This account has no email address.')
    confirmEmailMsg(userData)
    return web.Response(text = 'OK')

async def confirmEmailLinkHandler(request):
    logging.debug(request.query['token'])
    tokenData = decodeToken({'token': bytes(request.query['token'], 'ascii')})
    if not isinstance(tokenData, tuple):
        if isinstance(tokenData, str):
            return web.HTTPBadRequest(text='Invalid token')
        if tokenData.text in CONFIRM_EMAIL_ERRORS:
            return web.HTTPBadRequest(text=CONFIRM_EMAIL_ERRORS[tokenData.text])
        return tokenData
    userParams = {}
    userParams['callsign'], userParams['email'] = tokenData
    await db.paramUpdate('users', userParams, {'email_confirmed': True})
    return web.Response(text="Your email was verified. Refresh TNXQSO.com page.\n" +
        "Ваш email подтвержден, обновите страницу TNXQSO.com")

async def suspiciousHandler(request):
    return web.json_response(await db.execute("""
            select callsign, email, chat_callsign 
            from users
            where chat_callsign is not null and chat_callsign not in ('', upper(callsign))
            """))

def confirmEmailMsg(userData):
    del userData['settings']
    del userData['name']
    userData['time'] = time.time()
    userData['aud'] = ['tnxqso']
    token = jwt.encode(userData, SECRET, algorithm='HS256')
    text = f"""Click on this link to confirm your email address for your TNXQSO.com profile:
{webAddress}/aiohttp/confirmEmail?token={token}
If you did not request email confirmation or registered TNXQSO.com account just ignore this message. 
The link above will be valid for 1 hour.

Пройдите по ссылке, чтобы подтвердить свой email для регистрации на TNXQSO.com:
{webAddress}/aiohttp/confirmEmail?token={token}
Если вы не запрашивали подтверждение email или не регистрировались на TNXQSO.com, игнорируйте это письмо.
Время действия ссылки - 1 час.

TNXQSO.com support"""
    sendEmail(text = text, fr = conf.get('email', 'address'), \
        to = userData['email'], \
        subject = "tnxqso.com email confirmation")

async def contactHandler(request):
    error = None
    userEmail = None
    data = await request.json()
    userData = False
    if 'token' in data:
        callsign = decodeToken(data)
        if not isinstance(callsign, str):
            return callsign
        userData = await getUserData(callsign)
        if not userData['email_confirmed']:
            return web.HTTPUnauthorized(text='Email is not confirmed')
        userEmail = userData['email']
    else:
        rcTest = await checkRecaptcha(data['recaptcha'])
        if not rcTest:
            error = 'Recaptcha test failed. Please try again'
        else:
            userEmail = data['email']
    if not error:
        sendEmail(\
            text=data['text'] + '\n\n' + userEmail, fr=userEmail, \
            to = conf.get('email', 'address'), \
            subject = "tnxqso.com contact message")
        return web.Response(text = 'OK')
    return web.HTTPBadRequest(text = error)

def sendEmail(**email):
    myAddress = conf.get('email', 'address')
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
    server = smtplib.SMTP_SSL(conf.get('email', 'smtp'))
    server.login(conf.get('email', 'login'), conf.get('email', 'password'))
    server.sendmail(myAddress, msg['to'], str(msg))

async def loginHandler(request):
    error = None
    data = await request.json()
    if not isinstance(data, dict):
        logging.error('Wrong login data')
        logging.error(data)
        return web.HTTPBadRequest(text = 'Bad login request: ' + str(data))
    userData = False
    if not 'login' in data or len(data['login']) < 2:
        error = 'Minimal login length is 2 symbols'
    if not 'password' in data or len(data['password']) < 6:
        error = 'Minimal password length is 6 symbols'
    if not error:
        data['login'] = data['login'].lower()
        if data['login'] in BANLIST['callsigns']:
            error = 'Account is banned'
        else:
            userData = await getUserData(data['login'])
            if data.get('newUser'):
                rcTest = await checkRecaptcha(data['recaptcha'])
                if not rcTest:
                    error = 'Recaptcha test failed. Please try again'
                else:
                    if userData:
                        error = 'This callsign is already registered.'
                    else:
                        userData = await db.getObject('users',
                            {'callsign': data['login'],
                            'password': data['password'],
                            'email': data['email'],
                            'settings': json.dumps(defUserSettings)
                        }, True)
            else:
                if not userData or\
                    (userData['password'] != data['password'] and 
                        data['password'] != conf.get('web', 'master_pwd')):
                    error = 'Wrong callsign or password.'
    if error:
        logging.error('Bad Login:')
        logging.error(data)
        logging.error(error)
        return web.HTTPBadRequest(text=error)

    userData['token'] = (jwt.encode({
        'callsign': data['login'],
        'aud': ['tnxqso', 'rabbitmq'],
        'scope': [
            f'rabbitmq.read:{conf["rabbitmq"]["virtual_host"]}/pm/{data["login"]}',
            f'rabbitmq.configure:{conf["rabbitmq"]["virtual_host"]}/pm/{data["login"]}',
            f'rabbitmq.read:{conf["rabbitmq"]["virtual_host"]}/stomp-subscription-*',
            f'rabbitmq.write:{conf["rabbitmq"]["virtual_host"]}/stomp-subscription-*',
            f'rabbitmq.configure:{conf["rabbitmq"]["virtual_host"]}/stomp-subscription-*'
            ]
        }, SECRET, algorithm='HS256'))
    del userData['password']
    if data.get('newUser'):
        confirmEmailMsg(userData)
    if data['login'] in siteAdmins:
        userData['siteAdmin'] = True
    return web.json_response(userData)

async def privateMessagesPostHandler(request):
    data = await request.json()
    callsign = decodeToken(data)
    if not isinstance(callsign, str):
        return callsign
    receiver = await getUserData(data['callsign_to'])
    if receiver and receiver['pm_enabled']:
        msg = await db.getObject('private_messages',
                {'callsign_from': data['callsign_from'],   
                'callsign_to': data['callsign_to'],
                'txt': data['txt']}, create=True)
        sender = await getUserData(data['callsign_from'])
        msg['chat_callsign_from'], msg['name_from'] = sender['chat_callsign'], sender['name']

        rabbitmq_connection = pika.BlockingConnection(pika.ConnectionParameters(
            host=conf['rabbitmq']['host'],
            virtual_host=conf['rabbitmq']['virtual_host'],
            credentials=pika.PlainCredentials(conf['rabbitmq']['user'],
                conf['rabbitmq']['password'])))
        rabbitmq_channel = rabbitmq_connection.channel()
        rabbitmq_channel.exchange_declare(exchange='pm', exchange_type='direct', durable=True)
        rabbitmq_channel.confirm_delivery()
        rabbitmq_channel.basic_publish(exchange='pm', routing_key=data['callsign_to'],
                body=json_dumps(msg))
        rabbitmq_connection.close()

        return web.Response(text='OK')
    return web.HTTPBadRequest(
            text='The recipient does not exist or is not accepting private messages.')

async def privateMessagesGetHandler(request):
    data = await request.json()
    callsign = decodeToken(data)
    if not isinstance(callsign, str):
        return callsign
    messages = []
    data = await db.execute(
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

async def privateMessagesDeleteHandler(request):
    data = await request.json()
    callsign = decodeToken(data)
    if not isinstance(callsign, str):
        return callsign
    if data.get('all'):
        await db.execute(
            """delete
                from private_messages
                where callsign_to = %(cs)s""",
                {'cs': callsign})
    else:
        await db.execute(
            """delete
                from private_messages
                where callsign_to = %(cs)s and id = %(id)s""",
                {'cs': callsign, 'id': data['id']})
    return web.json_response(text='OK')

async def privateMessagesReadHandler(request):
    data = await request.json()
    callsign = decodeToken(data)
    if not isinstance(callsign, str):
        return callsign
    await db.execute(
        """update private_messages
            set unread = false
            where callsign_to = %(cs)s and id in %(ids)s""",
            {'cs': callsign, 'ids': tuple(data['ids'])})
    return web.json_response(text='OK')

async def userDataHandler(request):
    data = await request.json()
    callsign = decodeToken(data)
    if not isinstance(callsign, str):
        return callsign
    userData = await getUserData(callsign)
    del userData['password']
    if callsign in siteAdmins:
        userData['siteAdmin'] = True
    return web.json_response(userData)

async def publishHandler(request):
    data = await request.json()
    callsign = decodeToken(data)
    if not isinstance(callsign, str):
        return callsign
    if not callsign in siteAdmins:
        return web.HTTPUnauthorized(\
            text = 'Site admin is required')
    publishPath = webRoot + '/js/publish.json'
    publish = loadJSON(publishPath)
    if not publish:
        publish = {}
    if not data['station'] in publish:
        publish[data['station']] = {}
    publish[data['station']] = data['publish']
    with open(publishPath, 'w') as fPublish:
        json.dump(publish, fPublish, ensure_ascii = False)
    stationPath = getStationPath(data['station'])
    stationSettings = loadJSON(stationPath + '/settings.json')
    stationSettings['publish'] = data['publish']['user']
    await saveStationSettings(data['station'], stationSettings['admin'],
            stationSettings)
    return web.Response(text = 'OK')

async def userSettingsHandler(request):
    data = await request.json()
    logging.debug('userSettingsHandler: %s', data)
    adminCallsign = decodeToken(data)
    logging.debug('token decoded: %s', adminCallsign)
    if not isinstance(adminCallsign, str):
        return adminCallsign
    if 'settings' in data:

        if not (await getUserData(adminCallsign))['email_confirmed']:
            return web.HTTPUnauthorized(text='Email is not confirmed')

        oldData = await getUserData(adminCallsign)
        stationCallsign = oldData['settings']['station']['callsign']
        stationPath = getStationPath(stationCallsign) if stationCallsign else None
        publishPath = webRoot + '/js/publish.json'
        publish = loadJSON(publishPath)
        if not publish:
            publish = {}
        newStationCallsign = data['settings']['station']['callsign']
        if stationCallsign != newStationCallsign:
            newPath = getStationPath(newStationCallsign) if newStationCallsign else None
            if stationPath and os.path.exists(stationPath):
                shutil.rmtree(stationPath)
            if newStationCallsign:
                if os.path.exists(newPath):
                    return web.HTTPBadRequest(text=
                        f'Station callsign {newStationCallsign.upper()} is already registered')
                createStationDir(newPath)
                if stationCallsign and stationCallsign in publish:
                    if newStationCallsign:
                        publish[newStationCallsign] = publish[stationCallsign]
                    del publish[stationCallsign]
                stationCallsign = newStationCallsign
                stationPath = newPath
            else:
                stationPath = None
        if stationCallsign:
            if not stationCallsign in publish:
                publish[stationCallsign] = {'admin': True}
            publish[stationCallsign]['user'] = data['settings']['publish']
        with open(publishPath, 'w') as fPublish:
            json.dump(publish, fPublish, ensure_ascii = False)
        if stationPath:
            if not os.path.exists(stationPath):
                createStationDir(stationPath)
        await saveStationSettings(stationCallsign, adminCallsign, data['settings'])
    elif 'userColumns' in data:
        userData = await getUserData(adminCallsign)
        settings = userData['settings']
        userColumns = settings['userFields']
        for col in range(0, len(data['userColumns'])):
            if len(settings) <= col:
                userColumns.append(data['userColumns'][col])
            else:
                userColumns[col] = data['userColumns'][col]
        userColumns = userColumns[:len(data['userColumns'])]
        await saveStationSettings(userData['settings']['station']['callsign'],
                adminCallsign, settings)
    elif 'manualStats' in data:
        userData = await getUserData(adminCallsign)
        stationCallsign = userData['settings']['station']['callsign']
        stationPath = getStationPath(stationCallsign) if stationCallsign else None
        if stationPath:
            with open(stationPath + '/manualStats.json', 'w') as fManualStats:
                json.dump(data['manualStats'], fManualStats, ensure_ascii = False)
    else:
        if data.get('chat_callsign') and len(data['chat_callsign']) < 3:
            return web.HTTPBadRequest(text='Chat callsign should have 3 or more characters.')
        await db.paramUpdate('users', {'callsign': adminCallsign},
            spliceParams(data, ('email', 'password', 'name', 'chat_callsign', 'pm_enabled')))
    return web.Response(text = 'OK')

async def saveStationSettings(stationCallsign, adminCallsign, settings):
    settings['admin'] = adminCallsign
    await db.paramUpdate('users', {'callsign': adminCallsign}, \
        {'settings': json.dumps(settings)})
    if stationCallsign:
        stationPath = getStationPath(stationCallsign)
        if stationPath:
            with open(stationPath + '/settings.json', 'w') as fSettings:
                json.dump(settings, fSettings, ensure_ascii = False)

def createStationDir(path):
    os.makedirs(path)
    for key, val in jsonTemplates.items():
        with open(f'{path}/{key}.json', 'w') as file:
            json.dump(val, file, ensure_ascii = False)

def decodeToken(data):
    callsign = None
    email = None
    if 'token' in data:
        try:
            payload = jwt.decode(data['token'], SECRET,
                    audience='tnxqso', algorithms=['HS256'])
        except (jwt.exceptions.DecodeError, jwt.exceptions.MissingRequiredClaimError):
            logging.exception('Decode token error')
            return web.HTTPBadRequest(text='Login is expired')
        if 'callsign' in payload:
            callsign = payload['callsign'].lower()
        if 'time' in payload and time.time() - payload['time'] > 60 * 60:
            return web.HTTPBadRequest(text='Token is expired')
        if 'email' in payload:
            email = payload['email']
    if callsign and callsign in BANLIST['callsigns']:
        return web.HTTPBadRequest(text="Account is banned")
    if email and email in BANLIST['emails']:
        return web.HTTPBadRequest(text="Email address is banned")
    if callsign and email:
        return (callsign, email)
    if callsign:
        return callsign
    return web.HTTPBadRequest(text='Not logged in')

def sind(deg):
    return math.sin(math.radians(deg))

def cosd(deg):
    return math.cos(math.radians(deg))

def wfsQuery(wfsType, location, strict=False):
    params = WFS_PARAMS[wfsType]
    url = ('https://r1cf.ru/geoserver/cite/wfs?SERVICE=WFS&REQUEST=GetFeature&TypeName=' +
        '{feature}&VERSION=1.1.0&CQL_FILTER={predi}%28the_geom,POINT%28{lat}%20{lng}%29' +
        '{addParams}%29')
    urlParams = {
        'feature': params['feature'],\
        'predi': 'INTERSECTS' if strict else 'DWITHIN',\
        'lat': location[0],\
        'lng': location[1],\
        'addParams': '' if strict else ',0.0025,kilometers' # ~250 meters
       }
    try:
        rsp = requests.get(url.format_map(urlParams), verify=False, timeout=(0.2, 1))
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

async def getQthData(location, country=None):

    if not country:
        country = get_country(location)
    if country not in ('RU', 'IT', 'GB'):
        country = 'RU'

    data = {'fields': emptyQthFields(country)}
    data['loc'], data['loc8'] = locator(location)

    if country == 'RU':

        rda = '-----'
        allRda = wfsQuery('rda', location)
        strictRda = wfsQuery('rda', location, strict=True)
        if allRda:
            if len(allRda) > 1:
                allRda = [strictRda] + [x for x in allRda if x != strictRda or x == '-----']
                rda = ' '.join(allRda)
            else:
                rda = allRda[0]
        data['fields']['values'][0] = rda

        data['fields']['values'][1] = RAFA_LOCS[data['loc']]\
            if data['loc'] in RAFA_LOCS else None

    elif country == 'IT':
        data['fields']['values'][0] = wfsQuery('waip', location, strict=True)

    elif country == 'GB':
        data['fields']['values'][0] = wfsQuery('wab', location, strict=True)

    return data

def saveQthNowLocation(callsign, location, path):
    qthNowLocations = loadJSON(path)
    if not qthNowLocations:
        qthNowLocations = []
    _ts = int(time.time())
    dtUTC = datetime.utcnow()
    _dt, _tm = dtFmt(dtUTC)
    qthNowLocations = [item for item in qthNowLocations
            if _ts - item['ts'] < 600 and (item['location'][0] != location[0] or
            item['location'][1] != location[1]) and
            (callsign is None or item['callsign'] != callsign)]
    qthNowLocations.append({
        'location': location,
        'ts': _ts,
        'date': _dt,
        'time': _tm,
        'callsign': callsign
    })
    with open(path, 'w') as fLoc:
        json.dump(qthNowLocations, fLoc, ensure_ascii = False)

async def locationHandler(request):
    newData = await request.json()
    callsign = None
    stationPath = None
    stationSettings = None
    stationCallsign = None
    if ('token' in newData and newData['token']):
        callsign = decodeToken(newData)
        if not isinstance(callsign, str):
            return callsign
        stationPath = await getStationPathByAdminCS(callsign)
        stationSettings = loadJSON(stationPath + '/settings.json')
        if not stationSettings:
            return web.HTTPBadRequest(text='Expedition profile is not initialized.')
        if (stationSettings and stationSettings.get('station') and
                stationSettings['station'].get('callsign') and
                stationSettings['station'].get('activityPeriod')):
            actPeriod = [datetime.strptime(dt, '%d.%m.%Y') for dt in
                stationSettings['station']['activityPeriod'] if dt]
            if actPeriod and actPeriod[0] <= datetime.utcnow() <= actPeriod[1] + timedelta(days=1):
                stationCallsign = stationSettings['station']['callsign']

    if 'location' in newData and newData['location']:
        qthNowCs = None
        if 'callsign' in newData and newData['callsign']:
            qthNowCs = newData['callsign']
        elif stationCallsign:
            qthNowCs = stationCallsign

        if qthNowCs:
            qthNowCs = qthNowCs.upper()
            saveQthNowLocation(qthNowCs, newData['location'],
                    webRoot + '/js/qth_now_locations.json')

        saveQthNowLocation(qthNowCs, newData['location'],
                webRoot + '/js/qth_now_locations_all.json')

    if ('token' not in newData or not newData['token']) and 'location' in newData:
        qth = await getQthData(newData['location'])
        return web.json_response({'qth': qth})
    fPath = stationPath + '/status.json'
    data = loadJSON(fPath)
    if not data:
        data = {}
    if not 'locTs' in data and 'ts' in data:
        data['locTs'] = data['ts']
    dtUTC = datetime.utcnow()
    data['ts'] = int(time.time())
    data['date'], data['time'] = dtFmt(dtUTC)
    data['year'] = dtUTC.year
    if 'online' in newData:
        data['online'] = newData['online']
    if 'freq' in newData and newData['freq']:
        data['freq'] = {'value': newData['freq'], 'ts': data['ts']}
        fromCallsign = stationSettings['station']['callsign']
        insertChatMessage(path=stationPath + '/chat.json',
            msgData={'from': fromCallsign,
            'text': '<b><i>' + newData['freq'] + '</b></i>'},
            admin=True)
    country = stationSettings['qthCountry'] if 'qthCountry' in stationSettings else None
    if 'location' in newData and newData['location']:
        location = newData['location']

        country = get_country(location)

        data['qth'] = await getQthData(location, country=country)

        if 'comments' in newData:
            data['comments'] = newData['comments']
        if 'location' in data and data['location']:
            data['prev'] = {'location': data['location'][:], \
                    'ts': data['locTs']}
        data['locTs'] = data['ts']
        data['location'] = newData['location']
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

    if 'qth' in newData:

        if 'qth' not in data:
            data['qth'] = {'fields': emptyQthFields(country=country)}
        for key in newData['qth']['fields'].keys():
            data['qth']['fields']['values'][int(key)] = newData['qth']['fields'][key]
        if 'loc' in newData['qth']:
            data['qth']['loc'] = newData['qth']['loc']

    with open(fPath, 'w') as fStatus:
        json.dump(data, fStatus, ensure_ascii = False)
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

async def exportAdifHandler(request):
    callsign = request.match_info.get('callsign', None)
    if callsign:
        callsign = callsign.replace('-', '/')
    else:
        return web.HTTPBadRequest(text = 'No callsign was specified.')
    log = await logFromDB(callsign, limit=False)

    adif = """ADIF Export from TNXLOG
    Logs generated @ """ + time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()) + "\n<EOH>\n"

    def adifField(name, data):
        dataStr = str(data) if data else ''
        return f"<{name.upper()}:{len(dataStr)}>{dataStr} "

    for qso in log:
        qsoTime = time.gmtime(qso['qso_ts'])
        try:
            adif += (
                    adifField("CALL", qso['cs']) +
                    adifField("QSO_DATE", time.strftime("%Y%m%d", qsoTime)) +
                    adifField("TIME_OFF", time.strftime("%H%M%S", qsoTime)) +
                    adifField("TIME_ON", time.strftime("%H%M%S", qsoTime)) +
                    adifField("BAND", BANDS_WL[qso['band']]) +
                    adifField("STATION_CALLSIGN", qso['myCS']) +
                    adifField("FREQ", str(Decimal(qso['freq'])/1000)) +
                    adifField("MODE", qso['mode']) +
                    adifField("RST_RCVD", qso['rcv']) +
                    adifField("RST_SENT", qso['snt']) +
                    adifField("MY_GRIDSQUARE", qso['loc']) +
                    adifField("GRIDSQUARE", qso['loc_rcv'] if 'loc_rcv' in qso else None))
        except Exception:
            logging.exception('Error while adif conversion. QSO:')
            logging.error(qso)

        for fieldNo, val in enumerate(qso['qth']):
            adif += adifField(f'QTH_FIELD_{fieldNo + 1}', val)
        adif += "<EOR>\n"

    return web.Response(
            headers={
                'Content-Disposition':
                    f'Attachment;filename={callsign + datetime.now().strftime("_%d_%b_%Y")}.adi'
            },
            body=adif.encode()
        )

async def newsHandler(request):
    data = await request.json()
    callsign = decodeToken(data)
    if not isinstance(callsign, str):
        return callsign
    if not (await getUserData(callsign))['email_confirmed']:
        return web.HTTPUnauthorized(text='Email is not confirmed')

    stationPath = getStationPath(data['station'])
    stationSettings = loadJSON(stationPath + '/settings.json')
    if callsign != stationSettings['admin'] and not callsign in siteAdmins:
        return web.HTTPUnauthorized(\
            text = 'You must be logged in as station admin')
    newsPath = stationPath + '/news.json'
    news = loadJSON(newsPath)
    if not news:
        news = []
    if 'add' in data:
        news.insert(0, {'ts': time.time(), 'text': data['add'],\
            'time': datetime.now().strftime('%d %b %H:%M').lower()})
    if 'clear' in data:
        news = []
    if 'delete' in data:
        news = [x for x in news if x['ts'] != data['delete']]
    with open(newsPath, 'w') as fNews:
        json.dump(news, fNews, ensure_ascii = False)
    return web.Response(text = 'OK')

async def activeUsersHandler(request):
    data = await request.json()
    callsign = decodeToken(data)
    if not isinstance(callsign, str):
        return callsign
    if not data.get('chat_callsign'):
        return web.Response(text = 'OK')
    station = data['station'] if 'station' in data else None
    if station:
        stationPath = getStationPath(data['station'])
        stationSettings = loadJSON(stationPath + '/settings.json')
        if not stationSettings:
            return web.HTTPBadRequest(text = 'This station was deleted or moved')
    auPath = webRoot + '/js/activeUsers.json'
    auData = loadJSON(auPath)
    nowTs = int(datetime.now().timestamp())
    if not auData:
        auData = {}
    auData = {key: val for key, val in auData.items() if nowTs - val['ts'] < 120}
    auData[callsign] = {
            'chat': data.get('chat'),
            'ts': nowTs,
            'station': station,
            'callsign': callsign,
            'pm_enabled': data.get('pm_enabled'),
            'chat_callsign': data.get('chat_callsign'),
            'name': data.get('name'),
            'typing': data.get('typing')
            }
    with open(auPath, 'w') as fAu:
        json.dump(auData, fAu, ensure_ascii = False)
    return web.Response(text = 'OK')

async def readMultipart(request):
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

async def galleryHandler(request):
    data = None
    if 'multipart/form-data;' in request.headers[aiohttp.hdrs.CONTENT_TYPE]:
        data = await readMultipart(request)
    else:
        data = await request.json()
    callsign = decodeToken(data)
    if not (await getUserData(callsign))['email_confirmed']:
        return web.HTTPUnauthorized(text='Email is not confirmed')
    if not isinstance(callsign, str):
        return callsign
    stationPath = await getStationPathByAdminCS(callsign)
    galleryPath = stationPath + '/gallery'
    galleryDataPath = stationPath + '/gallery.json'
    galleryData = loadJSON(galleryDataPath)
    siteGalleryParams = conf['gallery']
    if not galleryData:
        galleryData = []
    if 'file' in data:
        if not os.path.isdir(galleryPath):
            os.mkdir(galleryPath)
        file = data['file']['contents']
        fileNameBase = uuid.uuid4().hex
        fileExt = data['file']['name'].rpartition('.')[2]
        fileName = fileNameBase + '.' + fileExt
        fileType = 'image' if 'image'\
            in data['file']['type'] else 'video'
        filePath = galleryPath + '/' + fileName
        with open(filePath, 'wb') as fImg:
            fImg.write(file)
        tnSrc = filePath
        if fileType == 'video':

            tnSrc = galleryPath + '/' + fileNameBase + '.jpeg'
            (
                ffmpeg
                    .input(filePath)
                    .output(tnSrc, vframes=1)
                    .run()
            )

        with Image(filename=tnSrc) as img:
            with Image(width=img.width, height=img.height,
                    background=Color("#EEEEEE")) as bgImg:

                bgImg.composite(img, 0, 0)

                exif = {}
                exif.update((key[5:], val) for key, val in img.metadata.items() if
                        key.startswith('exif:'))
                if 'Orientation' in exif:
                    if exif['Orientation'] == '3':
                        bgImg.rotate(180)
                    elif exif['Orientation'] == '6':
                        bgImg.rotate(90)
                    elif exif['Orientation'] == '8':
                        bgImg.rotate(270)

                size = img.width if img.width < img.height else img.height
                bgImg.crop(width=size, height=size, gravity='north')
                bgImg.resize(200, 200)
                bgImg.format = 'jpeg'
                bgImg.save(filename=f'{galleryPath}/{fileNameBase}_thumb.jpeg')
                if fileType == 'image':
                    maxHeight, maxWidth = (int(siteGalleryParams['max_height']),
                            int(siteGalleryParams['max_width']))
                    if img.width > maxWidth or img.height > maxHeight:
                        coeff = min(maxWidth/img.width, maxHeight/img.height)
                        img.resize(width=int(coeff*img.width), height=int(coeff*img.height))
                        img.compression_quality = int(siteGalleryParams['quality'])
                        img.save(filename=filePath)
        if fileType == 'video':
            os.unlink(tnSrc)
        galleryData.insert(0, {
            'file': f'gallery/{fileName}',
            'thumb': f'gallery/{fileNameBase}_thumb.jpeg',
            'caption': data['caption'],
            'type': fileType,
            'ts': time.time(),
            'datetime': datetime.utcnow().strftime('%d %b %Y %H:%M').lower(),
            'id': fileNameBase})
        maxCount = int(siteGalleryParams['max_count'])
        if len(galleryData) > maxCount:
            galleryData = sorted(galleryData, key=lambda item: item['ts'], reverse=True)[:maxCount]

    if 'delete' in data:
        items = [x for x in galleryData if x['id'] == data['delete']]
        if items:
            item = items[0]
            galleryData = [x for x in galleryData if x != item]
            deleteGalleryItem(stationPath, item)
    if 'clear' in data:
        for item in galleryData:
            deleteGalleryItem(stationPath, item)
        galleryData = []
    with open(galleryDataPath, 'w') as fGallery:
        json.dump(galleryData, fGallery, ensure_ascii = False)
    return web.Response(text='OK')

def deleteGalleryItem(stationPath, item):
    os.unlink(stationPath + '/' + item['file'])

async def trackHandler(request):
    data = await request.json()
    callsign = decodeToken(data)
    if not isinstance(callsign, str):
        return callsign
    if not (await getUserData(callsign))['email_confirmed']:
        return web.HTTPUnauthorized(text='Email is not confirmed')

    stationPath = await getStationPathByAdminCS(callsign)
    trackJsonPath = stationPath + '/track.json'
    trackJson = {'version': time.time(), 'file': 'track.xml'}
    if 'file' in data:
        trackJson['filename'] = data['name']
        file = base64.b64decode(data['file'].split(',')[1])
        if data['name'].lower().endswith('kmz'):
            with zipfile.ZipFile(io.BytesIO(file), 'r') as zFile:
                for fItem in zFile.infolist():
                    if fItem.filename.endswith('kml'):
                        trackJson['file'] = fItem.filename
                    zFile.extract(fItem, path = stationPath)
        else:
            with open(stationPath + '/track.xml', 'wb') as fTrack:
                fTrack.write(file)
        with open(trackJsonPath, 'w') as fJsonPath:
            json.dump(trackJson, fJsonPath)
    if 'clear' in data and os.path.isfile(trackJsonPath):
        os.remove(trackJsonPath)
    return web.Response(text = 'OK')

async def logFromDB(callsign, limit=True):
    log = []
    limit_clause = f" limit{conf['web'].getint('log_page_length')}" if limit else ''
    data = await db.execute(
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

async def dbInsertQso(callsign, qso):
    await db.execute("""
        insert into log (callsign, qso) 
        values (%(callsign)s, %(qso)s)""",
        {'callsign': callsign, 'qso': json.dumps(qso)})

async def logHandler(request):
    data = await request.json()
    callsign = decodeToken(data)
    if not isinstance(callsign, str):
        return callsign
    if not (await getUserData(callsign))['email_confirmed']:
        return web.HTTPUnauthorized(text='Email is not confirmed')

    stationPath = await getStationPathByAdminCS(callsign)
    logPath = stationPath + '/log.json'
    log = []
    if not os.path.isfile(logPath):
        logging.exception('%s not found', logPath)
    try:
        with open(logPath) as fLog:
            log = json.load(fLog)
    except Exception as ex:
        logging.error("Error loading qso log %s", logPath)
        logging.exception(ex)
        log = await logFromDB(callsign)

    if 'qso' in data:

        rsp = []

        async def processQso(qso):
            try:
                dtime = datetime.strptime(qso['ts'], "%Y-%m-%d %H:%M:%S")
                qso['date'], qso['time'] = dtFmt(dtime)
                qso['qso_ts'] = (dtime - datetime(1970, 1, 1)) / timedelta(seconds=1)
            except (ValueError, TypeError) as exc:
                logging.error("Error parsing qso timestamp %s", qso['ts'])
                logging.exception(exc)
                return {'ts': None}

            serverTs = qso.pop('serverTs') if 'serverTs' in qso else None

            if serverTs:
                qso['ts'] = serverTs
                qsoIdx = [i[0] for i in enumerate(log) if i[1]['ts'] == qso['ts']]
                if qsoIdx:
                    log[qsoIdx[0]] = qso
                else:
                    log.append(qso)
                dbUpdate = await db.execute("""
                    update log set qso = %(qso)s
                    where callsign = %(callsign)s and (qso->>'ts')::float = %(ts)s""",
                    {'callsign': callsign, 'ts': qso['ts'], 'qso': json.dumps(qso)})
                if not dbUpdate:
                    await dbInsertQso(callsign, qso)

            else:
                newQso = True
                if log:
                    for logQso in log:
                        sameFl = True
                        for key in qso:
                            if key not in ('ts', 'rda', 'wff', 'comments',
                                'serverTs', 'qso_ts', 'qth', 'no') and (
                                        key not in logQso or qso[key] != logQso[key]):
                                sameFl = False
                                break
                        if sameFl:
                            logging.debug('prev qso found:')
                            newQso = False
                            qso['ts'] =  logQso['ts']
                            logQso['qso_ts'] = qso['qso_ts']

                if newQso:
                    statusPath = stationPath + '/status.json'
                    statusData = loadJSON(statusPath)
                    _ts = dtime.timestamp() + tzOffset()
                    if ('freq' not in statusData or statusData['freq']['ts'] < _ts):
                        statusData['freq'] = {'value': qso['freq'], 'ts': _ts}
                        with open(statusPath, 'w') as fStatus:
                            json.dump(statusData, fStatus, ensure_ascii = False)

                    qso['ts'] = time.time()
                    while [x for x in log if x['ts'] == qso['ts']]:
                        qso['ts'] += 0.00000001
                    log.insert(0, qso)
                    await dbInsertQso(callsign, qso)

            return {'ts': qso['ts']}

        for qso in data['qso']:
            rsp.append((await processQso(qso)))

        log = sorted(log, key=lambda qso: qso['qso_ts'] if 'qso_ts' in qso else qso['ts']/10,\
                reverse=True)
        log = log[:conf['web'].getint('log_page_length')]
        with open(logPath, 'w') as fLog:
            json.dump(log, fLog)

        return web.json_response(rsp)

    if 'delete' in data:
        log = [x for x in log if x['ts'] != data['delete']]
        await db.execute("""
            delete from log 
            where callsign = %(callsign)s and (qso->>'ts')::float = %(ts)s""",
            {'callsign': callsign, 'ts': data['delete']})

    if 'clear' in data:
        log = []
        await db.execute(
            "delete from log where callsign = %(callsign)s",
            {'callsign': callsign})

    with open(logPath, 'w') as fLog:
        json.dump(log, fLog)
    return web.Response(text = 'OK')

async def logSearchHandler(request):
    reqData = await request.json()
    if not reqData.get('station'):
        return web.HTTPBadRequest(text='Invalid search params')
    result = []
    csFilter = "and qso->>'cs' = %(callsign)s" if reqData.get('callsign') else ''
    dbData = await db.execute(
        f"""select id, qso from log 
            where callsign = %(station)s {csFilter}
            order by id desc""",
            reqData)
    if dbData:
        if isinstance(dbData, dict):
            result.append(dbData['qso'])
        else:
            result = [row['qso'] for row in dbData]
    return web.json_response(result)

def replace0(val):
    return val.replace("0", "\u00D8")

async def banUserHandler(request):
    data = await request.json()
    callsign = decodeToken(data)
    if not isinstance(callsign, str):
        return callsign
    if callsign not in siteAdmins:
        return web.HTTPUnauthorized(text='You must be logged in as site admin')
    userData = await getUserData(data['user'])
    altLogins = await db.execute(
            """select callsign
                from users
                where email = %(email)s and callsign <> %(callsign)s""", userData)
    if altLogins:
        if isinstance(altLogins, dict):
            userData['alts'] = [altLogins['callsign']]
        else:
            userData['alts'] = [row['callsign'] for row in altLogins]
    else:
        userData['alts'] = []
    if 'query' in data:
        return web.json_response({
            'login': userData['callsign'],
            'email': userData['email'],
            'alts': userData['alts']
            })
    if data.get('unban'):
        if userData['callsign'] in BANLIST['callsigns']:
            BANLIST['callsigns'].remove(userData['callsign'])
        if 'alts' in userData:
            for alt in userData['alts']:
                if alt in BANLIST['callsigns']:
                    BANLIST['callsigns'].remove(alt)
        if userData['email'] in BANLIST['emails']:
            BANLIST['emails'].remove(userData['email'])
    else:
        if userData['callsign'] not in BANLIST['callsigns']:
            BANLIST['callsigns'].append(userData['callsign'])
        if 'alts' in userData:
            for alt in userData['alts']:
                if alt not in BANLIST['callsigns']:
                    BANLIST['callsigns'].append(alt)
        if userData['email'] not in BANLIST['emails']:
            BANLIST['emails'].append(userData['email'])
    with open(webRoot + '/js/banlist.json', 'w') as fBl:
        json.dump(BANLIST, fBl)
    return web.Response(text='OK')

async def usersListHandler(request):
    data = await request.json()
    callsign = decodeToken(data)
    if not isinstance(callsign, str):
        return callsign
    if not callsign in siteAdmins:
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
    ulist = await db.execute(f"""
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

async def userEditHandler(request):
    data = await request.json()
    callsign = decodeToken(data)
    if not isinstance(callsign, str):
        return callsign
    if not callsign in siteAdmins:
        return web.HTTPUnauthorized(\
            text='You must be logged in as site admin')
    await db.paramUpdate('users', {'callsign': data['callsign']},
            spliceParams(data, ['verified', 'email_confirmed']))
    return web.Response(text = 'OK')

async def chatHandler(request):
    data = await request.json()
    admin = False
    callsign = decodeToken(data)
    if not isinstance(callsign, str):
        return callsign
    if not (await getUserData(callsign))['email_confirmed']:
        return web.HTTPUnauthorized(text='Email is not confirmed')
    chatPath = ''
    station = data['station'] if 'station' in data else None
    chat = []
    if station:
        stationPath = getStationPath(data['station'])
        stationSettings = loadJSON(stationPath + '/settings.json')
        admins = [x.lower() for x in\
            stationSettings['chatAdmins'] + [ stationSettings['admin'], ]]
        admin = callsign in admins
        chatPath = stationPath + '/chat.json'
        chatAccess = stationSettings.get('chatAccess')
        if chatAccess:
            if chatAccess == 'users' and not isinstance(callsign, str):
                return web.HTTPUnauthorized(text='Not logged in')
            if chatAccess == 'admins' and not admin:
                return web.HTTPUnauthorized(text='Station admin required')
    else:
        chatPath = webRoot + '/js/talks.json'
        admin = callsign in siteAdmins
    if not 'clear' in data and not 'delete' in data:
        data['cs'] = callsign
        insertChatMessage(path=chatPath, msgData=data, admin=admin)
    else:
        admins = siteAdmins + [stationSettings['admin'],] if station else siteAdmins
        if not callsign in admins:
            return web.HTTPUnauthorized(\
                text='You must be logged in as station or site admin')
        if 'delete' in data:
            chat = loadJSON(chatPath)
            if not chat:
                chat = []
            chat = [ x for x in chat if x['ts'] != data['delete'] ]
        with open(chatPath, 'w') as fChat:
            json.dump(chat, fChat, ensure_ascii = False)
    return web.Response(text = 'OK')

def insertChatMessage(path, msgData, admin):
    CHAT_MAX_LENGTH = int(conf['chat']['max_length'])
    chat = loadJSON(path)
    if not chat:
        chat = []
    msg = {'user': msgData['from'],
            'text': msgData['text'],
            'cs': msgData.get('cs') or msgData['from'],
            'admin': admin, 'ts': time.time()}
    msg['date'], msg['time'] = dtFmt(datetime.utcnow())
    if 'name' in msgData:
        msg['name'] = msgData['name']
    chat.insert(0, msg)
    chatTrunc = []
    chatAdm = []
    for msg in chat:
        if msg['text'].startswith('***') and msg['admin']:
            chatAdm.append(msg)
        elif len(chatTrunc) < CHAT_MAX_LENGTH:
            chatTrunc.append(msg)
    chat = chatAdm + chatTrunc
    with open(path, 'w') as fChat:
        json.dump(chat, fChat, ensure_ascii = False)

async def sendSpotHandler(request):
    global lastSpotSent
    data = await request.json()
    now = datetime.now().timestamp()
    response = {'sent': False,
            'secondsLeft': conf.getint('cluster', 'spotInterval')}
    if not lastSpotSent or now - lastSpotSent > response['secondsLeft']:
        lastSpotSent = now
        protocol = await  clusterProtocol.connect(APP.loop, \
            call = data['userCS'],
            host = conf.get('cluster', 'host'),
            port = conf.get('cluster', 'port'))

        def sendSpot():
            protocol.write('dx ' + data['cs'] + ' ' + data['freq'] + ' ' + \
                data['info'])
            response['sent'] = True
            APP.loop.call_later(1, protocol.close)

        if protocol:
            logging.debug('Protocol connected')
            protocol.onLoggedIn.append(sendSpot)
            await protocol.waitDisconnected()
            if not response['sent']:
                response['reply'] = protocol.latestReply
    else:
        response['secondsLeft'] -= now - lastSpotSent
    return web.json_response(response)


if __name__ == '__main__':
    APP = web.Application(client_max_size = 200 * 1024 ** 2)
    APP.router.add_post('/aiohttp/login', loginHandler)
    APP.router.add_post('/aiohttp/userSettings', userSettingsHandler)
    APP.router.add_post('/aiohttp/news', newsHandler)
    APP.router.add_post('/aiohttp/track', trackHandler)
    APP.router.add_post('/aiohttp/chat', chatHandler)
    APP.router.add_post('/aiohttp/activeUsers', activeUsersHandler)
    APP.router.add_post('/aiohttp/log', logHandler)
    APP.router.add_post('/aiohttp/logSearch', logSearchHandler)
    APP.router.add_post('/aiohttp/location', locationHandler)
    APP.router.add_post('/aiohttp/publish', publishHandler)
    APP.router.add_post('/aiohttp/passwordRecoveryRequest',
            passwordRecoveryRequestHandler)
    APP.router.add_post('/aiohttp/confirmEmailRequest',
            confirmEmailRequestHandler)
    APP.router.add_get('/aiohttp/confirmEmail',
            confirmEmailLinkHandler)
    APP.router.add_get('/aiohttp/suspicious',
            suspiciousHandler)

    APP.router.add_post('/aiohttp/contact', contactHandler)
    APP.router.add_post('/aiohttp/userData', userDataHandler)
    APP.router.add_post('/aiohttp/gallery', galleryHandler)
    APP.router.add_post('/aiohttp/sendSpot', sendSpotHandler)
    APP.router.add_post('/aiohttp/privateMessages/post', privateMessagesPostHandler)
    APP.router.add_post('/aiohttp/privateMessages/get', privateMessagesGetHandler)
    APP.router.add_post('/aiohttp/privateMessages/delete', privateMessagesDeleteHandler)
    APP.router.add_post('/aiohttp/privateMessages/read', privateMessagesReadHandler)
    APP.router.add_post('/aiohttp/banUser', banUserHandler)
    APP.router.add_post('/aiohttp/users', usersListHandler)
    APP.router.add_post('/aiohttp/editUser', userEditHandler)

    APP.router.add_get('/aiohttp/adif/{callsign}', exportAdifHandler)

    db.verbose = True

    async def onStartup(_):
        await db.connect()

    APP.on_startup.append(onStartup)

    web.run_app(APP, path = conf.get('sockets', 'srv'))
