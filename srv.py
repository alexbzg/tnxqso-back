#!/usr/bin/python3
#coding=utf-8

import argparse, asyncio, logging, logging.handlers, aiohttp, jwt, os, base64, \
        json, time
from datetime import datetime
from aiohttp import web
from common import siteConf, loadJSON, appRoot, startLogging
from tqdb import db, spliceParams

parser = argparse.ArgumentParser(description="tnxqso backend aiohttp server")
parser.add_argument('--path')
parser.add_argument('--port')

conf = siteConf()
webRoot = conf.get( 'web', 'root' )

startLogging( 'srv' )
logging.debug( "restart" )

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
    'log': {}, 'chat': {}, 'news': {}, 'cluster': {}, 'status': {} }



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
        if data['newUser']:
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
                    if userData:
                        callsign = data['login']
                        stationPath = getStationPath( callsign )
                        if not os.path.exists( stationPath ):
                            os.makedirs( stationPath )
                        for k, v in jsonTemplates.items():
                            with open( stationPath + '/' + k + '.json', 'w' ) as f:
                                json.dump( v, f, ensure_ascii = False )

        else:
            if not userData:
                error = 'This callsign is not registerd yet.'
            elif userData['password'] != data['password']:
                error = 'Wrong password.'            
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
    yield from db.paramUpdate( 'users', { 'callsign': callsign }, \
        { 'settings': json.dumps( data['settings'] ) } )
    with open( getStationPath( callsign ) + '/settings.json', 'w' ) as f:
        json.dump( data['settings'], f, ensure_ascii = False )
    return web.Response( text = 'OK' )

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


@asyncio.coroutine
def newsHandler(request):
    data = yield from request.json()
    callsign = decodeToken( data )
    if not isinstance( callsign, str ):
        return callsign
    newsPath = getStationPath( callsign ) + '/news.json'
    news = loadJSON( newsPath )
    if not news:
        news = []
    if 'add' in data:
        news.insert( 0, { 'ts': time.time(), 'text': data['add'],\
            'time': datetime.now().strftime( '%d %b %H:%M' ).lower() } )
    if 'clear' in data:
        news = []
    with open( newsPath, 'w' ) as f:
        json.dump( news, f, ensure_ascii = False )
    return web.Response( text = 'OK' )

@asyncio.coroutine
def trackHandler(request):
    data = yield from request.json()
    callsign = decodeToken( data )
    if not isinstance( callsign, str ):
        return callsign
    stationPath = getStationPath( callsign )
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
def chatHandler(request):
    data = yield from request.json()
    stationData = getUserData( data['station'] )
    chatAdmins = stationData['chatAdmins'] + ( data['station'] )
    admin = False
    if data['from'] in chatAdmins or 'clear' in data or 'delete' in data:
        callsign = decodeToken( data )
        if not isinstance( callsign, str ):
            return callsign
        if not callsign in chatAdmins:
            return web.HTTPUnauthorized( \
                text = 'You must be logged in as chat admin to post with this callsign' )
        admin = True    
    chatPath = getStationPath( data['station'] ) + '/chat.json'
    chat = []
    if not 'clear' in data:
        chat = loadJSON( chatPath )
        if not chat:
            chat = []
        if 'delete' in data:
            chat = [ x for x in chat if x['ts'] != data['ts'] ]
        else:
            chat.insert( 0, { 'user': data['from'], 'text': data['text'], \
                    'admin': admin, 'ts': time.time() } )
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
    db.verbose = True
    asyncio.async( db.connect() )

    args = parser.parse_args()
    web.run_app(app, path=args.path, port=args.port)
