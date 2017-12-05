#!/usr/bin/python3
#coding=utf-8


import configparser, decimal, json, logging, logging.handlers
from os import path
from datetime import datetime, date

appRoot = path.dirname( path.abspath( __file__ ) ) 

def siteConf():
    conf = configparser.ConfigParser()
    conf.optionxform = str
    conf.read( appRoot + '/site.conf' )
    return conf

def readConf( file ):
    conf = configparser.ConfigParser()
    conf.read( appRoot + '/' + file )
    return conf


def jsonEncodeExtra( obj ):
    if isinstance( obj, decimal.Decimal ):
        return float( obj )
    elif isinstance(obj, datetime):
        return obj.isoformat()
    elif isinstance(obj, date):
        return obj.isoformat()
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

