#!/usr/bin/python3
#coding=utf-8

import argparse, asyncio, logging, logging.handlers, aiohttp, jwt, os, base64, \
        json, time, math, smtplib, shutil, io, zipfile, pathlib
from datetime import datetime
from aiohttp import web
from common import siteConf, loadJSON, appRoot, startLogging, createFtpUser
from tqdb import DBConn, spliceParams

logging.basicConfig( level = logging.DEBUG )

parser = argparse.ArgumentParser(description="tnxqso backend aiohttp server")
args = parser.parse_args()

conf = siteConf()

root = conf.get( 'web', 'root' )
stationsPath =  root + '/stations'
db = DBConn(conf.items('db'))

async def main():
    await db.connect()

    for station in [str(x) for x in pathlib.Path(stationsPath).iterdir() if x.is_dir()]:
        stPath = station + '/settings.json'
        settings = loadJSON(stPath)
        if settings:
            settings['initialized'] = True
            await db.paramUpdate('users', {'callsign': settings['admin']}, \
                {'settings': json.dumps(settings)})
    await db.disconnect()

asyncio.run(main())
