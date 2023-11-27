#!/usr/bin/python3
#coding=utf-8
import asyncio
import os

from tnxqso.db import DB
from tnxqso.services.station_dir import (update_station_settings,
        write_station_file, get_station_path_by_admin_cs)

async def _main():

    await DB.connect()

    users = await DB.execute("""
        select callsign, settings
        from users""", container="list")
    for user in users:
        if (not user['settings'] or
            not os.path.exists(await get_station_path_by_admin_cs(user['callsign']))):
            continue
        update = False
        if 'donate' in user['settings']:
            await write_station_file(user['callsign'],
                    'donateText.html', user['settings']['donate']['text'])
            await write_station_file(user['callsign'],
                    'donateCode.html', user['settings']['donate']['code'])
            del user['settings']['donate']
            update = True
        if 'info' in user['settings']['station']:
            await write_station_file(user['callsign'], 'stationInfo.html',
                user['settings']['station']['info'])
            del user['settings']['station']['info']
            update = True
        if update:
           await update_station_settings(user['callsign'], user['settings'])

    await DB.disconnect()

if __name__ == '__main__':
    asyncio.run(_main())
