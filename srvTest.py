#!/usr/bin/python3
#coding=utf-8

import asyncio, logging, unittest, requests, json

from common import siteConf, loadJSON


logging.basicConfig( level = logging.DEBUG,
        format='%(asctime)s %(message)s', 
        datefmt='%Y-%m-%d %H:%M:%S' )
logging.info( 'starting in test mode' )

srvURL = 'http://test.tnxqso.com/aiohttp'
token = 'eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJjYWxsc2lnbiI6IlFRUVEifQ.iUylnjRQK6vqkeL_JlzqaSs11YvK_8qyjtn88mtvGEc'

class TestSrv(unittest.TestCase):

    def testLogQSO( self ):
        r = requests.post( srvURL + '/log', \
            data = json.dumps( { 'qso': 'blahblahblah', 'token': token } ) )
        self.assertEqual( r.status_code, requests.codes.ok )
        print( r.text )

if __name__ == '__main__':
    unittest.main()

