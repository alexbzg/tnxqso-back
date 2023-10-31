#!/usr/bin/python3
#coding=utf-8

import time
import asyncio
import logging

from aiohttp import web

from tnxqso.common import CONF
from tnxqso.services.auth import auth

CLUSTER_ROUTES = web.RouteTableDef()
RETRIES_LIMIT = 3
CONNECT_TIMEOUT = 0.5
LOGIN_TIMEOUT = 2
CLOSE_DELAY = 1

@CLUSTER_ROUTES.post('/aiohttp/sendSpot')
@auth(require_token=False)
async def send_spot_handler(data, *, request, **_):
    now = time.time()
    response = {'sent': False,
            'secondsLeft': CONF.getint('cluster', 'spotInterval')}
    if now - request.app['tnxqso-last-spot-sent'][data['cs']] > response['secondsLeft']:
        request.app['tnxqso-last-spot-sent'][data['cs']] = now
        retries = 0
        last_reply = ''
        while retries < RETRIES_LIMIT:
            try:
                reader, writer = await asyncio.wait_for(asyncio.open_connection(
                        CONF.get('cluster', 'host'),
                        CONF.get('cluster', 'port')),
                        timeout=CONNECT_TIMEOUT)

                def write(cmd):
                    writer.write(f"{cmd}\n".encode())

                async def login():
                    nonlocal last_reply
                    logged_in = False
                    while not logged_in:
                        cluster_reply = (await reader.readline()).decode().strip()
                        if cluster_reply:
                            last_reply = cluster_reply
                            if 'your call:' in cluster_reply:
                                write(data['userCS'])
                            elif 'Please enter' in cluster_reply:
                                write('-')
                            elif '(Y or N)' in cluster_reply:
                                write('Y')
                            elif '>' in cluster_reply:
                                logged_in = True

                await asyncio.wait_for(login(), timeout=LOGIN_TIMEOUT)

                write(f"dx {data['cs']} {data['freq']} {data['info']}")
                response['sent'] = True
                await asyncio.sleep(CLOSE_DELAY)
                request.app['tnxqso-last-spot-sent'][data['cs']] = time.time()
                break
            except (OSError, asyncio.TimeoutError) as exc:
                logging.error('Cluster connect error %s', exc)
                retries += 1
            finally:
                if writer:
                    writer.close()
        if retries == RETRIES_LIMIT:
            logging.error('Connection retries limit reached')
            if last_reply:
                response['reply'] = last_reply
    else:
        response['secondsLeft'] -= time.time() - request.app['tnxqso-last-spot-sent'][data['cs']]
    return web.json_response(response)
