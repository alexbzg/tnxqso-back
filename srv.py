#!/usr/bin/python3
#coding=utf-8

import argparse, asyncio, logging, logging.handlers
from aiohttp import web

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

@asyncio.coroutine
def loginHandler(request):
    data = yield from request.json()
    return web.json_response(data)


if __name__ == '__main__':
    app = web.Application()
    app.router.add_post('/aiohttp/login', loginHandler)

    args = parser.parse_args()
    web.run_app(app, path=args.path, port=args.port)
