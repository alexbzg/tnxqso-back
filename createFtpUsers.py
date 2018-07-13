#!/usr/bin/python3
#coding=utf-8

import argparse, asyncio, logging, logging.handlers, aiohttp, jwt, os, base64, \
        json, time, math, smtplib, shutil, io, zipfile
from datetime import datetime
from aiohttp import web
from common import siteConf, loadJSON, appRoot, startLogging, createFtpUser
from tqdb import DBConn, spliceParams

logging.basicConfig( level = logging.DEBUG )

parser = argparse.ArgumentParser(description="tnxqso backend aiohttp server")
parser.add_argument('--test', action = "store_true" )
args = parser.parse_args()

conf = siteConf()
db = DBConn( conf.items( 'db_test' if args.test else 'db' ) )

@asyncio.coroutine
def main():
    yield from db.connect()
    users = yield from db.execute( "select callsign, password from users" )
    for user in users:
        createFtpUser( user['callsign'], user['password'], args.test )

loop = asyncio.get_event_loop()
loop.run_until_complete( main() )
loop.close()
