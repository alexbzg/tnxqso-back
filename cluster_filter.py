#!/usr/bin/python3
#coding=utf-8
import json
import pathlib
import re
import argparse

from common import siteConf, loadJSON, startLogging
import cluster_consumer

argparser = argparse.ArgumentParser(usage="-c clears previous data -a consumes all data")
argparser.add_argument('-c', action='store_true')
argparser.add_argument('-a', action='store_true')
args = argparser.parse_args()

startLogging('clusterFilter')

conf = siteConf()
root = conf.get('web', 'root')

dx_data = cluster_consumer.get_data('tnxqso', dict(conf['cluster_db']), all_data=args.a)

stationsPath = root + '/stations'

for station in [str(x) for x in pathlib.Path( stationsPath ).iterdir() \
        if x.is_dir() ]:
    settings = loadJSON( station + '/settings.json' )
    if settings and 'clusterCallsigns' in settings and \
            settings['enable']['cluster'] and settings['clusterCallsigns']:
        reCS = []
        for cs in settings['clusterCallsigns']:
            reCS.append( re.compile( '^' + cs.replace( '*', '.*' ) + '$' ) )
        reHl = []
        if 'clusterHighlight' in settings and settings['clusterHighlight']:
            for cs in settings['clusterHighlight']:
                reHl.append( re.compile( '^' + cs.replace( '*', '.*' ) + '$' ) )
        stationDXpath = station + '/cluster.json'
        stationDX = []
        if not args.c:
            stationDX = loadJSON(stationDXpath)
            if not stationDX:
                stationDX = []
        idx = 0

        for item in dx_data:
            for r in reCS:
                if r.match(item['cs']):
                    item_c = item.copy()
                    del item_c['_id']
                    for rh in reHl:
                        if rh.match(item_c['cs']):
                            item_c['highlight'] = True
                            break
                    
                    stationDX = [x for x in stationDX if x['ts'] >=  item_c['ts'] - 5400
                        or x['cs'] != item_c['cs'] or not -1 < x['freq'] - item_c['freq'] < 1]

                    stationDX.insert(idx, item_c)
                    
                    idx += 1
                    break
        if idx > 0:
            if len( stationDX ) > 20:
                stationDX = stationDX[:20]
            with open( stationDXpath, 'w' ) as f:
                json.dump( stationDX, f, ensure_ascii = False )
