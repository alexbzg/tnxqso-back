#!/usr/bin/python3
#coding=utf-8

import time
import asyncio
import logging

from aiohttp import web

from tnxqso.common import CONF
from tnxqso.services.auth import auth

CLUSTER_ROUTES = web.RouteTableDef()

@CLUSTER_ROUTES.post('/aiohttp/sendSpot')
@auth(require_token=False)
async def send_spot_handler(data, *, request, **_):
    now = time.time()
    response = {'sent': False,
            'secondsLeft': CONF.getint('cluster', 'spotInterval')}
    if now - request.app['tnxqso-last-spot-sent'][data['cs']] > response['secondsLeft']:
        retries = 0
        while retries < 3:
            try:
                fut_connection = asyncio.open_connection(
                        CONF.get('cluster', 'host'),
                        CONF.get('cluster', 'port'))
                reader, writer = await asyncio.wait_for(fut_connection,
                        timeout=0.5)

                def write(cmd):
                    writer.write(f"{cmd}\n".encode())

                async def login():
                    logged_in = False
                    while not logged_in:
                        cluster_rsp = (await reader.readline()).decode().strip()
                        if 'your call:' in cluster_rsp:
                            write(data['userCS'])
                        elif 'Please enter' in cluster_rsp:
                            write('-')
                        elif '(Y or N)' in cluster_rsp:
                            write('Y')
                        elif '>' in cluster_rsp:
                            logged_in = True

                await asyncio.wait_for(login(), timeout=2)

                write(f"dx {data['cs']} {data['freq']} {data['info']}")
                request.app['tnxqso-last-spot-sent'][data['cs']] = now
                response['sent'] = True
                await asyncio.sleep(0.5)
                break
            except (OSError, asyncio.TimeoutError) as exc:
                logging.error('Cluster connect error %s', exc)
                retries += 1
            finally:
                if writer:
                    writer.close()
        if retries == 3:
            logging.error('Connection retries limit reached')
    else:
        response['secondsLeft'] -= now - request.app['tnxqso-last-spot-sent'][data['cs']]
    return web.json_response(response)
