#!/usr/bin/python3
#coding=utf-8

import asyncio, logging, unittest, requests, json, jwt, os, time

from common import siteConf, loadJSON
conf = siteConf()
secret = None
fpSecret = conf.get( 'files', 'secret' )
if ( os.path.isfile( fpSecret ) ):
    with open( fpSecret, 'rb' ) as fSecret:
        secret = fSecret.read()


logging.basicConfig( level = logging.DEBUG,
        format='%(asctime)s %(message)s', 
        datefmt='%Y-%m-%d %H:%M:%S' )
logging.info( 'starting in test mode' )

srvURL = 'http://test.tnxqso.com/aiohttp'
token = 'eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJjYWxsc2lnbiI6IlFRUVEifQ.iUylnjRQK6vqkeL_JlzqaSs11YvK_8qyjtn88mtvGEc'

class TestSrv(unittest.TestCase):

#    def testLogQSO( self ):
#        r = requests.post( srvURL + '/log', \
#            data = json.dumps( { 'qso': 'blahblahblah', 'token': token } ) )
#        self.assertEqual( r.status_code, requests.codes.ok )
#        print( r.text )
#        r.connection.close()

    def testChangePasswordEmail( self ):
        r = requests.post( srvURL + '/userSettings', \
            data = json.dumps( { 'token': token, 'email': '18@73.ru', 'password': '222222' } ) )
        self.assertEqual( r.status_code, requests.codes.ok )
        print( r.text )
        r.connection.close()

    def testChangePasswordWToken( self ):
        if not secret:
            print( 'Test skipped - no secret' )
        timeToken = jwt.encode( 
            { 'callsign': 'qqqq', 'time': time.time() }, \
            secret, algorithm='HS256' ).decode('utf-8')
        r = requests.post( srvURL + '/userSettings', \
            data = json.dumps( { 'token': timeToken, 'password': '111111' } ) )
        self.assertEqual( r.status_code, requests.codes.ok )
        print( r.text )
        r.connection.close()


if __name__ == '__main__':
    unittest.main()

