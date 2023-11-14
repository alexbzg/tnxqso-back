#!/usr/bin/python3
#coding=utf-8
import subprocess
import requests

from tnxqso.common import appRoot

def main():
    rafa_rsp = requests.get('https://hamlog.online/api/v1/rafa/rafalist/')
    rafa_rsp.raise_for_status()
    rafa_json = rafa_rsp.json()
    with open(f'{appRoot}/rafa.csv', 'w') as f_out:
        for val in rafa_json:
            f_out.write(f";{val};;{rafa_json[val]['loc'].replace(' ', '')}\n")
    subprocess.call('systemctl restart tnxqso', shell=True)
