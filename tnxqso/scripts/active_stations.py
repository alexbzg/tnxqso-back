#!/usr/bin/python3
#coding=utf-8
import logging
import logging.handlers
from datetime import datetime
import json
import time
import asyncio

import aiormq
import aio_pika

from tnxqso.common import WEB_ROOT, loadJSON, startLogging
from tnxqso.services.station_dir import get_station_path
from tnxqso.services.rabbitmq import create_connection, rabbitmq_publish

ONLINE_INT = 120
FREQ_INT = 300

async def rabbitmq_post(data):
    try:
        connection = await create_connection()
        channel = await connection.channel()
        exchange = await channel.declare_exchange(
            name='active_stations',
            type=aio_pika.ExchangeType.FANOUT,
            durable=True
            )
        await rabbitmq_publish(exchange, '', data)

    except aiormq.exceptions.AMQPConnectionError:
        logging.exception("Rabbitmq connection error.")

def main():
    startLogging('active_stations', logging.DEBUG)

    publish = loadJSON(f'{WEB_ROOT}/js/publish.json')

    data = []
    today = datetime.utcnow().date()
    now = time.time()


    for callsign, publish_settings in publish.items():
        if all(publish_settings.values()):
            try:
                station_path = get_station_path(callsign)
                station_settings = loadJSON(f'{station_path}/settings.json')
                if (station_settings and station_settings.get('station') and
                    station_settings['station'].get('callsign') and
                    station_settings['station'].get('activityPeriod')):
                    activity_period = [datetime.strptime(item, '%d.%m.%Y').date()
                            for item in station_settings['station']['activityPeriod']
                                if item is not None]
                    if len(activity_period) == 2 and activity_period[0] <= today <= activity_period[1]:
                        status = loadJSON(f'{station_path}/status.json')
                        if station_settings['status']['get'] != 'manual':
                            status['online'] = status.get('ts') and (now - status['ts'] < ONLINE_INT)
                        else:
                            status['online'] = status.get('online', False)
                        if (status.get('freq') and status['freq'].get('value') and
                                now - status['freq']['ts'] > FREQ_INT):
                            status['freq'] = None
                        if status.get('speed') and now - status['locTs'] > ONLINE_INT:
                            status['speed'] = 0
                        else:
                            status['speed'] = status.get('speed')
                        data.append({
                            'callsign': callsign,
                            'status': status
                            })
            except Exception as exc:
                logging.exception("Error processing station %s", callsign)

    data.sort(key=lambda item: item['callsign'])
    with open(f'{WEB_ROOT}/js/activeStations.json', 'w') as f_stations:
        json.dump(data, f_stations, ensure_ascii = False)

    asyncio.run(rabbitmq_post(data))
