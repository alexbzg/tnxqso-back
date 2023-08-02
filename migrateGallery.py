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
db = DBConn( conf.items('db') )


async def main():
    await  db.connect()

    for station in [str(x) for x in pathlib.Path(stationsPath).iterdir() if x.is_dir()]:
        settings = loadJSON(f"{station}/settings.json")
        gallery = loadJSON(f"{station}/gallery.json")
        if settings and gallery and settings.get('admin'):
            for item in gallery[::-1]:
                if not item.get('datetime'):
                    continue
                await db.execute("""
                    insert into blog_entries
                        ("user", "file", file_type, file_thumb, txt, timestamp_created)
                    values
                        (%(callsign)s, %(file)s, %(fileType)s, %(fileThumb)s, %(text)s, 
                        %(datetime)s)
                    """,
                    {'callsign': settings['admin'], 'file': item.get('file'), 'fileType': item.get('type'), 
                        'fileThumb': item.get('thumb'), 'text': item.get('caption'), 'datetime': item['datetime']})

asyncio.run( main() )
