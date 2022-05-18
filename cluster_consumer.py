#!/usr/bin/python3
#coding=utf-8

from pymongo import MongoClient

def get_data(consumer_id, db_params, all_data=False):
    MC = MongoClient(db_params['host'],
        username=db_params['user'],
        password=db_params['password'],
        authSource='admin')

    DB = MC.dx
    ts_filter = {}
    if not all_data:
        ts_prev = DB.consumers.find_one({'id': consumer_id})['last']
        if ts_prev:
            ts_filter['ts'] = {'$gt': ts_prev}

    data = DB.dx.find(ts_filter).sort('ts', -1)
    if data:
        DB.consumers.update_one({'id': consumer_id}, {'$set': {'last': data[0]['ts']}})

    return data
