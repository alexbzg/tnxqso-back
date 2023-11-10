#!/usr/bin/python3
#coding=utf-8
import pathlib
import asyncio
import shutil
import os
import json

from tnxqso.common import CONF, WEB_ROOT, loadJSON, startLogging
from tnxqso.db import DB
from tnxqso.services.station_dir import get_gallery_size, delete_blog_entry

async def _main():

    startLogging('gc_stations')
    await DB.connect()

    stations_path = WEB_ROOT + '/stations'

    for station_path in [str(x) for x in pathlib.Path(stations_path).iterdir() if x.is_dir()]:
        settings, db_settings = loadJSON(station_path + '/settings.json'), None
        if settings and settings.get('admin'):
            db_settings = await DB.execute("""
                select settings, gallery_quotas
                from users 
                where callsign = %(admin)s""", settings)

            #delete station directory with no db entries
            if (not db_settings or
                    db_settings['settings']['station']['callsign'] !=
                        settings['station']['callsign']):
                shutil.rmtree(station_path)
                continue

            gallery_path = f"{station_path}/gallery"
            if os.path.isdir(gallery_path):
                #delete station media files with no db entries
                db_media = await DB.execute("""
                    select id, "file", file_thumb
                    from blog_entries 
                    where "user" = %(admin)s and "file" is not null
                    order by id""", settings)
                db_files = set()
                if db_media:
                    for entry in db_media:
                        db_files.add(entry['file'])
                        db_files.add(entry['file_thumb'])
                for file_path in [x 
                        for x in pathlib.Path(gallery_path).iterdir()
                        if x.is_file()]:
                    if f"gallery/{file_path.name}" not in db_files:
                        os.unlink(file_path)


                #check gallery size and delete older media if out of quota
                if db_media:
                    gallery_size = get_gallery_size(station_path)
                    user_quota = int(CONF['gallery']['quota']*db_settings['gallery_quotas'])
                    if gallery_size > user_quota:
                        for entry in db_media:
                            file_size = os.path.getsize(f"{station_path}/{entry['file']}")
                            await delete_blog_entry(entry, station_path)
                            gallery_size -= file_size
                            if gallery_size <= user_quota:
                                break

    await DB.disconnect()

    publish_path = f'{WEB_ROOT}/js/publish.json'
    publish = loadJSON(publish_path)
    publish = {callsign: publish[callsign] for callsign in publish
            if os.path.exists(f"{WEB_ROOT}/stations/{callsign.lower().replace('/', '-')}")}

    with open(publish_path, 'w') as f_publish:
        json.dump(publish, f_publish, ensure_ascii = False)

def main():
    asyncio.run(_main())
