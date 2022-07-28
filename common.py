#!/usr/bin/python3
#coding=utf-8


import configparser, decimal, json, logging, logging.handlers, os
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

def createFtpUser( user, passwd, test = False ):
    conf = siteConf()
    testPfx = '_test' if test else ''
    setFtpPasswd( user, passwd, test )
    ftpPath = conf.get( 'web', 'root' + testPfx ) + '/ftp/' + user
    os.makedirs( ftpPath )
    os.chmod( ftpPath, 0o775 )

def setFtpPasswd( user, passwd, test = False ):
    conf = siteConf()
    testPfx = '_test' if test else ''
    ht = HtpasswdFile( conf.get( 'ftp', 'passwd' + testPfx ), 
            default_scheme = "md5_crypt" )
    ht.set_password( user, passwd )
    ht.save()

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

