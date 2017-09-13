#!/usr/bin/python3
#coding=utf-8

import argparse, asyncio, logging, logging.handlers, aiohttp, jwt, os, base64, json
from aiohttp import web
from common import siteConf, loadJSON, appRoot
from tqdb import db, spliceParams

parser = argparse.ArgumentParser(description="tnxqso backend aiohttp server")
parser.add_argument('--path')
parser.add_argument('--port')

conf = siteConf()

fpLog = conf.get( 'files', 'log' ) 
logger = logging.getLogger('')
logger.setLevel( logging.DEBUG )
loggerHandler = logging.handlers.WatchedFileHandler( fpLog )
loggerHandler.setLevel( logging.DEBUG )
loggerHandler.setFormatter( logging.Formatter( \
    '%(asctime)s %(name)-12s %(levelname)-8s %(message)s' ) )
logger.addHandler( loggerHandler )
logger.debug( "restart" )

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


if __name__ == '__main__':
    app = web.Application()
    app.router.add_post('/aiohttp/login', loginHandler)
    db.verbose = True
    asyncio.async( db.connect() )

    args = parser.parse_args()
    web.run_app(app, path=args.path, port=args.port)
