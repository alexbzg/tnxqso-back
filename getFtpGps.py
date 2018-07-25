#!/usr/bin/python3
#coding=utf-8

import argparse, asyncio, logging, logging.handlers, aiohttp, jwt, os, base64, \
        json, time, math, smtplib, shutil, io, zipfile
from datetime import datetime, timezone
from common import siteConf, loadJSON, startLogging, createFtpUser, dtFmt, appRoot, qth
from tqdb import DBConn, spliceParams

logging.basicConfig( level = logging.DEBUG )
startLogging( 'getFtpGps' )

parser = argparse.ArgumentParser(description="tnxqso backend -- ftp gps data collector" )
parser.add_argument('--test', action = "store_true" )
args = parser.parse_args()

conf = siteConf()
db = DBConn( conf.items( 'db_test' if args.test else 'db' ) )
webRoot = conf.get( 'web', 'root_test' if args.test else 'root' )

fname = datetime.utcnow().strftime( '%Y%m%d' ) + '.csv'

rafa = {}
with open( appRoot + '/rafa.csv', 'r' ) as fRAFA:
    for line in fRAFA.readlines():
        line = line.strip( '\n' )
        data = line.split(';')
        if data[0] == '':
            rafa[data[1]] = data[3].split(',')

def getStationPath( callsign ):
    return webRoot + '/stations/' + callsign.lower().replace( '/', '-' )

@asyncio.coroutine
def main():
    yield from db.connect()
    users = yield from db.execute( "select callsign, settings from users" )
    fname = datetime.now().strftime( "%Y%m%d" ) + ".csv"
    for user in users:
        if user['settings'] and 'station' in user['settings'] and \
            'callsign' in user['settings']['station'] and \
            user['settings']['station']['callsign'] and \
            user['settings']['status']['get'] == 'gpslogger':            
            stationCs = user['settings']['station']['callsign']
            stationPath = getStationPath( stationCs )
            statusPath = stationPath + '/status.json'
            status = loadJSON( statusPath  )
            if not status:
                status = {}
            ftpPath = webRoot + '/ftp/' + user['callsign'] + '/GPSLogger'
            if os.path.isdir( ftpPath ):
                ftpFilePath = ( ftpPath + '/' + fname )
                if os.path.isfile( ftpFilePath ):
                    data = None
                    with open( ftpPath + '/' + fn, 'r' ) as f:
                        data = f.readlines()[-1].split( ',' )
                    dt = datetime.strptime( data[0], '%Y-%m-%dT%H:%M:%S.%fZ' )
                    dt.replace( tzinfo = timezone.utc )
                    ts = dt.timestamp() 
                    if not 'ts' in status or status['ts'] < ts:
                        status['date'], status['time'] = dtFmt( dt )    
                        status['year'] = dt.year
                        status['ts'] = ts
                        status['location'] = [ data[1], data[2] ]
                        status['loc'] = qth( data[1], data[2] )
                        if status['loc'] in rafa:
                            status['rafa'] = rafa[status['loc']]
                        status['speed'] = float( data[6] )
                        dt_ = datetime.utcnow()
                        #dt_.replace( tzinfo = timezone.utc )
                        #status['ts_'] = dt_.timestamp() 
                        #status['date_'], status['time_'] = dtFmt( dt_ )    
                        #status['year_'] = dt_.year
                        with open( statusPath, 'w' ) as f:
                            json.dump( status, f, ensure_ascii = False )

loop = asyncio.get_event_loop()
loop.run_until_complete( main() )
loop.close()
