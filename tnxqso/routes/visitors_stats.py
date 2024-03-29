#!/usr/bin/python3
#coding=utf-8
from aiohttp import web

from tnxqso.db import DB
from tnxqso.services.auth import auth, SITE_ADMINS
from tnxqso.services.station_dir import strip_callsign

VISITORS_ROUTES = web.RouteTableDef()

@VISITORS_ROUTES.post('/aiohttp/visitors')
@auth(require_token=False)
async def visitors_handler(data, *, callsign, **_):
    visitor = callsign or data.get('user_id')
    await DB.execute("""
        insert into visitors (station, visitor, tab)
        values (%(station)s, %(visitor)s, %(tab)s)
        on CONFlict on constraint visitors_pkey do
            update set visited = now();
        """,
        {'station': data['station'],
            'visitor': visitor,
            'tab': data['tab']})
    return web.Response(text = 'OK')

@VISITORS_ROUTES.post('/aiohttp/visitors/stats')
@auth()
async def visitors_stats_handler(data, *, callsign, **_):
    if callsign not in SITE_ADMINS:
        user_station_callsign = (await DB.execute("""
        select settings from users
        where callsign = %(callsign)s""",
        {'callsign': callsign}))['settings']['station']['callsign']
        if strip_callsign(user_station_callsign) != data['station']:
            raise web.HTTPForbidden()
    result = {'day': {}, 'week': {}, 'total': {}}
    wheres = {'day': "and visited >= now() - interval '1 day'",
            'week': "and visited >= now() - interval '1 week'",
            'total': ""}
    for period, where in wheres.items():
        db_res = await DB.execute(f"""
            select tab, count(*) as visitors_count
            from visitors
            where station = %(station)s {where}
            group by tab""",
            data, container='list')
        if db_res:
            for row in db_res:
                result[period][row['tab']] = row['visitors_count']
            result[period]['total'] = (await DB.execute(f"""
                select count(distinct visitor) as visitors_count
                from visitors
                where station = %(station)s {where}""",
                {'station': data['station']}))['visitors_count']
    return web.json_response(result)
