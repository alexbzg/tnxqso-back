#!/usr/bin/python3
#coding=utf-8

import argparse, asyncio, logging, logging.handlers, aiohttp, jwt, os, base64, \
        json, time, math, smtplib, shutil, io, zipfile, pwd, grp
from datetime import datetime
from aiohttp import web
from common import siteConf, loadJSON, appRoot, startLogging, \
        createFtpUser, setFtpPasswd, dtFmt, tzOffset
from tqdb import DBConn, spliceParams
import clusterProtocol
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication


parser = argparse.ArgumentParser(description="tnxqso backend aiohttp server")
parser.add_argument('--test', action = "store_true" )
args = parser.parse_args()

conf = siteConf()
webRoot = conf.get( 'web', 'root_test' if args.test else 'root' )
webAddress = conf.get( 'web', 'address_test' if args.test else 'address' )
siteAdmins = conf.get( 'web', 'admins' ).split( ' ' )

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

defUserSettings = loadJSON( webRoot + '/js/defaultUserSettings.json' )
if not defUserSettings:
    defUserSettings = {}

jsonTemplates = { 'settings': defUserSettings, \
    'log': [], 'chat': [], 'news': [], 'cluster': [], 'status': {}, \
    'chatUsers': {} }

app = None
lastSpotSent = None

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
    server.login( myAddress, conf.get( 'email', 'password' ) )
    server.sendmail( myAddress, msg['to'], str( msg ) )

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
            if not userData or userData['password'] != data['password']:
                error = 'Wrong callsign or password.'            
    if error:
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
    yield from db.paramUpdate( 'users', { 'callsign': adminCallsign }, \
        { 'settings': json.dumps( settings ) } )
    if stationCallsign:
        stationPath = getStationPath( stationCallsign )
        if stationPath:
            settings['admin'] = adminCallsign
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
    dtUTC = datetime.utcnow()
    data['ts'] = int( dtUTC.timestamp() + tzOffset() ) 
    data['date'], data['time'] = dtFmt( dtUTC )    
    data['year'] = dtUTC.year
    data['loc'] = newData['loc'] if 'loc' in newData else None
    data['rafa'] = newData['rafa'] if 'rafa' in newData else None
    data['rda'] = newData['rda'] if 'rda' in newData else None
    data['wff'] = newData['wff'] if 'wff' in newData else None
    data['userFields'] = newData['userFields']
    if 'online' in newData:
        data['online'] = newData['online']
    if 'location' in newData and newData['location']:
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
    admins = []
    if 'station' in data:
        stationPath = getStationPath( data['station'] )
        stationSettings = loadJSON( stationPath + '/settings.json' )
        if not stationSettings:
            return web.HTTPBadRequest( text = 'This station was deleted or moved' )
        admins = stationSettings['chatAdmins'] + [ stationSettings['admin'] ]
    else:
        admins = siteAdmins
    auPath = stationPath + '/activeUsers.json'
    au = loadJSON( auPath )
    nowTs = int( datetime.now().timestamp() ) 
    if not au:
        au = {}
    au = { k : v for k, v in au.items() \
            if nowTs - v['ts'] < 120 }
    au[data['user']] = { 'chat': data['chat'], 'ts': nowTs, \
            'admin': data['user'] in admins, \
            'station': data['station'] if 'station' in data else None,\
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
        qso = data['qso']
        dt = datetime.strptime( qso['ts'], "%Y-%m-%d %H:%M:%S" )
        qso['date'], qso['time'] = dtFmt( dt )
        sameFl = True
        if log:
            for key in qso:
                if not key in ('ts', 'rda', 'wff') and qso[key] != log[0][key]:
                    sameFl = False
                    break
        else:
            sameFl = False
        if not sameFl:
            if qso['rda']:
                qso['rda'] = qso['rda'].upper()
            if qso['wff']:
                qso['wff'] = qso['wff'].upper()
            qso['ts'] = time.time()
            log.insert( 0, qso )
            yield from db.execute( 
                "insert into log (callsign, qso) values ( %(callsign)s, %(qso)s )",
                { 'callsign': callsign, 'qso': json.dumps( qso ) } )

    if 'clear' in data:
        log = []
    with open( logPath, 'w' ) as f:
        json.dump( log, f )
    return web.Response( text = 'OK' )

def replace0( val ):
    return val.replace( "0", u"\u00D8" )


@asyncio.coroutine
def chatHandler(request):
    data = yield from request.json()
    stationPath = getStationPath( data['station'] )
    stationSettings = loadJSON( stationPath + '/settings.json' )
    chatAdmins = stationSettings['chatAdmins'] + \
            [ stationSettings['admin'], ]
    admin = 'from' in data and data['from'] in chatAdmins
    if 'clear' in data or 'delete' in data:
        callsign = decodeToken( data )
        if not isinstance( callsign, str ):
            return callsign
        if not callsign in chatAdmins and not callsign in siteAdmins:
            return web.HTTPUnauthorized( \
                text = 'You must be logged in as station or site admin' )
    chatPath = stationPath + '/chat.json'
    chat = []
    if not 'clear' in data:
        chat = loadJSON( chatPath )
        if not chat:
            chat = []
        if 'delete' in data:
            chat = [ x for x in chat if x['ts'] != data['delete'] ]
        else:
            msg = { 'user': data['from'], \
                    'text': data['text'], \
                    'admin': admin, 'ts': time.time() }
            msg['date'], msg['time'] = dtFmt( datetime.utcnow() )
            if 'name' in data:
                msg['name'] = data['name']
            chat.insert( 0, msg )
    if len( chat ) > 100:
        chat = chat[:100]
    with open( chatPath, 'w' ) as f:
        json.dump( chat, f, ensure_ascii = False )
    return web.Response( text = 'OK' )


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
    app = web.Application( client_max_size = 10 * 1024 ** 2 )
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
    app.router.add_post('/aiohttp/sendSpot', sendSpotHandler )
    db.verbose = True
    asyncio.async( db.connect() )

    args = parser.parse_args()
    web.run_app(app, path = conf.get( 'sockets', 'srv_test' if args.test else 'srv' ) )
