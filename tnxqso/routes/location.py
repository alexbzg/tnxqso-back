#!/usr/bin/python3
#coding=utf-8

import math
import time
import json
from datetime import datetime, timedelta
import logging

from aiohttp import web
import httpx

from tnxqso.common import WEB_ROOT, loadJSON, appRoot, dtFmt
from tnxqso.services.auth import auth
from tnxqso.services.station_dir import get_station_path_by_admin_cs
from tnxqso.services.countries import get_country
from tnxqso.services.chat import insert_chat_message

LOCATION_ROUTES = web.RouteTableDef()

RAFA_LOCS = {}
with open(appRoot + '/rafa.csv', 'r') as f_rafa:
    for line in f_rafa.readlines():
        rafa_data = line.strip('\r\n ').split(';')
        locators = rafa_data[3].split(',')
        for loc in locators:
            if loc in RAFA_LOCS:
                RAFA_LOCS[loc] += ' ' + rafa_data[1]
            else:
                RAFA_LOCS[loc] = rafa_data[1]

LAST_SPOT_SENT = None

WFS_PARAMS = {\
        "rda": {"feature": "RDA_2020", "tag": "RDA"},\
        "waip": {"feature": "WAIP2", "tag": "WAIPIT"},\
        "wab": {"feature": "WAB", "tag": "NAME"},
        "kda": {"feature": "KDA_layer", "tag": "KDA"}
}

QTH_PARAMS = loadJSON(WEB_ROOT + '/js/qthParams.json')
def empty_qth_fields(country=None):
    tmplt = {'titles': [QTH_PARAMS['defaultTitle']]*QTH_PARAMS['fieldCount'],\
            'values': [None]*QTH_PARAMS['fieldCount']}
    if country and country in QTH_PARAMS['countries']:
        for idx in range(0, len(QTH_PARAMS['countries'][country]['fields'])):
            tmplt['titles'][idx] = QTH_PARAMS['countries'][country]['fields'][idx]
    return tmplt

def sind(deg):
    return math.sin(math.radians(deg))

def cosd(deg):
    return math.cos(math.radians(deg))

async def wfs_query(wfs_type, location, strict=False):
    params = WFS_PARAMS[wfs_type]
    url = ('https://r1cf.ru:8088/geoserver/cite/wfs?SERVICE=WFS&REQUEST=GetFeature&TypeName=' +
        '{feature}&VERSION=1.1.0&CQL_FILTER={predi}%28the_geom,POINT%28{lat}%20{lng}%29' +
        '{addParams}%29')
    url_params = {
        'feature': params['feature'],\
        'predi': 'INTERSECTS' if strict else 'DWITHIN',\
        'lat': location[0],\
        'lng': location[1],\
        'addParams': '' if strict else ',0.0025,kilometers' # ~250 meters
       }
    try:
        data = ''
        async with httpx.AsyncClient() as client:
            rsp = await client.get(url.format_map(url_params), timeout=(0.2, 1))
            data = rsp.text
        tag = '<cite:' + params['tag'] + '>'
        result = []
        while tag in data:
            start = data.find(tag) + len(tag)
            end = data.find('<', start)
            result.append(data[start:end])
            data = data[end:]
        if result:
            return result[0] if strict else result
        return None

    except httpx.TimeoutException:
        logging.exception('wfs query timeout: ')
        return ['-----']

async def get_qth_data(location, country=None):

    if not country:
        country = get_country(location)
    if country not in QTH_PARAMS['countries']:
        country = 'RU'

    data = {'fields': empty_qth_fields(country)}
    data['loc'], data['loc8'] = locator(location)

    if country == 'RU':

        rda = '-----'
        all_rda = await wfs_query('rda', location)
        strict_rda = await wfs_query('rda', location, strict=True)
        if all_rda:
            if len(all_rda) > 1:
                all_rda = [strict_rda] + [x for x in all_rda if x != strict_rda or x == '-----']
                rda = ' '.join(all_rda)
            else:
                rda = all_rda[0]
        data['fields']['values'][0] = rda

        data['fields']['values'][1] = RAFA_LOCS[data['loc']]\
            if data['loc'] in RAFA_LOCS else None

    elif country == 'KZ':

        kda = '-----'
        all_kda = await wfs_query('kda', location)
        strict_kda = await wfs_query('kda', location, strict=True)
        if all_kda:
            if len(all_kda) > 1:
                all_kda = [strict_kda] + [x for x in all_kda if x != strict_kda or x == '-----']
                kda = ' '.join(all_kda)
            else:
                kda = all_kda[0]
        data['fields']['values'][0] = kda

    elif country == 'IT':
        data['fields']['values'][0] = await wfs_query('waip', location, strict=True)

    elif country == 'GB':
        data['fields']['values'][0] = await wfs_query('wab', location, strict=True)

    return data

def save_qth_now_location(callsign, location, path):
    qth_now_locations = loadJSON(path) or []
    _ts = int(time.time())
    dt_utc = datetime.utcnow()
    _dt, _tm = dtFmt(dt_utc)
    qth_now_locations = [item for item in qth_now_locations
            if _ts - item['ts'] < 600 and (item['location'][0] != location[0] or
            item['location'][1] != location[1]) and
            (callsign is None or item['callsign'] != callsign)]
    qth_now_locations.append({
        'location': location,
        'ts': _ts,
        'date': _dt,
        'time': _tm,
        'callsign': callsign
    })
    with open(path, 'w') as f_loc:
        json.dump(qth_now_locations, f_loc, ensure_ascii = False)

@LOCATION_ROUTES.post('/aiohttp/location')
@auth(require_token=False)
async def location_handler(data, *, callsign, request):
    new_data = data
    station_path = None
    station_settings = None
    station_callsign = None
    if callsign:
        station_path = await get_station_path_by_admin_cs(callsign)
        station_settings = loadJSON(station_path + '/settings.json')
        if not station_settings:
            raise web.HTTPBadRequest(text='Expedition profile is not initialized.')
        if (station_settings.get('station') and
                station_settings['station'].get('callsign') and
                station_settings['station'].get('activityPeriod')):
            act_period = [datetime.strptime(dt, '%d.%m.%Y') for dt in
                station_settings['station']['activityPeriod'] if dt]
            if (act_period and
                    act_period[0] <= datetime.utcnow() <= act_period[1] + timedelta(days=1)):
                station_callsign = station_settings['station']['callsign']

    if new_data.get('location'):
        qth_now_cs = None
        if 'callsign' in new_data and new_data['callsign']:
            qth_now_cs = new_data['callsign']
        elif station_callsign:
            qth_now_cs = station_callsign

        if qth_now_cs:
            qth_now_cs = qth_now_cs.upper()
            save_qth_now_location(qth_now_cs, new_data['location'],
                    WEB_ROOT + '/js/qth_now_locations.json')

        save_qth_now_location(qth_now_cs, new_data['location'],
                WEB_ROOT + '/js/qth_now_locations_all.json')

    if not callsign and 'location' in new_data:
        qth = await get_qth_data(new_data['location'])
        return web.json_response({'qth': qth})
    f_path = station_path + '/status.json'
    data = loadJSON(f_path) or {}
    if 'locTs' not in data and 'ts' in data:
        data['locTs'] = data['ts']
    dt_utc = datetime.utcnow()
    data['ts'] = int(time.time())
    data['date'], data['time'] = dtFmt(dt_utc)
    data['year'] = dt_utc.year
    if 'online' in new_data:
        data['online'] = new_data['online']
    if 'freq' in new_data and new_data['freq']:
        data['freq'] = {'value': new_data['freq'], 'ts': data['ts']}
        from_callsign = station_settings['station']['callsign']
        await insert_chat_message(
            {'from': from_callsign,
            'text': '<b><i>' + new_data['freq'] + '</b></i>'},
            callsign,
            request,
            force_admin=True)
    country = station_settings['qthCountry'] if 'qthCountry' in station_settings else None
    if new_data.get('location'):
        location = new_data['location']

        country = get_country(location)

        data['qth'] = await get_qth_data(location, country=country)

        if 'comments' in new_data:
            data['comments'] = new_data['comments']
        if 'location' in data and data['location']:
            data['prev'] = {'location': data['location'][:], \
                    'ts': data['locTs']}
        data['locTs'], data['locDate'], data['locTime'] = data['ts'], data['date'], data['time']
        data['location'] = new_data['location']
        if 'prev' in data:
            lat = [data['location'][1], data['prev']['location'][1]]
            lon = [data['location'][0], data['prev']['location'][0]]
            dlon = lon[0] - lon[1]
            dlat = lat[0] - lat[1]
            _ap = (sind(dlat/2))**2 + cosd(lat[0]) * cosd(lat[1]) * (sind(dlon/2)) ** 2
            _cp = 2 * math.atan2(math.sqrt(_ap), math.sqrt(1 - _ap))
            distance = _cp * 6373
            data['d'] = distance
            data['dt'] = data['locTs'] - data['prev']['ts']
            if float(data['locTs'] - data['prev']['ts']) != 0:
                data['speed'] = distance / (float(data['locTs'] - data['prev']['ts']) \
                        / 3600)
            else:
                data['speed'] = 0

    if 'qth' in new_data:

        if 'qth' not in data:
            data['qth'] = {'fields': empty_qth_fields(country=country)}
        for key in new_data['qth']['fields'].keys():
            data['qth']['fields']['values'][int(key)] = new_data['qth']['fields'][key]
        if 'loc' in new_data['qth']:
            data['qth']['loc'] = new_data['qth']['loc']

    with open(f_path, 'w') as f_status:
        json.dump(data, f_status, ensure_ascii = False)
    return web.json_response(data)

def locator(location):
    lat = location[0]
    lng = location[1]
    qth = ""
    lat += 90
    lng += 180
    lat = lat / 10 + 0.0000001
    lng = lng / 20 + 0.0000001
    qth += chr(65 + int(lng))
    qth += chr(65 + int(lat))
    lat = 10 * (lat - math.trunc(lat))
    lng = 10 * (lng - math.trunc(lng))
    qth += chr(48 + int(lng))
    qth += chr(48 + int(lat))
    lat = 24 * (lat - math.trunc(lat))
    lng = 24 * (lng - math.trunc(lng))
    qth += chr(65 + int(lng))
    qth += chr(65 + int(lat))
    lat = 10 * (lat - math.trunc(lat))
    lng = 10 * (lng - math.trunc(lng))
    sfx = chr(48 + int(lng))
    sfx += chr(48 + int(lat))
    return (qth, sfx)
