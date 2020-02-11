#!/usr/bin/python3
#coding=utf-8
import subprocess
import requests
from common import loadJSON

rafa = {}

rafa_rsp = requests.get('https://api.hamlog.ru/rafa/rafalist.php')
rafa_rsp.raise_for_status()
rafa_json = rafa_rsp.json()
with open('/usr/local/tnxqso/rafa.csv', 'w') as f_out:
    for val in rafa_json:
        f_out.write(';' + val + ';;' + rafa_json[val]['loc'].replace(' ', '') + '\n')
subprocess.call('systemctl restart tnxqso', shell=True)
