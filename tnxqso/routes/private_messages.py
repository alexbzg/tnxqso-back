#!/usr/bin/python3
#coding=utf-8

import pika

from aiohttp import web

from tnxqso.common import CONF, json_dumps, web_json_response
from tnxqso.db import DB
from tnxqso.services.auth import auth
from tnxqso.services.rabbitmq import rabbitmq_publish

PM_ROUTES = web.RouteTableDef()

@PM_ROUTES.post('/aiohttp/privateMessages/post')
@auth(require_email_confirmed=True)
async def private_messages_post_handler(data, *, request, **_):
    receiver = await DB.get_user_data(data['callsign_to'])
    if not receiver or not receiver['pm_enabled']:
        raise web.HTTPBadRequest(
            text='The recipient does not exist or is not accepting private messages.')
    msg = await DB.get_object('private_messages',
            {'callsign_from': data['callsign_from'],
            'callsign_to': data['callsign_to'],
            'txt': data['txt']}, create=True)
    sender = await DB.get_user_data(data['callsign_from'])
    msg['chat_callsign_from'], msg['name_from'] = sender['chat_callsign'], sender['name']

    if request.app.get('rabbitmq') and request.app['rabbitmq']['exchanges'].get('pm'):
        await rabbitmq_publish(request.app['rabbitmq']['exchanges']['pm'],
                key=data['callsign_to'], message=msg)

    return web.Response(text='OK')

"""
    rabbitmq_connection = pika.BlockingConnection(pika.ConnectionParameters(
        host=CONF['rabbitmq']['host'],
        virtual_host=CONF['rabbitmq']['virtual_host'],
        credentials=pika.PlainCredentials(CONF['rabbitmq']['user'],
            CONF['rabbitmq']['password'])))
    rabbitmq_channel = rabbitmq_connection.channel()
    rabbitmq_channel.exchange_declare(exchange='pm', exchange_type='direct', durable=True)
    rabbitmq_channel.confirm_delivery()
    rabbitmq_channel.basic_publish(exchange='pm', routing_key=data['callsign_to'],
            body=json_dumps(msg))
    rabbitmq_connection.close()
"""

@PM_ROUTES.post('/aiohttp/privateMessages/get')
@auth()
async def private_messages_get_handler(_data, *, callsign, **_):
    messages = []
    data = await DB.execute(
        """select id, callsign_from, callsign_to, tstamp, txt, unread,
                chat_callsign as chat_callsign_from, name as name_from
            from private_messages join users on callsign_from = users.callsign
            where callsign_to = %(cs)s
            order by id desc""",
            {'cs': callsign})
    if data:
        if isinstance(data, dict):
            messages.append(data)
        else:
            messages = data
    return web_json_response(messages)


@PM_ROUTES.post('/aiohttp/privateMessages/delete')
@auth(require_email_confirmed=True)
async def private_messages_delete_handler(data, *, callsign, **_):
    if data.get('all'):
        await DB.execute(
            """delete
                from private_messages
                where callsign_to = %(cs)s""",
                {'cs': callsign})
    else:
        await DB.execute(
            """delete
                from private_messages
                where callsign_to = %(cs)s and id = %(id)s""",
                {'cs': callsign, 'id': data['id']})
    return web.json_response(text='OK')

@PM_ROUTES.post('/aiohttp/privateMessages/read')
@auth()
async def private_messages_read_handler(data, *, callsign, **_):
    await DB.execute(
        """update private_messages
            set unread = false
            where callsign_to = %(cs)s and id in %(ids)s""",
            {'cs': callsign, 'ids': tuple(data['ids'])})
    return web.json_response(text='OK')
