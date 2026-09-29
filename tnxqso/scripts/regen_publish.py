#!/usr/bin/python3
#coding=utf-8
import pathlib
import os
import json

from tnxqso.common import CONF, WEB_ROOT, loadJSON

def main():

    stations_path = WEB_ROOT + '/stations'
    publish = {}

    for station_path in [str(x) for x in pathlib.Path(stations_path).iterdir() if x.is_dir()]:
        settings = loadJSON(station_path + '/settings.json')
        if settings and 'station' in settings and (callsign := settings['station'].get('callsign', None)):
            publish[callsign] = {'admin': True, 'user': settings.get('publish', False)}

    with open(f'{WEB_ROOT}/js/publish.json', 'w') as f_publish:
        json.dump(publish, f_publish, ensure_ascii = False)

