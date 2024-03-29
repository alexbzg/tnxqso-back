#!/usr/bin/python3
#coding=utf-8


import configparser, decimal, logging, logging.handlers, os
from os import path
from datetime import datetime, date
from functools import partial

import simplejson as json
from aiohttp import web

appRoot = path.dirname( path.abspath( __file__ ) ) 

def siteConf():
    conf = configparser.ConfigParser()
    conf.optionxform = str
    conf.read( appRoot + '/site.conf' )
    return conf

CONF = siteConf()
WEB_ROOT = CONF.get('web', 'root')
WEB_ADDRESS = CONF.get('web', 'address')


def jsonEncodeExtra( obj ):
    if isinstance( obj, decimal.Decimal ):
        return float( obj )
    elif isinstance(obj, (date, datetime)):
        return dtFmt(obj)
    raise TypeError( repr( obj ) + " is not JSON serializable" )

def loadJSON( pathJS ):
    if not path.isfile( pathJS ):
        logging.exception( pathJS + " not found" )
        return False
    try:
        r = json.load( open( pathJS ) )
        return r
    except Exception as ex:
        logging.error( "Error loading " + pathJS )
        logging.exception( ex )
        return False

DEF_USER_SETTINGS = loadJSON(WEB_ROOT + '/js/defaultUserSettings.json') or {}

json_dumps = partial(json.dumps, default=jsonEncodeExtra)
web_json_response = partial(web.json_response, dumps=json_dumps)

def startLogging( type, level = logging.DEBUG ):
    conf = siteConf()
    fpLog = conf.get( 'logs', type ) 
    logger = logging.getLogger('')
    logger.setLevel( level )
    loggerHandler = logging.handlers.WatchedFileHandler( fpLog )
    loggerHandler.setLevel( level )
    loggerHandler.setFormatter( logging.Formatter( \
        '%(asctime)s %(name)-12s %(levelname)-8s %(message)s' ) )
    logger.addHandler( loggerHandler )

def dtFmt( dt ):
    return dt.strftime( '%d %b' ).lower(), dt.strftime( '%H:%Mz' )

def tzOffset():
    return datetime.now().timestamp() - datetime.utcnow().timestamp()

def qth( _lat, _lon ):
    r = ''
    lat = float( _lat )
    lng = float( _lon )

    lat += 90
    lng += 180
    lat = lat / 10 + 0.0000001
    lng = lng / 20 + 0.0000001
    r += chr(int(65 + lng))
    r += chr(int(65 + lat))
    lat = 10 * (lat - int(lat))
    lng = 10 * (lng - int(lng))
    r += chr(int(48 + lng))
    r += chr(int(48 + lat))
    lat = 24 * (lat - int(lat))
    lng = 24 * (lng - int(lng))
    r += chr(int(65 + lng))
    r += chr(int(65 + lat))

    return r

