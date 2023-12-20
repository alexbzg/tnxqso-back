#!/usr/bin/python3
#coding=utf-8
import time
import json
import logging
from datetime import datetime, timedelta
import os
from decimal import Decimal
from pathlib import Path

from aiohttp import web

from tnxqso.common import CONF, dtFmt, loadJSON, tzOffset
from tnxqso.db import DB
from tnxqso.services.auth import auth, extract_callsign
from tnxqso.services.station_dir import (get_station_path_by_admin_cs,
        read_station_file, write_station_file)

QSO_LOG_ROUTES = web.RouteTableDef()

BANDS_WL = {'1.8': '160M', '3.5': '80M', '7': '40M',
        '10': '30M', '14': '20M', '20': '14M', '18': '17M', '21': '15M',
        '24': '12M', '28': '10M', '50': '6M', '144': '2M'}

ADIF_QTH_FIELDS = ('MY_CNTY', 'MY_CITY', 'NOTES')

@QSO_LOG_ROUTES.get('/aiohttp/adif/{callsign}')
async def export_adif_handler(request):
    callsign = extract_callsign(request)
    log = await log_from_db(callsign, limit=False)

    adif = """ADIF Export from TNXLOG
    Logs generated @ """ + time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()) + "\n<EOH>\n"

    def adif_field(name, data):
        data_str = str(data) if data else ''
        return f"<{name.upper()}:{len(data_str)}>{data_str} "

    for qso in log:
        try:
            qso_time = time.gmtime(qso['qso_ts'])
            adif += (
                    adif_field("CALL", qso['cs']) +
                    adif_field("QSO_DATE", time.strftime("%Y%m%d", qso_time)) +
                    adif_field("TIME_OFF", time.strftime("%H%M%S", qso_time)) +
                    adif_field("TIME_ON", time.strftime("%H%M%S", qso_time)) +
                    adif_field("BAND", BANDS_WL[qso['band']]) +
                    adif_field("STATION_CALLSIGN", qso['myCS']) +
                    adif_field("FREQ", str(Decimal(qso['freq'])/1000)) +
                    adif_field("MODE", qso['mode']) +
                    adif_field("RST_RCVD", qso['rcv']) +
                    adif_field("RST_SENT", qso['snt']) +
                    adif_field("MY_GRIDSQUARE", qso['loc']) +
                    adif_field("GRIDSQUARE", qso['loc_rcv'] if 'loc_rcv' in qso else None))
        except Exception:
            logging.exception('Error while adif conversion. QSO:')
            logging.error(qso)

        for field_no, val in enumerate(qso['qth']):
            adif += adif_field(ADIF_QTH_FIELDS[field_no], val)
        adif += "<EOR>\r\n"

    return web.Response(
            headers={
                'Content-Disposition':
                    f'Attachment;filename={callsign + datetime.now().strftime("_%d_%b_%Y")}.adi'
            },
            body=adif.encode()
        )

@QSO_LOG_ROUTES.post('/aiohttp/soundRecord')
@auth(require_email_confirmed=True)
async def sound_record_handler(data, *, callsign, **_):
    station_path = await get_station_path_by_admin_cs(callsign)
    sound_records_path = station_path + '/sound'
    if not os.path.isdir(sound_records_path):
        os.mkdir(sound_records_path)
    file = data['file']['contents']
    file_name = data['file']['name']
    file_path = sound_records_path + '/' + file_name
    with open(file_path, 'wb') as f_sound:
        f_sound.write(file)
    sound_records_data_path = station_path + '/sound.json'
    sound_records_data = loadJSON(sound_records_data_path)
    if not sound_records_data:
        sound_records_data = []
    sound_records_data.append(file_name)
    with open(sound_records_data_path, 'w') as f_sr_data:
        json.dump(sound_records_data, f_sr_data, ensure_ascii = False)
    return web.Response(text='oK')

async def log_from_db(callsign, limit=True):
    log = []
    limit_clause = f" limit{CONF['web'].getint('log_page_length')}" if limit else ''
    data = await DB.execute(
        f"""select id, qso from log
            where callsign = %(cs)s order by id desc
            {limit_clause}
        """, {'cs': callsign})
    if data:
        if isinstance(data, dict):
            log.append(data['qso'])
        else:
            log = [row['qso'] for row in data]
    return log

async def db_insert_qso(callsign, qso):
    insert_success = await DB.execute("""
        insert into log (callsign, qso) 
        values (%(callsign)s, %(qso)s)""",
        {'callsign': callsign, 'qso': json.dumps(qso)})
    if not insert_success:
        qso_in_db = await DB.execute("""
            select qso from log
            where callsign = %(callsign)s and (qso->>'cs') = %(cs)s and 
                (qso->>'qso_ts') = %(qso_ts)s and (qso->>'band') = %(band)s""",
            {'callsign': callsign,
                'qso_ts': str(qso['qso_ts']),
                'cs': qso['cs'],
                'band': qso['band']})
        if qso_in_db:
            return qso_in_db.get('ts')

@QSO_LOG_ROUTES.post('/aiohttp/log')
@auth(require_email_confirmed=True)
async def log_handler(data, *, callsign, **_):
    log = await read_station_file(callsign, 'log.json')
    if log is False:
        log = (await log_from_db(callsign)) or []

    if 'qso' in data:

        rsp = []

        async def process_qso(qso):
            try:
                dtime = datetime.strptime(qso['ts'], "%Y-%m-%d %H:%M:%S")
                qso['date'], qso['time'] = dtFmt(dtime)
                qso['qso_ts'] = (dtime - datetime(1970, 1, 1)) / timedelta(seconds=1)
            except (ValueError, TypeError) as exc:
                logging.error("Error parsing qso timestamp %s", qso['ts'])
                logging.exception(exc)
                return {'ts': None}

            server_ts = qso.pop('serverTs') if 'serverTs' in qso else None

            if server_ts:
                qso['ts'] = server_ts
                qso_idx = [i[0] for i in enumerate(log) if i[1]['ts'] == qso['ts']]
                if qso_idx:
                    log[qso_idx[0]] = qso
                else:
                    log.append(qso)
                db_update = await DB.execute("""
                    update log set qso = %(qso)s
                    where callsign = %(callsign)s and (qso->>'ts')::float = %(ts)s
                    returning (qso->>'ts')::float""",
                    {'callsign': callsign, 'ts': qso['ts'], 'qso': json.dumps(qso)})
                if not db_update:
                    prev_ts = await db_insert_qso(callsign, qso)
                    if prev_ts:
                        qso['ts'] = prev_ts

            else:
                new_qso = True
                if log:
                    for log_qso in log:
                        same_fl = True
                        for key in qso:
                            if key not in ('ts', 'rda', 'wff', 'comments',
                                'serverTs', 'qso_ts', 'qth', 'no', 'sound') and (
                                        key not in log_qso or qso[key] != log_qso[key]):
                                same_fl = False
                                break
                        if same_fl:
                            logging.debug('prev qso found:')
                            new_qso = False
                            qso['ts'] =  log_qso['ts']
                            log_qso['qso_ts'] = qso['qso_ts']

                if new_qso:
                    status_data = await read_station_file(callsign, 'status.json')
                    _ts = dtime.timestamp() + tzOffset()
                    status_update = False
                    if ('freq' not in status_data or status_data['freq']['ts'] < _ts):
                        status_data['freq'] = {'value': qso['freq'], 'ts': _ts}
                        status_update = True
                    if ('callsign' not in status_data or status_data['callsign']['ts'] < _ts):
                        status_data['callsign'] = {'value': qso['myCS'], 'ts': _ts}
                        status_update = True
                    if status_update:
                        await write_station_file(callsign, 'status.json', status_data)

                    qso['ts'] = time.time()
                    while [x for x in log if x['ts'] == qso['ts']]:
                        qso['ts'] += 0.00000001
                    log.insert(0, qso)
                    prev_ts = await db_insert_qso(callsign, qso)
                    if prev_ts:
                        qso['ts'] = prev_ts

            return {'ts': qso['ts']}

        for qso in data['qso']:
            rsp.append((await process_qso(qso)))

        log = sorted(log, key=lambda qso: qso['qso_ts'] if 'qso_ts' in qso else qso['ts']/10,
                reverse=True)
        log = log[:CONF['web'].getint('log_page_length')]
        logging.debug(log)
        await write_station_file(callsign, 'log.json', log)

        return web.json_response(rsp)

    if 'delete' in data:
        log = [x for x in log if x['ts'] != data['delete']]
        await DB.execute("""
            delete from log 
            where callsign = %(callsign)s and (qso->>'ts')::float = %(ts)s""",
            {'callsign': callsign, 'ts': data['delete']})

    if 'clear' in data:
        log = []
        await DB.execute(
            "delete from log where callsign = %(callsign)s",
            {'callsign': callsign})
        #clear sound recordings
        station_path = await get_station_path_by_admin_cs(callsign)
        for file in Path(station_path + "/sound").glob("*"):
            if file.is_file():
                file.unlink()
        await write_station_file(callsign, 'sound.json', [])

    await write_station_file(callsign, 'log.json', log)
    return web.Response(text = 'OK')

@QSO_LOG_ROUTES.post('/aiohttp/logSearch')
async def log_search_handler(request):
    req_data = await request.json()
    if not req_data.get('station'):
        return web.HTTPBadRequest(text='Invalid search params')
    result = []
    cs_filter = "and qso->>'cs' = %(callsign)s" if req_data.get('callsign') else ''
    db_data = await DB.execute(
        f"""select id, qso from log
            where callsign = %(station)s {cs_filter}
            order by id desc""",
            req_data)
    if db_data:
        if isinstance(db_data, dict):
            result.append(db_data['qso'])
        else:
            result = [row['qso'] for row in db_data]
    return web.json_response(result)
