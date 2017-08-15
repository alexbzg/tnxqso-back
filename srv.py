#!/usr/bin/python3
#coding=utf-8

import argparse, asyncio, logging, logging.handlers, aiohttp, psycopg2
from aiohttp import web
from common import siteConf
from tqdb import db, spliceParams

parser = argparse.ArgumentParser(description="tnxqso backend aiohttp server")
parser.add_argument('--path')
parser.add_argument('--port')

logger = logging.getLogger('')
logger.setLevel( logging.DEBUG )
loggerHandler = logging.handlers.WatchedFileHandler('/var/log/tnxqso.aiohttp.log')
loggerHandler.setLevel( logging.DEBUG )
loggerHandler.setFormatter( logging.Formatter( \
    '%(asctime)s %(name)-12s %(levelname)-8s %(message)s' ) )
logger.addHandler( loggerHandler )
logger.debug( "restart" )

conf = siteConf()

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

def getUserData( callsign, password = None ):
    return { 'callsign': callsign }

@asyncio.coroutine
def loginHandler(request):
    error = None
    data = yield from request.json()
    if not 'login' in data or len( data['login'] ) < 2:
        error = 'Minimal login length is 2 symbols'
    if not 'password' in data or len( data['password'] ) < 6:
        error = 'Minimal password length is 6 symbols'
    if not error:
        if data['newUser']:
            rcTest = yield from checkRecaptcha( data['recaptcha'] )
            if not rcTest:
                error = 'Recaptcha test failed. Please try again'
            else:
                logging.debug( 'call db.getObject' )
                yield from db.getObject( 'users', \
                    { 'callsign': data['login'], \
                    'password': data['password'] }, True )
        else:
            pass
    if error:
        return web.HTTPBadRequest( text = error )
    else:
        return web.json_response( getUserData( data['login'] ) )


if __name__ == '__main__':
    app = web.Application()
    app.router.add_post('/aiohttp/login', loginHandler)
    db.verbose = True
    asyncio.async( db.connect() )

    args = parser.parse_args()
    web.run_app(app, path=args.path, port=args.port)
