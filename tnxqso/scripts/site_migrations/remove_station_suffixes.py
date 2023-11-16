#!/usr/bin/python3
#coding=utf-8
import pathlib
import os

from tnxqso.common import WEB_ROOT

def main():

    stations_path = WEB_ROOT + '/stations'

    for station in [x.parts[-1] for x in pathlib.Path(stations_path).iterdir()
            if x.is_dir() and '-' in x.parts[-1]]:
        new_station_path = f"{stations_path}/{station.split('-')[0]}"
        if os.path.isdir(new_station_path):
            print(new_station_path)
        else:
            os.rename(f"{stations_path}/{station}", new_station_path)

if __name__ == '__main__':
    main()
