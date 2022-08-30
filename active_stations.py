#!/usr/bin/python3
#coding=utf-8
import logging
import logging.handlers
from datetime import datetime
import json
import time

from common import siteConf, loadJSON, startLogging

startLogging('active_stations', logging.DEBUG)

conf = siteConf()
webRoot = conf.get('web', 'root')
publish = loadJSON(f'{webRoot}/js/publish.json')

data = []
today = datetime.utcnow().date()
now = time.time()

ONLINE_INT = 120
FREQ_INT = 300

for callsign, publishSettings in publish.items():
    if all(publishSettings.values()):
        stationPath = f"{webRoot}/stations/{callsign.lower().replace('/', '-')}"
        stationSettings = loadJSON(f'{stationPath}/settings.json')
        if (stationSettings and stationSettings.get('station') and
            stationSettings['station'].get('callsign') and
            stationSettings['station'].get('activityPeriod')):
            activityPeriod = [datetime.strptime(item, '%d.%m.%Y').date()
                    for item in stationSettings['station']['activityPeriod'] if item is not None]
            if len(activityPeriod) == 2 and activityPeriod[0] <= today <= activityPeriod[1]:
                status = loadJSON(f'{stationPath}/status.json')
                if status.get('online') and stationSettings['status']['get'] != 'manual':
                    status['online'] = now - status['ts'] < ONLINE_INT
                else:
                    status['online'] = status.get('online')
                if status.get('freq') and status['freq'].get('value'):
                    status['freq'] = status['freq']['value'] if now - status['freq']['ts'] < FREQ_INT else None
                else: 
                    status['freq'] = None
                data.append({
                    'callsign': callsign,
                    'status': status
                    })

data.sort(key=lambda item: item['callsign'])
with open(f'{webRoot}/js/activeStations.json', 'w') as fStations:
    json.dump(data, fStations, ensure_ascii = False)
