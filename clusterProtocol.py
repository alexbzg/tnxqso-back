#!/usr/bin/python3
#coding=utf-8

import logging, asyncio


@asyncio.coroutine
def connect( loop, host = None, port = 23, call = '', retries = 3 ):
    protocol = None

    def createProtocol():
        protocol = ClusterProtocol( loop, call = call )
        return protocol

    count = 0
    while count < retries:
        try:
            t, p = yield from loop.create_connection( createProtocol, host, port )
            return p
        except OSError as e:
            logging.error( 'Cluster connect error ' + str( e ) )
            count += 1
        else:
            break
    logging.error( 'Connection retries limit reached' )
    return None


class ClusterProtocol( asyncio.Protocol ):
    
    def __init__( self, loop, call = '' ):
        self.loop = loop
        self.rcvbuf = ''
        self.call = call
        self.onLoggedIn = []
        self.latestReply = ''
        self.loggedIn = False
        self.loginTimeoutHandle = None
        self.__disconnected = asyncio.Event( loop = loop )

    def connection_made( self, transport ):
        self.transport = transport
        self.rcvbuf = ''
        logging.debug( 'cluster connect' )
        self.loginTimeoutHandle = self.loop.call_later(2, self.login_timeout)

    def login_timeout( self ):
        if not self.loggedIn:
            logging.debug('cluster login timeout')
            self.close()    

    def connection_lost( self, reason ):
        logging.debug( 'cluster disconnect ' + str( reason ) )
        self.__disconnected.set()

    def write( self, data ):
        logging.debug( 'To cluster: ' + data )
        self.latestReply = ''
        self.transport.write( ( data + '\n' ).encode() )

    def data_received( self, data ):
        strData = data.decode()
        trimData = strData.strip( '\r\n ' )
        if trimData:
            self.latestReply += ' ' + trimData
        logging.debug( strData )
        if 'your call:' in strData:
            self.write( self.call )
        elif 'Please enter' in strData:
            self.write( '-' )
        elif '(Y or N)' in strData:
            self.write( 'Y' )
        elif '>' in strData:
            if not self.loggedIn:
                self.loggedIn = True
                self.loginTimeoutHandle.cancel()
                self.loginTimeoutHandle = None
                logging.debug( 'cluster login' )
                for cb in self.onLoggedIn:
                    cb()

    @asyncio.coroutine
    def waitDisconnected( self ):
        yield from self.__disconnected.wait()
                
    def close( self ):
        if self.loginTimeoutHandle:
            self.loginTimeoutHandle.cancel()
        self.transport.close()


