from aiohttp import web

from tnxqso.services.auth import auth
from tnxqso.db import DB

STATION_USER_BAN_ROUTES = web.RouteTableDef()

@STATION_USER_BAN_ROUTES.post('/aiohttp/stationUserBan')
@auth()
async def station_user_post_delete_handler(data, *, callsign, _):
    await DB.execute("""
        insert into user_bans (admin_callsign, banned_callsign)
        values (%(admin)s, %(banned)s
        """, {'admin': callsign, 'banned': data['banned']})
    return web.Response(text = 'OK')

@STATION_USER_BAN_ROUTES.delete('/aiohttp/stationUserBan')
@auth()
async def station_user_ban_delete_handler(data, *, callsign, _):
    await DB.execute("""
        delete from user_bans
        where admin_callsign = %(admin)s and  banned_callsign = %(banned)s
        """, {'admin': callsign, 'banned': data['banned']})
    return web.Response(text = 'OK')
