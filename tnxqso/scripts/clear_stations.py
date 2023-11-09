#!/usr/bin/python3
#coding=utf-8
import pathlib
import asyncio
import shutil
import os
import json

from tnxqso.common import WEB_ROOT, loadJSON, startLogging
from tnxqso.db import DB

async def _main():

    startLogging('gc_stations')
    await DB.connect()

    stations_path = WEB_ROOT + '/stations'

    for station_path in [str(x) for x in pathlib.Path(stations_path).iterdir() if x.is_dir()]:
        settings, db_settings = loadJSON(station_path + '/settings.json'), None
        if settings and settings.get('admin'):
            db_settings = await DB.execute("""
                select settings 
                from users 
                where callsign = %(admin)s""", settings)
            if (db_settings and
                    db_settings['settings']['station']['callsign'] == settings['station']['callsign']):
                continue
        shutil.rmtree(station_path)
    await DB.disconnect()

    publish_path = f'{WEB_ROOT}/js/publish.json'
    publish = loadJSON(publish_path)
    publish = {callsign: publish[callsign] for callsign in publish
            if os.path.exists(f"{WEB_ROOT}/stations/{callsign.lower().replace('/', '-')}")}

    with open(publish_path, 'w') as f_publish:
        json.dump(publish, f_publish, ensure_ascii = False)

def main():
    asyncio.run(_main())
