#!/usr/bin/python3
#coding=utf-8

import argparse, asyncio, logging, logging.handlers, aiohttp, jwt, os, base64, \
        json, time, math, smtplib, shutil, io, zipfile
from datetime import datetime
from aiohttp import web
from common import siteConf, loadJSON, appRoot, startLogging
from tqdb import DBConn, spliceParams

logging.basicConfig( level = logging.DEBUG )

parser = argparse.ArgumentParser(description="tnxqso backend aiohttp server")
parser.add_argument('--test', action = "store_true" )
args = parser.parse_args()

conf = siteConf()
webRoot = conf.get( 'web', 'root_test' if args.test else 'root' )

db = DBConn( conf.items( 'db_test' if args.test else 'db' ) )

@asyncio.coroutine
def upload():
    yield from db.connect()
    users_data = yield from db.execute( "select * from users" )
    for row in users_data:
        if not row['settings']:
            continue
        station_callsign = row['settings']['station']['callsign']
        if station_callsign:
            station_path = webRoot + '/stations/' +\
                station_callsign.lower().replace( '/', '-' )
            if os.path.isdir(station_path):
                log = []
                log_data = yield from db.execute("""
                    select id, qso 
                    from log where callsign = %(callsign)s order by id desc""",\
                    row)
                if log_data:
                    if isinstance(log_data, dict):
                        log.append(log_data)
                    else:
                        log = [row['qso'] for row in log_data]
                    with open(station_path + '/log.json', 'w' ) as f:
                        json.dump(log, f)
                with open(station_path + '/settings.json', 'w' ) as f:
                    json.dump(row['settings'], f)
                status_path = station_path + '/status.json'
                status = loadJSON(status_path)
                if status:
                    status['qth'] = {\
                        'fields': {\
                            'titles': ["RDA", "RAFA", "QTH field"],\
                            'values': [\
                                status['rda'] if 'rda' in status else None,\
                                status['rafa'] if 'rafa' in status else None,\
                                status['userFields'][0] if 'userFields' in status\
                                    else None\
                                    ]\
                                    },\
                        'loc': status['loc'] if 'loc' in status else None}
                    with open(status_path, 'w' ) as f:
                        json.dump(status, f)

loop = asyncio.get_event_loop()
loop.run_until_complete( upload() )
loop.close()
