#!/usr/bin/python3
#coding=utf-8
import argparse, logging, logging.handlers, os, json, pathlib, re
from common import siteConf, loadJSON, appRoot, startLogging

parser = argparse.ArgumentParser(description="tnxqso adxcluster filter")
startLogging( 'clusterFilter' )

conf = siteConf()
roots = [ conf.get( 'web', x ) for x in [ 'root', 'root_test' ] ]
dx = loadJSON( conf.get( 'files', 'adxcluster' ) )
if not dx:
    logging.error( 'no dx data' )
    sys.exit()

for root in roots:
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
            stationDX = loadJSON( stationDXpath )
            if not stationDX:
                stationDX = []
            prev = stationDX[0] if stationDX else None
            idx = 0
            for item in reversed( dx ):
                if prev and item['ts'] <= prev['ts']:
                    break
                for r in reCS:
                    if r.match( item['cs'] ):
                        _item = dict(item)
                        for rh in reHl:
                            if rh.match( _item['cs'] ):
                                _item['highlight'] = True
                                break
                        stationDX.insert( idx, _item )
                        idx += 1
                        break
            if len( stationDX ) > 20:
                stationDX = stationDX[:20]
            with open( stationDXpath, 'w' ) as f:
                json.dump( stationDX, f, ensure_ascii = False )


