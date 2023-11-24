#!/usr/bin/python3
#coding=utf-8
import asyncio

from tnxqso.db import DB
from tnxqso.services.station_dir import update_station_settings, write_station_file

async def _main():

    await DB.connect()

    users = await DB.execute("""
        select callsign, settings
        from users""", container="list")
    for user in users:
        if not user['settings']:
            continue
        if user['settings'].get('donate'):
            await write_station_file(user['callsign'], 'donateText.html', user['settings']['donate']['text'])
            await write_station_file(user['callsign'], 'donateCode.html', user['settings']['donate']['code'])
        await write_station_file(user['callsign'], 'stationInfo.html',
                user['settings']['station']['info'])
        del user['settings']['donate']
        del user['settings']['station']['info']
        await update_station_settings(user['callsign'], user['settings'])

if __name__ == '__main__':
    asyncio.run(_main())
