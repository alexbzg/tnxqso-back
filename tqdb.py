#!/usr/bin/python3
#coding=utf-8

import aiopg, logging, traceback, json, asyncio, psycopg2

from common import siteConf

@asyncio.coroutine
def toDict( cur, keys = None ):
    if cur and cur.rowcount:
        colNames = [ col.name for col in cur.description ]
        if cur.rowcount == 1 and not keys:
            data = yield from cur.fetchone()
            return dict( zip( colNames, data ) )
        else:
            data = yield from cur.fetchall()
            if ( 'id' in colNames ) and keys:
                idIdx = colNames.index( 'id' )
                return { row[ idIdx ]: dict( zip( colNames, row ) ) \
                        for row in data }
            else:
                return [ dict( zip( colNames, row ) ) for
                        row in data ]
    else:
        return False

def paramStr( params, str ):
    return str.join( [ x + " = %(" + x + ")s" for x in params.keys() ] )

def spliceParams( data, params ):
    return { param: json.dumps( data[param] ) \
            if isinstance( data[param],dict ) else data[param] \
        for param in params \
        if param in data }

@asyncio.coroutine
def initConnection( cn ):
    cn.set_client_encoding( 'UTF8' )
    logging.debug( 'new db connection' )


class DBConn:

    def __init__( self, dbParams ):
        self.dsn = ' '.join( 
                [ k + "='" + v + "'" 
                    for k, v in dbParams ] )
        self.verbose = False


    @asyncio.coroutine
    def connect( self ):
        try:
            self.pool = yield from aiopg.create_pool( self.dsn, \
                    on_connect = initConnection  )
            logging.debug( 'db connections pool created' )
        except:
            logging.exception( 'Error creating connection pool' )
            logging.error( self.dsn )

    @asyncio.coroutine
    def fetch( self, sql, params = None ):
        res = False
        cur = yield from self.execute( sql, params )
        if cur.rowcount:
            res = yield from cur.fetchall()
        return res

    @asyncio.coroutine
    def paramUpdate( self, table, idParams, updParams ):
        return ( yield from self.execute( 'update ' + table + \
                ' set ' + paramStr( updParams, ', ' ) + \
                " where " + paramStr( idParams, ' and ' ), \
                dict( idParams, **updParams ) ) )

    @asyncio.coroutine
    def paramDelete( self, table, idParams ):
        return ( yield from self.execute( 'delete from ' + table + \
                " where " + paramStr( idParams, ' and ' ), \
                idParams ) )

    @asyncio.coroutine
    def paramUpdateInsert( self, table, idParams, updParams ):
        lookup = yield from self.getObject( table, idParams, False, True )
        r = None
        if lookup:
            r = yield from self.paramUpdate( table, idParams, updParams )
        else:
            r = yield from self.getObject( table, dict( idParams, **updParams ), \
                    True )
        return r

    @asyncio.coroutine
    def execute( self, sql, params = None ):
        res = False
        with (yield from self.pool.cursor()) as cur:
            try:
                if self.verbose:
                    logging.debug( sql )
                    logging.debug( params )
                yield from cur.execute( sql, params )                                
                res = ( yield from toDict( cur ) ) if cur.description != None else True
            except psycopg2.Error as e:
                logging.exception( "Error executing: " + sql + "\n" )
                stack = traceback.extract_stack()
                logging.error( stack )
                if params:
                    logging.error( "Params: " )
                    logging.error( params )
                if e.pgerror:
                    logging.error(  e.pgerror )
                    self.error = e.pgerror
        return res
        
        

    @asyncio.coroutine
    def getValue( self, sql, params = None ):
        res = yield from self.fetch( sql, params )
        if res:
            return res[0][0]
        else:
            return False

    @asyncio.coroutine
    def getObject( self, table, params, create = False, 
            never_create = False ):
        sql = ''
        res = False
        if not create:
            sql = "select * from %s where %s" % (
                    table, 
                    " and ".join( [ k + " = %(" + k + ")s"
                        if params[ k ] != None 
                        else k + " is null"
                        for k in params.keys() ] ) )
            res = yield from self.execute( sql, params )
        if create or ( not res and not never_create ):
            keys = params.keys()
            sql = "insert into " + table + " ( " + \
                ", ".join( keys ) + ") values ( " + \
                ', '.join( [ "%(" + k + ")s" for k in keys ] ) + \
                " ) returning *"
            logging.debug( 'creating object in db' )
            res = yield from self.execute( sql, params )
        return res 

    @asyncio.coroutine
    def updateObject( self, table, params, idParam = "id" ):
        paramString = ", ".join( [ k + " = %(" + k + ")s" 
            for k in params.keys() if k != idParam ] )
        if paramString != '':
            sql = "update " + table + " set " + paramString + \
                " where " + idParam + " = %(" + idParam + ")s returning *" 
            with ( yield from self.execute( sql, params ) ) as cur:
                if cur:
                    objRes = yield from toDdict( cur )
                    return objRes
    
    @asyncio.coroutine
    def deleteObject( self, table, id ):
        sql = "delete from " + table + " where id = %s" 
        yield from self.execute( sql, ( id, ) )



