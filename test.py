#!/usr/bin/python3
#coding=utf-8

import requests, json, argparse

#rsp = requests.post('https://test.tnxqso.com/aiohttp/login',\
#        data=json.dumps({'login': 'qqqq', 'password': '11111111'}))
#rsp.raise_for_status()
#print(rsp.text)
#token = json.loads(rsp.text)['token']
#rsp = requests.post('https://test.tnxqso.com/aiohttp/location',\
#        data=json.dumps({'location': [45.0678148, 38.9859166]}))
#print(rsp.text)
parser = argparse.ArgumentParser()
parser.add_argument('-t', action="store_true")
args = parser.parse_args()
pfx = 'test.' if args.t else ''

print("No login tests")
print("Test IT")
rsp = requests.post('https://' + pfx + 'tnxqso.com/aiohttp/location',\
        data=json.dumps({'location': [41.90, 12.50]}))
print(rsp.text)
print("Test RU")
rsp = requests.post('https://' + pfx + 'tnxqso.com/aiohttp/location',\
        data=json.dumps({'location': [45.0832, 38.9690]}))
print(rsp.text)
print("Test RU multiple RDAs")
rsp = requests.post('https://' + pfx + 'tnxqso.com/aiohttp/location',\
        data=json.dumps({'location': [45.0674, 38.6534]}))
print(rsp.text)
print("Test RU country detect glitch")
rsp = requests.post('https://' + pfx + 'tnxqso.com/aiohttp/location',\
        data=json.dumps({'location': [60.7117895, 28.7183622]}))
print(rsp.text)

print("Test UK")
rsp = requests.post('https://' + pfx + 'tnxqso.com/aiohttp/location',\
        data=json.dumps({'location': [51.7674, -1.2635]}))
print(rsp.text)

print("Login tests")
rsp = requests.post('https://' + pfx + 'tnxqso.com/aiohttp/login',\
        data=json.dumps({'login': 'ADM111N', 'password': '11111111'}))
rsp.raise_for_status()
print(rsp.text)
token = json.loads(rsp.text)['token']
print("Test RU multiple RDAs")
rsp = requests.post('https://' + pfx + 'tnxqso.com/aiohttp/location',\
        data=json.dumps({'token': token, 'location': [45.0674, 38.6534]}))
print(rsp.text)

