#!/usr/bin/python3
#coding=utf-8

import logging
from collections import defaultdict

from aiohttp import web

#import tnxqso.clusterProtocol
from tnxqso.common import CONF, startLogging
from tnxqso.db import DB

from tnxqso.routes.station_settings import STATION_SETTINGS_ROUTES
from tnxqso.routes.user import USER_ROUTES
from tnxqso.routes.admin import ADMIN_ROUTES
from tnxqso.routes.private_messages import PM_ROUTES
from tnxqso.routes.location import LOCATION_ROUTES
from tnxqso.routes.blog import BLOG_ROUTES
from tnxqso.routes.qso_log import QSO_LOG_ROUTES
from tnxqso.routes.visitors_stats import VISITORS_ROUTES
from tnxqso.routes.active_users import ACTIVE_USERS_ROUTES
from tnxqso.routes.chat import CHAT_ROUTES
from tnxqso.routes.cluster import CLUSTER_ROUTES

startLogging('srv', logging.DEBUG)
logging.debug("server start")

APP = web.Application(client_max_size = 200 * 1024 ** 2)
APP['tnxqso-last-spot-sent'] = defaultdict(int)

def run():
    APP.add_routes(STATION_SETTINGS_ROUTES)
    APP.add_routes(USER_ROUTES)
    APP.add_routes(ADMIN_ROUTES)
    APP.add_routes(PM_ROUTES)
    APP.add_routes(LOCATION_ROUTES)
    APP.add_routes(BLOG_ROUTES)
    APP.add_routes(QSO_LOG_ROUTES)
    APP.add_routes(VISITORS_ROUTES)
    APP.add_routes(ACTIVE_USERS_ROUTES)
    APP.add_routes(CHAT_ROUTES)
    APP.add_routes(CLUSTER_ROUTES)

    DB.verbose = True

    async def on_startup(_):
        await DB.connect()

    APP.on_startup.append(on_startup)

    web.run_app(APP, path = CONF.get('sockets', 'srv'))
