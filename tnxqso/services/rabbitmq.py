#!/usr/bin/python3
import logging
import asyncio

import aio_pika
import aiormq
from bson.json_util import dumps

from tnxqso.common import CONF, json_dumps

async def rabbitmq_connect(app):
    try:
        connection = await aio_pika.connect_robust(
            host=CONF['rabbitmq']['host'],
            login=CONF['rabbitmq']['user'],
            password=CONF['rabbitmq']['password'],
            virtualhost=CONF['rabbitmq']['virtual_host']
            )


        app['rabbitmq'] = {
            'connection': connection,
            'exchanges': {}
        }
        channel = await connection.channel()
        app['rabbitmq']['exchanges']['chats'] = await channel.declare_exchange(
            name='chat',
            type=aio_pika.ExchangeType.DIRECT,
            durable=True
            )
        app['rabbitmq']['exchanges']['pm'] = await channel.declare_exchange(
            name='pm',
            type=aio_pika.ExchangeType.DIRECT,
            durable=True
            )

    except aiormq.exceptions.AMQPConnectionError:
        logging.exception("Rabbitmq connection error. Retry in 10 seconds")
        await asyncio.sleep(10)
        await rabbitmq_connect(app)
        return


async def rabbitmq_publish(exchange, key, message):
    try:
        await exchange.publish(
            aio_pika.Message(
                body=bytes(json_dumps(message), 'utf-8'),
                ),
            routing_key=key
            )
    except aiormq.exceptions.AMQPConnectionError:
        logging.exception("Rabbitmq publish error")
        logging.error("Exchange: %s, routing_key: %s, message: %s",
                exchange, key, message)

async def rabbitmq_disconnect(app):
    if app.get('rabbitmq'):
        await app['rabbitmq']['connection'].close()
