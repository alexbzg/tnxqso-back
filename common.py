#!/usr/bin/python3
#coding=utf-8


import configparser, decimal, json, logging, logging.handlers
from os import path
from datetime import datetime, date
from passlib.apache import HtpasswdFile

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

def setFtpDir( user, path ):
    conf = siteConf()
    with open( conf.get( 'ftp', 'user_conf_dir' ) + '/' + user, \
            'w' ) as uf:  
        uf.write( 'local_root=' + path ) 

def createFtpUser( user, passwd, test = False ):
    conf = siteConf()
    ht = HtpasswdFile( conf.get( 'ftp', 'passwd_file' ) )
    ht.set_password( user, passwd )
    ht.save()

