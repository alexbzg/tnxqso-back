#!/usr/bin/python3
#coding=utf-8
import pika

from tnxqso.common import CONF

def main():
    rabbitmq_connection = pika.BlockingConnection(pika.ConnectionParameters(
        host=CONF['rabbitmq']['host'],
        virtual_host=CONF['rabbitmq']['virtual_host'],
        credentials=pika.PlainCredentials(CONF['rabbitmq']['user'],
            CONF['rabbitmq']['password'])))
    rabbitmq_channel = rabbitmq_connection.channel()
    for exchange_name in ['pm', 'chat']:
        rabbitmq_channel.exchange_delete(exchange_name)
    rabbitmq_connection.close()

if __name__ == "__main__":
    main()
