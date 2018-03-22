#!/usr/bin/python3
#coding=utf-8

import argparse, asyncio, logging, logging.handlers, aiohttp, jwt, os, base64, \
        json, time, math
from datetime import datetime
from aiohttp import web
from common import siteConf, loadJSON, appRoot, startLogging
from tqdb import DBConn, spliceParams

parser = argparse.ArgumentParser(description="tnxqso backend aiohttp server")
parser.add_argument('--test', action = "store_true" )
args = parser.parse_args()

conf = siteConf()
webRoot = conf.get( 'web', 'root_test' if args.test else 'root' )

startLogging( 'srv_test' if args.test else 'srv' )
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

defUserSettings = loadJSON( appRoot + '/defaultUserSettings.json' )
if not defUserSettings:
    defUserSettings = {}

jsonTemplates = { 'settings': defUserSettings, \
    'log': {}, 'chat': {}, 'news': {}, 'cluster': {}, 'status': {}, \
    'chatUsers': {} }

def dtFmt( dt ):
    return dt.strftime( '%d %b' ).lower(), dt.strftime( '%H:%Mz' )

@asyncio.coroutine
def checkRecaptcha( response ):
    try:
        rcData = { 'secret': conf.get( 'recaptcha', 'secret' ),\
                'response': response }
        with aiohttp.ClientSession() as session:
            resp = yield from session.post( \
                    conf.get( 'recaptcha', 'verifyURL' ), data = rcData )
            respData = yield from resp.json()
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
def loginHandler(request):
    error = None
    data = yield from request.json()
    userData = False
    if not 'login' in data or len( data['login'] ) < 2:
        error = 'Minimal login length is 2 symbols'
    if not 'password' in data or len( data['password'] ) < 6:
        error = 'Minimal password length is 6 symbols'
    if not error:
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
                        'settings': json.dumps( defUserSettings ) }, True )                    
        else:
            if not userData or userData['password'] != data['password']:
                error = 'Wrong callsign or password.'            
    if error:
        return web.HTTPBadRequest( text = error )
    else:
        userData['token'] = jwt.encode( { 'callsign': data['login'] }, \
                secret, algorithm='HS256' ).decode('utf-8') 
        del userData['password']
        return web.json_response( userData )

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
    oldData = yield from getUserData( callsign )
    oldCs = oldData['settings']['station']['callsign']
    stationPath = getStationPath( oldCs ) if oldCs else None
    if oldCs != data['settings']['station']['callsign']:
        newCs = data['settings']['station']['callsign'] 
        newPath = getStationPath( newCs ) if newCs else None
        if newCs:
            if os.path.exists( newPath ):
                return web.HTTPBadRequest( \
                    text = 'Station callsign ' + newCS.upper() + \
                        'is already registered' )
            if oldCs:
                if os.path.exists( stationPath ):
                    os.rename( stationPath, newPath )
                else:
                    createStationDir( newPath )
        else:
            if stationPath and os.file.exists( stationPath ):
                os.remove( stationPath )
        stationPath = newPath
    yield from db.paramUpdate( 'users', { 'callsign': callsign }, \
        { 'settings': json.dumps( data['settings'] ) } )
    if stationPath:
        if not os.path.exists( stationPath ):
            createStationDir( stationPath )
        data['settings']['admin'] = callsign
        with open( stationPath + '/settings.json', 'w' ) as f:
            json.dump( data['settings'], f, ensure_ascii = False )
    return web.Response( text = 'OK' )

def createStationDir( path ):
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
            callsign = pl['callsign']
    return callsign if callsign else web.HTTPBadRequest( text = 'Not logged in' )

def sind( d ):
    return math.sin( math.radians(d) )

def cosd( d ):
    return math.cos( math.radians(d) )

@asyncio.coroutine
def locationHandler( request ):
    newData = yield from request.json()
    callsign = decodeToken( newData )
    if not isinstance( callsign, str ):
        return callsign
    stationPath = yield from getStationPathByAdminCS( callsign )
    fp = stationPath + '/status.json'
    data = loadJSON( fp )
    if not data:
        data = {}
    if not 'locTs' in data and 'ts' in data:
        data['locTs'] = data['ts']
    data['ts'] = int( datetime.now().strftime("%s") ) 
    dtUTC = datetime.utcnow()
    data['date'], data['time'] = dtFmt( dtUTC )    
    data['year'] = dtUTC.year
    data['loc'] = newData['loc']
    data['rafa'] = newData['rafa']
    data['rda'] = newData['rda']
    if newData['location']:
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
            data['speed'] = d / ( float( data['locTs'] - data['prev']['ts'] ) \
                    / 3600 )
    with open( fp, 'w' ) as f:
        json.dump( data, f, ensure_ascii = False )
    return web.Response( text = 'OK' )

@asyncio.coroutine
def newsHandler(request):
    data = yield from request.json()
    callsign = decodeToken( data )
    if not isinstance( callsign, str ):
        return callsign
    stationPath = yield from getStationPathByAdminCS( callsign )
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
    stationPath = getStationPath( data['station'] )
    auPath = stationPath + '/activeUsers.json'
    stationSettings = loadJSON( stationPath + '/settings.json' )
    stationAdmins = stationSettings['chatAdmins'] + [ stationSettings['admin'] ]
    au = loadJSON( auPath )
    nowTs = time.time()
    if not au:
        au = {}
    au = { k : v for k, v in au.items() \
            if nowTs - v['ts'] > 120 }
    au[data['user']] = { 'chat': data['chat'], 'ts': nowTs, \
            'admin': data['user'] in stationAdmins, \
            'typing': data['typing'] }
    with open( auPath, 'w' ) as f:
        json.dump( au, f, ensure_ascii = False )
    return web.Response( text = 'OK' )


@asyncio.coroutine
def trackHandler(request):
    data = yield from request.json()
    callsign = decodeToken( data )
    if not isinstance( callsign, str ):
        return callsign
    stationPath = yield from getStationPathByAdminCS( callsign )
    trackJsonPath = stationPath + '/track.json'
    if 'file' in data:
        with open( stationPath + '/track.xml', 'wb' ) as f:
            f.write( base64.b64decode( data['file'].split( ',' )[1] ) )
        with open( trackJsonPath, 'w' ) as fj:
            json.dump( { 'version': time.time() }, fj )
    if 'clear' in data:
        os.remove( trackJsonPath )
    return web.Response( text = 'OK' )

@asyncio.coroutine
def logHandler(request):
    data = yield from request.json()
    callsign = decodeToken( data )
    if not isinstance( callsign, str ):
        return callsign
    stationPath = yield from getStationPathByAdminCS( callsign )
    logPath = stationPath + '/log.json'
    log = loadJSON( logPath )
    if 'qso' in data:
        if not log:
            log = []
        qso = data['qso']
        dt = datetime.strptime( qso['ts'], "%Y-%m-%d %H:%M:%S" )
        if qso['rda']:
            qso['rda'] = qso['rda'].upper()
        if qso['wff']:
            qso['wff'] = qso['wff'].upper()
        qso['date'], qso['time'] = dtFmt( dt )
        qso['ts'] = time.time()
        log.insert( 0, qso )
    if 'clear' in data:
        log = []
    with open( logPath, 'w' ) as f:
        json.dump( log, f )
    return web.Response( text = 'OK' )


@asyncio.coroutine
def chatHandler(request):
    data = yield from request.json()
    stationPath = getStationPath( data['station'] )
    stationSettings = loadJSON( stationPath + '/settings.json' )
    chatAdmins = stationSettings['chatAdmins'] + [ stationSettings['admin'], ]
    admin = False
    if ( 'from' in data and data['from'] in chatAdmins ) \
        or 'clear' in data or 'delete' in data:
        callsign = decodeToken( data )
        if not isinstance( callsign, str ):
            return callsign
        if not callsign in chatAdmins:
            return web.HTTPUnauthorized( \
                text = 'You must be logged in as chat admin' )
        admin = True    
    chatPath = stationPath + '/chat.json'
    chat = []
    if not 'clear' in data:
        chat = loadJSON( chatPath )
        if not chat:
            chat = []
        if 'delete' in data:
            chat = [ x for x in chat if x['ts'] != data['delete'] ]
        else:
            msg = { 'user': data['from'], 'text': data['text'], \
                    'admin': admin, 'ts': time.time() }
            msg['date'], msg['time'] = dtFmt( datetime.utcnow() )
            chat.insert( 0, msg )
    with open( chatPath, 'w' ) as f:
        json.dump( chat, f, ensure_ascii = False )
    return web.Response( text = 'OK' )
 
if __name__ == '__main__':
    app = web.Application( client_max_size = 10 * 1024 ** 2 )
    app.router.add_post('/aiohttp/login', loginHandler)
    app.router.add_post('/aiohttp/userSettings', userSettingsHandler)
    app.router.add_post('/aiohttp/news', newsHandler)
    app.router.add_post('/aiohttp/track', trackHandler)
    app.router.add_post('/aiohttp/chat', chatHandler)
    app.router.add_post('/aiohttp/activeUsers', activeUsersHandler)
    app.router.add_post('/aiohttp/log', logHandler)
    app.router.add_post('/aiohttp/location', locationHandler)
    db.verbose = True
    asyncio.async( db.connect() )

    args = parser.parse_args()
    web.run_app(app, path = conf.get( 'sockets', 'srv_test' if args.test else 'srv' ) )
