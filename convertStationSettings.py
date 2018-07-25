#!/usr/bin/python3
#coding=utf-8

import argparse, asyncio, logging, logging.handlers, aiohttp, jwt, os, base64, \
        json, time, math, smtplib, shutil, io, zipfile
from datetime import datetime
from aiohttp import web
from common import siteConf, loadJSON, appRoot, startLogging, createFtpUser
from tqdb import DBConn, spliceParams

logging.basicConfig( level = logging.DEBUG )

parser = argparse.ArgumentParser(description="tnxqso backend aiohttp server")
parser.add_argument('--test', action = "store_true" )
args = parser.parse_args()

conf = siteConf()
db = DBConn( conf.items( 'db_test' if args.test else 'db' ) )

webRoot = conf.get( 'web', 'root_test' if args.test else 'root' )
defSettings = loadJSON( webRoot + '/js/defaultUserSettings.json' )
defStatus = defSettings['status']

@asyncio.coroutine
def main():
    yield from db.connect()
    users = yield from db.execute( "select callsign, settings from users" )
    for user in users:
        settings = user['settings']
        settings['status'] = defStatus
        if not 'userFields' in settings:
            settings['userFields'] = []
            columns = settings['log']['userColumns']
            settings['log']['userColumns'] = []
            for col in columns:
                settings['userFields'].append( col['column'] )
                settings['log']['userColumns'].append( col['enabled'] )
        yield from db.paramUpdate( 'users', { 'callsign': user['callsign'] }, \
            { 'settings': json.dumps( settings ) } )
        if settings['station']['callsign']:
            stationPath = webRoot + '/stations/' + \
                settings['station']['callsign'].lower().replace( '/', '-' )
            if stationPath:
                settings['admin'] = user['callsign']
                with open( stationPath + '/settings.json', 'w' ) as f:
                    json.dump( settings, f, ensure_ascii = False )

loop = asyncio.get_event_loop()
loop.run_until_complete( main() )
loop.close()
